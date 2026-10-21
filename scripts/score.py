#!/usr/bin/env python3
"""試題凍結、派發評分小組、計分。

在有 input/ 的那一層執行：
    python3 SKILL/scripts/score.py freeze                  # 檢查 eval/items.json 的藍圖並鎖定
    python3 SKILL/scripts/score.py panel [--students 3 --raters 3]
                                                           # 為最新一次 observe 產生每位 AI 學生／評分者的指令檔
    python3 SKILL/scripts/score.py score [--label 名稱]    # 收齊結果後計分 → report.json、report.md、feedback.md
    python3 SKILL/scripts/score.py score --no-video        # 沒有影片：直接記 0 分
"""
import argparse, datetime, glob, hashlib, json, pathlib, random, re, statistics as st, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import INSTRUMENT, PROMPTS, ROOT, die, dump_json, fuzzy_contains, load_json, norm_zh, parse_stamp, stamp
from psychometrics import hake_gain, mean_ci, rwg_j, t95

ROLES = {"target": 2, "diagnostic": 2, "near": 1, "far": 1, "control": 1}
TYPES = {"mcq", "tf", "numeric", "short"}


# ───────────────────────── 共用 ─────────────────────────
def paths(a):
    ev = pathlib.Path(a.eval)
    return ev, ev / "items.json", ev / "items.lock", ev / "items_public.json"


def latest_run(ev, rid=None):
    rid = rid or ((ev / "runs" / "LATEST").read_text().strip() if (ev / "runs" / "LATEST").exists() else None)
    if not rid or not (ev / "runs" / rid / "observe" / "meta.json").exists():
        die("找不到 observe 的結果。先執行 observe.py。")
    return rid, ev / "runs" / rid


def sha_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


# ───────────────────────── freeze ─────────────────────────
def check_items(items, case):
    errs, warns = [], []
    if items.get("case_id") != case.get("case_id"):
        errs.append(f"case_id 是 {items.get('case_id')}，但 input/case_input.json 是 {case.get('case_id')}")
    objs = {o.get("id") for o in items.get("objectives", [])}
    objs.discard("control")
    if not 2 <= len(objs) <= 4:
        warns.append(f"學習目標有 {len(objs)} 個（建議 2–4 個）")
    qs = items.get("items", [])
    if not 6 <= len(qs) <= 12:
        errs.append(f"題數 {len(qs)}，要 6–12 題")
    ids = [q.get("id") for q in qs]
    if len(set(ids)) != len(ids):
        errs.append("題號重複")
    for role, need in ROLES.items():
        n = sum(q.get("role") == role for q in qs)
        if n < need:
            errs.append(f"role={role} 只有 {n} 題，至少要 {need} 題")
    keys = []
    for q in qs:
        tag = q.get("id", "?")
        if q.get("role") not in ROLES:
            errs.append(f"{tag}：role 不對（{q.get('role')}）")
        if q.get("type") not in TYPES:
            errs.append(f"{tag}：type 不對（{q.get('type')}）")
        if q.get("role") == "control" and q.get("objective") not in ("control", None):
            warns.append(f"{tag}：控制題的 objective 請填 \"control\"")
        if q.get("objective") not in objs and q.get("role") != "control":
            errs.append(f"{tag}：objective {q.get('objective')} 不在 objectives 裡")
        if not str(q.get("stem", "")).strip():
            errs.append(f"{tag}：沒有題幹")
        if not str(q.get("rationale", "")).strip():
            warns.append(f"{tag}：沒有 rationale")
        t = q.get("type")
        if t == "mcq":
            opts = q.get("options") or {}
            if not 3 <= len(opts) <= 5:
                errs.append(f"{tag}：選項要 3–5 個")
            if q.get("key") not in opts:
                errs.append(f"{tag}：key 不在選項裡")
            keys.append(q.get("key"))
            if q.get("role") == "diagnostic":
                mo = q.get("misconception_option")
                if mo not in opts or mo == q.get("key"):
                    errs.append(f"{tag}：diagnostic 選擇題要有 misconception_option，而且不能是正解")
        elif t == "tf" and not isinstance(q.get("key"), bool):
            errs.append(f"{tag}：是非題的 key 要是 true／false")
        elif t == "numeric" and not isinstance(q.get("key"), (int, float)):
            errs.append(f"{tag}：數字題的 key 要是數字")
        elif t == "short" and not q.get("accept"):
            errs.append(f"{tag}：簡答題要有 accept 清單")
    if len(keys) >= 4 and max(keys.count(k) for k in set(keys)) / len(keys) > 0.6:
        warns.append("選擇題的正解集中在同一個選項，請打散")
    return errs, warns


def cmd_freeze(a):
    ev, fi, fl, fp = paths(a)
    if not fi.exists():
        die(f"找不到 {fi}。先照 instrument/test_spec.md 出題。")
    items, case = load_json(fi), load_json(a.case)
    errs, warns = check_items(items, case)
    for w in warns:
        print(f"  ⚠ {w}")
    if errs:
        die("試題不符合藍圖：\n  " + "\n  ".join(errs))
    h = sha_file(fi)
    if fl.exists():
        old = load_json(fl)
        if old["sha256"] != h and not a.new_form:
            die("試題已經鎖定過，而且內容不同。同一題的不同版本影片要用同一份試題才能比較。\n"
                "  真的要換一份（平行複本）：加 --new-form，舊的會留在 eval/forms/。")
        form = old["form"] + (1 if old["sha256"] != h else 0)
    else:
        form = 1
    pub = {"case_id": items["case_id"], "items": [{k: v for k, v in q.items() if k in ("id", "type", "stem", "options", "unit")} for q in items["items"]]}
    dump_json(pub, fp)
    dump_json(dict(sha256=h, form=form, n_items=len(items["items"]), frozen_at=datetime.datetime.now().isoformat(timespec="seconds")), fl)
    (ev / "forms").mkdir(exist_ok=True)                      # 每一份複本都留底，換新複本後舊的還找得到
    (ev / "forms" / f"items_form{form}.json").write_text(fi.read_text(encoding="utf-8"), encoding="utf-8")
    roles = {r: sum(q["role"] == r for q in items["items"]) for r in ROLES}
    print(f"✓ 試題已鎖定（複本 {form}，{len(items['items'])} 題：" + "、".join(f"{r} {n}" for r, n in roles.items()) + f"）\n  學生看得到的版本：{fp}")
    print(f"  下一步：python3 {ROOT}/scripts/observe.py")


# ───────────────────────── panel ─────────────────────────
def fill(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace(f"<<{k}>>", str(v))
    return tpl


def prefix_block(obs, who):
    pdir = obs / "prefix"
    if not pdir.exists():
        return "（沒有原片片段可看，只能依上面的描述。）"
    head = "## 你已經看過的原片\n\n這些是你在提問前看過的內容，打開看，讓自己「進入狀況」：" if who == "student" else "## 原片（對照「延續原片教法」用）\n\n打開看："
    return (f"{head}\n\n- `{pdir}/overview.jpg`：原片從頭到提問時間平均截 12 格。\n"
            f"- `{pdir}/end_1.jpg`–`end_4.jpg`：提問前 20、10、4、0.5 秒的畫面。\n"
            f"- `{pdir}/transcript.txt`：原片逐字稿（可能只有最後一段）。")


def cmd_panel(a):
    ev, fi, fl, fp = paths(a)
    if not fl.exists():
        die("試題還沒鎖定，先執行 score.py freeze。")
    rid, rdir = latest_run(ev, a.run)
    obs, pdir, out = rdir / "observe", rdir / "panel", rdir / "panel" / "out"
    out.mkdir(parents=True, exist_ok=True)
    case, pub, rubric = load_json(a.case), load_json(fp), load_json(INSTRUMENT / "rubric.json")
    lp = case.get("learner_profile", {})
    common = dict(GRADE=case.get("grade", ""), LEVEL=case.get("level", ""), SUBJECT=case.get("subject", ""), TITLE=case.get("title") or f"{case.get('case_id', '')}（題目沒附課名）",
                  KNOWN="、".join(lp.get("known_concepts", [])) or "（沒寫）", MISCONCEPTION=lp.get("misconception", "（沒寫）"),
                  T=case.get("timestamp_seconds", "?"), QUESTION=case.get("question", ""), CONTEXT=case.get("context_summary", ""),
                  STYLE=case.get("style") or "題目沒提供，請看原片判斷", OBS=obs.resolve())
    ids = [q["id"] for q in pub["items"]]
    files = []
    for i in range(1, a.students + 1):
        order = ids[:]
        random.Random(1000 + i).shuffle(order)
        p = pdir / f"student_{i}.md"
        p.write_text(fill((PROMPTS / "student.md").read_text(encoding="utf-8"), N=i, ITEMS=fp.resolve(), ORDER="、".join(order),
                          OUT=(out / f"student_{i}.json").resolve(), PREFIX_BLOCK=prefix_block(obs.resolve(), "student"), **common), encoding="utf-8")
        files.append(p)
    cids = [c["id"] for c in rubric["constructs"]]
    for i in range(1, a.raters + 1):
        k = (i - 1) % len(cids)
        order = cids[k:] + cids[:k]
        p = pdir / f"rater_{i}.md"
        p.write_text(fill((PROMPTS / "rater.md").read_text(encoding="utf-8"), N=i, RUBRIC=(INSTRUMENT / "rubric.json").resolve(),
                          ORDER=" → ".join(order), OUT=(out / f"rater_{i}.json").resolve(),
                          PREFIX_BLOCK=prefix_block(obs.resolve(), "rater"), **common), encoding="utf-8")
        files.append(p)
    print(f"✓ 評分小組指令檔（run {rid}）：")
    for p in files:
        print(f"  {p}")
    print("\n每一份都要交給**獨立的**執行者（彼此看不到對方、也沒看過試題答案與老師腳本）：")
    print("  · 能開 subagent（Claude Code 的 Agent／Task 工具等）：每份開一個，指令就是「完整照著 <檔案路徑> 做」。可以同時跑。")
    print("  · 不能開 subagent：每份各開一個新的 headless 行程，例如")
    print(f"      claude -p \"完整照著 {files[0]} 做\"      或      codex exec \"完整照著 {files[0]} 做\"")
    print(f"\n全部完成後：python3 {ROOT}/scripts/score.py score")


# ───────────────────────── score：學生 ─────────────────────────
def transcript_segments(obs):
    segs = []
    for line in (obs / "transcript.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[(\d+:[\d.]+)–(\d+:[\d.]+)\]\s*(.*)", line)
        if m:
            segs.append((parse_stamp(m[1]), parse_stamp(m[2]), m[3]))
    return segs


def is_correct(q, ans):
    if ans is None:
        return False
    t = q["type"]
    if t == "mcq":
        m = re.match(r"\s*\(?([A-Ea-e])", str(ans))
        return bool(m) and m[1].upper() == str(q["key"]).upper()
    if t == "tf":
        s = str(ans).strip().lower()
        v = True if s in ("true", "t", "o", "對", "是", "正確", "yes", "1") else False if s in ("false", "f", "x", "錯", "否", "不對", "錯誤", "no", "0") else None
        return v is not None and v == q["key"]
    if t == "numeric":
        try:
            return abs(float(re.sub(r"[^\d.\-]", "", str(ans))) - float(q["key"])) <= float(q.get("tolerance", 0)) + 1e-9
        except ValueError:
            return False
    if t == "short":
        a_ = norm_zh(str(ans))
        return any(norm_zh(x) and (norm_zh(x) == a_ or norm_zh(x) in a_) for x in q["accept"])
    return False


def same_answer(q, a1, a2):
    if a1 is None or a2 is None:
        return False
    if q["type"] == "mcq":
        f = lambda x: (re.match(r"\s*\(?([A-Ea-e])", str(x)) or [None, str(x)])[1].upper()
        return f(a1) == f(a2)
    return norm_zh(str(a1)) == norm_zh(str(a2)) or str(a1).strip().lower() == str(a2).strip().lower()


def grounded(evidence, segs, cfg):
    for e in evidence or []:
        quote, t = str(e.get("quote", "")), parse_stamp(e.get("t"))
        if len(norm_zh(quote)) < 4:
            continue
        w = cfg["window_sec"]
        near = [s for s in segs if t is None or (s[1] >= t - w and s[0] <= t + w)]
        if fuzzy_contains(quote, " ".join(x[2] for x in near)) >= cfg["min_quote_match"]:
            return True
    return False


def score_students(items, students, segs, cfg):
    qs = {q["id"]: q for q in items["items"]}
    rw, cr, hi = cfg["role_weights"], cfg["credit"], cfg["high_confidence"]
    per, warns = [], []
    for s in students:
        pre = {r["id"]: r for r in s.get("pre", [])}
        post = {r["id"]: r for r in s.get("post", [])}
        miss = [i for i in qs if i not in pre or i not in post]
        if miss:
            warns.append(f"學生 {s.get('student')} 少答 {', '.join(miss)}（當作答錯）")
        rows = []
        for qid, q in qs.items():
            a0, a1 = pre.get(qid, {}), post.get(qid, {})
            c0, c1 = is_correct(q, a0.get("answer")), is_correct(q, a1.get("answer"))
            changed = not same_answer(q, a0.get("answer"), a1.get("answer"))
            g = grounded(a1.get("evidence"), segs, cfg["grounding"])
            credit = cr["wrong"] if not c1 else cr["correct_grounded"] if (g or (c0 and not changed)) else cr["correct_ungrounded"]
            held = (q["type"] == "mcq" and q.get("misconception_option") and str(a0.get("answer", "")).strip().upper().startswith(q["misconception_option"])) or \
                   (q["role"] == "diagnostic" and q["type"] != "mcq" and not c0)
            rows.append(dict(id=qid, role=q["role"], objective=q.get("objective"), pre=int(c0), post=int(c1), credit=credit, grounded=g, changed=changed,
                             conf0=a0.get("confidence"), conf1=a1.get("confidence"), held_misconception=bool(held),
                             strong_wrong_post=(not c1) and (a1.get("confidence") or 0) >= hi))
        scored = [r for r in rows if r["role"] != "control"]
        wsum = sum(rw[r["role"]] for r in scored)
        L = 100 * sum(rw[r["role"]] * r["credit"] for r in scored) / wsum if wsum else None
        pre_acc = st.fmean(r["pre"] for r in scored) if scored else None
        post_acc = st.fmean(r["credit"] for r in scored) if scored else None
        per.append(dict(student=s.get("student"), L=L, pre=pre_acc, post=post_acc, gain=hake_gain(pre_acc, post_acc), rows=rows,
                        experience=s.get("experience", {}), explain=s.get("explain", ""), still_confused=s.get("still_confused", "")))
    return per, warns


# ───────────────────────── score：評分者 ─────────────────────────
def clip_ci(ci):
    return None if not ci else (max(0.0, ci[0]), min(100.0, ci[1]))


def to100(x):
    return None if x is None else (x - 1) / 4 * 100


def clamp01(x):
    return max(0.0, min(1.0, x))


def band(v, ok, zero):
    """v 在 ok 區間 → 1；往外線性降到 zero 端點 → 0。"""
    if v is None:
        return None
    lo, hi = ok
    if lo <= v <= hi:
        return 1.0
    if v < lo:
        return clamp01((v - zero[0]) / (lo - zero[0]))
    return clamp01((zero[1] - v) / (zero[1] - hi))


def objective_scores(meta, cfg):
    o = cfg["objective"]
    out = {}
    out["duration"] = band(meta.get("duration"), o["duration"]["ok"], o["duration"]["zero_at"])
    L = meta.get("lufs")
    if L is not None:
        lo, hi = o["loudness_lufs"]["ok"]
        out["loudness"] = 1.0 if lo <= L <= hi else clamp01(1 - o["loudness_lufs"]["minus_per_lu"] * min(abs(L - lo), abs(L - hi)) / 100)
    sr = meta.get("speech_ratio")
    if sr is not None:
        out["speech_ratio"] = clamp01((sr - o["speech_ratio"]["zero_at"]) / (o["speech_ratio"]["full_at"] - o["speech_ratio"]["zero_at"]))
    it = o["intelligibility"]
    if meta.get("script_cer") is not None:
        out["intelligibility"] = clamp01(1 - meta["script_cer"] / it["script_cer_zero_at"])
    elif (meta.get("asr") or {}).get("confidence") is not None:
        out["intelligibility"] = clamp01((meta["asr"]["confidence"] - it["asr_conf_zero_at"]) / (it["asr_conf_full_at"] - it["asr_conf_zero_at"]))
    pr, vr = (meta.get("prefix") or {}).get("rate_cps"), (meta.get("asr") or {}).get("rate_cps")
    pace = band(vr / pr, o["pace_ratio_vs_prefix"]["ok"], o["pace_ratio_vs_prefix"]["zero_at"]) if pr and vr else None
    c4 = [v for k, v in out.items() if v is not None]
    return {k: (None if v is None else round(100 * v, 1)) for k, v in out.items()}, (100 * st.fmean(c4) if c4 else None), (None if pace is None else 100 * pace)


def clean_rater(r, rubric, warns):
    items = {it["id"]: it for c in rubric["constructs"] for it in c["items"]}
    got = {x.get("id"): x for x in r.get("ratings", [])}
    out = {}
    for iid, it in items.items():
        x = got.get(iid)
        s = None if x is None else x.get("score")
        if x is None:
            warns.append(f"評分者 {r.get('rater')} 沒評 {iid}")
        elif s is None and "na_when" not in it:
            warns.append(f"評分者 {r.get('rater')} 把 {iid} 填 null，但這題不允許不適用（當作缺值）")
        elif s is not None:
            try:
                s = int(round(float(s)))
            except (TypeError, ValueError):
                warns.append(f"評分者 {r.get('rater')} 的 {iid} 分數不是數字（{s}）")
                s = None
            if s is not None and not 1 <= s <= 5:
                warns.append(f"評分者 {r.get('rater')} 的 {iid} 分數超出 1–5（{s}）")
                s = max(1, min(5, s))
        out[iid] = s
    wrong = [c for c in r.get("claims", []) if str(c.get("verdict", "")).lower() == "wrong"]
    if wrong and out.get("C1.1") is not None and out["C1.1"] > 2:
        warns.append(f"評分者 {r.get('rater')} 列了 {len(wrong)} 個 wrong 卻給 C1.1={out['C1.1']}：依規準降為 2")
        out["C1.1"] = 2
    sq = r.get("subquestions") or []
    if sq and out.get("C2.1") is not None:
        frac = sum(1 for q in sq if q.get("answered_at")) / len(sq)
        cap = 1 + 4 * frac
        if out["C2.1"] > cap + 1e-9:
            warns.append(f"評分者 {r.get('rater')} 的子問題只回答 {frac:.0%} 卻給 C2.1={out['C2.1']}：依規準降為 {cap:.1f}")
            out["C2.1"] = round(cap, 2)
    return out, wrong


def cmd_score(a):
    ev, fi, fl, fp = paths(a)
    cfg, rubric = load_json(INSTRUMENT / "scoring.json"), load_json(INSTRUMENT / "rubric.json")
    case = load_json(a.case) if pathlib.Path(a.case).exists() else {}
    if a.no_video:
        rep = dict(case_id=case.get("case_id"), label=a.label, overall=0.0, flags=["沒有影片"], scored_at=datetime.datetime.now().isoformat(timespec="seconds"))
        dump_json(rep, ev / "report_no_video.json")
        with open(ev / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False) + "\n")
        print("✓ 沒有影片，記 0 分。")
        return
    rid, rdir = latest_run(ev, a.run)
    obs, out = rdir / "observe", rdir / "panel" / "out"
    meta, items, lock = load_json(obs / "meta.json"), load_json(fi), load_json(fl)
    if sha_file(fi) != lock["sha256"]:
        die("eval/items.json 在鎖定後被改過。改回來，或用 freeze --new-form 換一份新試題重跑整套。")
    warns, flags = [], []

    raters = [load_json(p) for p in sorted(glob.glob(str(out / "rater_*.json")))]
    students = [load_json(p) for p in sorted(glob.glob(str(out / "student_*.json")))]
    rel = cfg["reliability"]
    if len(raters) < rel["min_raters"]:
        die(f"評分者只有 {len(raters)} 位，至少要 {rel['min_raters']} 位（{out}/rater_*.json）。")
    if len(students) < rel["min_students"]:
        warns.append(f"AI 學生只有 {len(students)} 位（建議 {rel['recommended_students']} 位），學習成效分的誤差會比較大")

    # ── 規準分 ──
    cons = rubric["constructs"]
    W = cfg["rubric"]["construct_weights"]
    obj_items, c4_obj, c3_pace = objective_scores(meta, cfg)
    clean = [clean_rater(r, rubric, warns) for r in raters]
    per_rater, construct_by_rater = [], {c["id"]: [] for c in cons}
    for (sc, _), r in zip(clean, raters):
        cs = {}
        for c in cons:
            vals = [to100(sc[it["id"]]) for it in c["items"] if sc.get(it["id"]) is not None]
            v = st.fmean(vals) if vals else None
            if c["id"] == "C4" and c4_obj is not None and v is not None:
                sh = cfg["rubric"]["c4_objective_share"]
                v = (1 - sh) * v + sh * c4_obj
            if c["id"] == "C3" and c3_pace is not None and v is not None:
                sh = cfg["rubric"]["c3_objective_share"]
                v = (1 - sh) * v + sh * c3_pace
            cs[c["id"]] = v
            construct_by_rater[c["id"]].append(v)
        avail = {k: v for k, v in cs.items() if v is not None}
        ta = sum(W[k] * v for k, v in avail.items()) / sum(W[k] for k in avail) if avail else None
        per_rater.append(dict(rater=r.get("rater"), constructs=cs, rubric_total=ta))

    # 閘門
    k = len(raters)
    wrong_lists = [w for _, w in clean]
    content_error = sum(1 for w in wrong_lists if w) > k / 2
    c44 = [sc.get("C4.4") for sc, _ in clean if sc.get("C4.4") is not None]
    no_label = bool(c44) and st.median(c44) <= 2
    dur_bad = not (cfg["objective"]["duration"]["ok"][0] <= meta.get("duration", 0) <= cfg["objective"]["duration"]["ok"][1])
    dead = not meta.get("has_video") or not meta.get("has_audio")
    caps = cfg["caps"]
    for pr_ in per_rater:
        if no_label and pr_["constructs"].get("C4") is not None:
            pr_["constructs"]["C4"] = min(pr_["constructs"]["C4"], caps["missing_ai_label"]["cap_c4"])
            av = {k_: v for k_, v in pr_["constructs"].items() if v is not None}
            pr_["rubric_total"] = sum(W[k_] * v for k_, v in av.items()) / sum(W[k_] for k_ in av)
    if no_label:
        flags.append("沒有（或看不清楚）「AI 生成教學示範，非原講者本人」標示 → 影音品質上限 40")

    rub_m, rub_se, rub_ci = mean_ci([p["rubric_total"] for p in per_rater])
    rub_ci = clip_ci(rub_ci)
    constructs = {}
    for c in cons:
        m, se, ci = mean_ci(construct_by_rater[c["id"]])
        ratings = [[sc.get(it["id"]) for it in c["items"]] for sc, _ in clean]
        r_ = rwg_j(ratings, rel["rwg_null_variance_5pt"])
        constructs[c["id"]] = dict(name=c["name"], weight=W[c["id"]], mean=m, se=se, ci95=clip_ci(ci), rwg=r_)
        if r_ is not None and r_ < rel["rwg_min"]:
            flags.append(f"{c['id']} {c['name']} 評分者意見分歧（r_wg = {r_:.2f} < {rel['rwg_min']}）→ 建議加評分者或檢查規準")
    item_stats = {}
    for c in cons:
        for it in c["items"]:
            v = [sc.get(it["id"]) for sc, _ in clean if sc.get(it["id"]) is not None]
            item_stats[it["id"]] = dict(text=it["text"], construct=c["id"], mean=st.fmean(v) if v else None,
                                        sd=st.stdev(v) if len(v) > 1 else None, n=len(v))

    # ── 學習成效分 ──
    segs = transcript_segments(obs)
    per_student, w2 = score_students(items, students, segs, cfg["learning"]) if students else ([], [])
    warns += w2
    learn = None
    if per_student:
        Lm, Lse, Lci = mean_ci([p["L"] for p in per_student])
        Lci = clip_ci(Lci)
        allrows = [r for p in per_student for r in p["rows"]]
        diag = [r for r in allrows if r["role"] == "diagnostic"]
        held = [r for r in diag if r["held_misconception"]]
        ctrl = [r for r in allrows if r["role"] == "control"]
        main = [r for r in allrows if r["role"] in ("target", "diagnostic")]
        pre_main = st.fmean(r["pre"] for r in main) if main else None
        changed_ok = [r for r in allrows if r["changed"] and r["post"] and r["role"] != "control"]
        objs = {}
        for o in items.get("objectives", []):
            rr = [r for r in allrows if r["objective"] == o["id"] and r["role"] != "control"]
            objs[o["id"]] = dict(text=o["text"], post=st.fmean(r["credit"] for r in rr) if rr else None,
                                 pre=st.fmean(r["pre"] for r in rr) if rr else None)
        exp = {}
        for e in rubric["student_experience"]["items"]:
            v = [p["experience"].get(e["id"]) for p in per_student if isinstance(p["experience"].get(e["id"]), (int, float))]
            exp[e["id"]] = dict(text=e["text"], mean=st.fmean(v) if v else None)
        learn = dict(mean=Lm, se=Lse, ci95=Lci, pre=st.fmean(p["pre"] for p in per_student), post=st.fmean(p["post"] for p in per_student),
                     gain=st.fmean(g for g in [p["gain"] for p in per_student] if g is not None) if any(p["gain"] is not None for p in per_student) else None,
                     misconception_held_pre=len(held) / len(diag) if diag else None,
                     misconception_resolved=(sum(r["post"] for r in held) / len(held)) if held else None,
                     strong_wrong_post=sum(r["strong_wrong_post"] for r in allrows if r["role"] != "control"),
                     grounded_rate=(sum(r["grounded"] for r in changed_ok) / len(changed_ok)) if changed_ok else None,
                     control_shift=st.fmean(r["post"] - r["pre"] for r in ctrl) if ctrl else None,
                     objectives=objs, experience=exp)
        if pre_main is not None and pre_main >= 0.75:
            flags.append(f"角色檢核：AI 學生前測在目標／診斷題已答對 {pre_main:.0%}，沒有守住迷思 → 學習成效分的鑑別力低，解讀要保守")
        if learn["control_shift"] and learn["control_shift"] > 0:
            flags.append(f"控制題前後測差 {learn['control_shift']:+.2f}（應接近 0）→ AI 學生可能在「看完突然全會」，學習成效分可能高估")

    # ── 總分 ──
    ow = cfg["overall"]
    if learn and learn["mean"] is not None and rub_m is not None:
        overall = ow["rubric_weight"] * rub_m + ow["learning_weight"] * learn["mean"]
        se_parts = [(ow["rubric_weight"] * (rub_se or 0)) ** 2, (ow["learning_weight"] * (learn["se"] or 0)) ** 2]
        overall_se = sum(se_parts) ** 0.5 if (rub_se or learn["se"]) else None
        # 兩個來源的自由度不同（評分者 k−1、學生 s−1），用 Welch–Satterthwaite 近似合併後的自由度
        den = (se_parts[0] ** 2 / (k - 1) if se_parts[0] else 0) + (se_parts[1] ** 2 / (len(per_student) - 1) if se_parts[1] and len(per_student) > 1 else 0)
        overall_df = (sum(se_parts) ** 2 / den) if den else None
    else:
        overall, overall_se = rub_m, rub_se
        overall_df = k - 1
        flags.append("沒有學習成效分，總分只用規準分")
    if dead:
        overall = caps["no_video_or_no_audio"]
        flags.append("沒有畫面或沒有聲音 → 0 分")
    if content_error and overall is not None:
        if overall > caps["content_error"]["cap_total"]:
            overall = caps["content_error"]["cap_total"]
        flags.append(f"內容錯誤閘門：{sum(1 for w in wrong_lists if w)}/{k} 位評分者列出錯誤陳述 → 總分上限 {caps['content_error']['cap_total']}")
    if dur_bad and overall is not None:
        overall = min(overall, caps["duration_out_of_range"]["cap_total"])
        flags.append(f"長度 {meta.get('duration'):.1f} 秒不在 30–90 秒 → 總分上限 {caps['duration_out_of_range']['cap_total']}")
    ci = clip_ci((overall - t95(round(overall_df)) * overall_se, overall + t95(round(overall_df)) * overall_se)) if overall_se and overall_df else None

    rep = dict(case_id=meta.get("case_id"), run_id=rid, label=a.label or rid, video=meta.get("video"), sha256=meta.get("sha256"),
               items_form=lock["form"], items_sha256=lock["sha256"], n_raters=k, n_students=len(per_student),
               overall=overall, overall_se=overall_se, overall_ci95=ci,
               rubric=dict(mean=rub_m, se=rub_se, ci95=rub_ci, constructs=constructs, objective=obj_items, objective_c4=c4_obj, pace=c3_pace),
               learning=learn, items=item_stats, per_rater=per_rater,
               per_student=[{k_: v for k_, v in p.items()} for p in per_student],
               wrong_claims=[dict(rater=r.get("rater"), **c) for r, (_, w) in zip(raters, clean) for c in w],
               subquestions=[dict(rater=r.get("rater"), items=r.get("subquestions", [])) for r in raters],
               flags=flags, warnings=warns, scored_at=datetime.datetime.now().isoformat(timespec="seconds"))
    dump_json(rep, rdir / "report.json")
    (rdir / "report.md").write_text(report_md(rep, meta, case), encoding="utf-8")
    (rdir / "feedback.md").write_text(feedback_md(rep, raters, per_student, rubric), encoding="utf-8")
    hist = {k_: rep[k_] for k_ in ("case_id", "run_id", "label", "sha256", "items_form", "overall", "overall_ci95", "n_raters", "n_students", "scored_at")}
    hist.update(rubric=rub_m, learning=learn["mean"] if learn else None, constructs={c: constructs[c]["mean"] for c in constructs})
    with open(ev / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(hist, ensure_ascii=False) + "\n")

    f1 = lambda x: "—" if x is None else f"{x:.1f}"
    print(f"\n總分 {f1(overall)}" + (f"（95% CI {ci[0]:.1f}–{ci[1]:.1f}）" if ci else "") +
          f"　規準 {f1(rub_m)}　學習成效 {f1(learn['mean'] if learn else None)}")
    print("　".join(f"{c} {constructs[c]['name']} {f1(constructs[c]['mean'])}" for c in constructs))
    for fl_ in flags:
        print(f"  ⚑ {fl_}")
    for w in warns:
        print(f"  ⚠ {w}")
    print(f"\n✓ {rdir}/report.md（完整報告）、feedback.md（給 AI 老師改片用，不含試題）")


# ───────────────────────── 報告 ─────────────────────────
def fmt(x, d=1):
    return "—" if x is None else f"{x:.{d}f}"


def report_md(r, meta, case):
    L = r["learning"]
    lines = [f"# AI 學生評量報告：{r['case_id']} {case.get('title') or case.get('question', '')[:40]}", "",
             f"- 影片：`{r['video']}`（run `{r['run_id']}`，標籤 `{r['label']}`）",
             f"- 試題：複本 {r['items_form']}；評分者 {r['n_raters']} 位、AI 學生 {r['n_students']} 位" +
             ("（只有 2 位時信賴區間會很寬，要比較版本建議用 3 位以上）" if min(r['n_raters'], r['n_students'] or 99) < 3 else ""), "",
             "## 總分", "",
             f"**{fmt(r['overall'])} / 100**" + (f"　95% 信賴區間 {r['overall_ci95'][0]:.1f}–{r['overall_ci95'][1]:.1f}" if r["overall_ci95"] else ""), "",
             f"= 規準分 {fmt(r['rubric']['mean'])} × 0.75 ＋ 學習成效分 {fmt(L['mean'] if L else None)} × 0.25（再套用閘門）", ""]
    if r["flags"]:
        lines += ["## 注意", ""] + [f"- {x}" for x in r["flags"]] + [""]
    lines += ["## 規準分（預測助教的四項評分）", "", "| 構念 | 比重 | 分數 | 95% CI | 評分者一致性 r_wg |", "|---|---|---|---|---|"]
    for cid, c in r["rubric"]["constructs"].items():
        ci = f"{c['ci95'][0]:.0f}–{c['ci95'][1]:.0f}" if c["ci95"] else "—"
        lines.append(f"| {cid} {c['name']} | {c['weight']}% | {fmt(c['mean'])} | {ci} | {fmt(c['rwg'], 2)} |")
    o = r["rubric"]["objective"]
    lines += ["", "客觀指標（0–100）：" + "、".join(f"{k} {fmt(v)}" for k, v in o.items()) + f"；原片語速對照 {fmt(r['rubric']['pace'])}",
              f"（長度 {meta.get('duration')} 秒、響度 {meta.get('lufs')} LUFS、說話比例 {meta.get('speech_ratio')}、"
              f"語速 {(meta.get('asr') or {}).get('rate_cps')} 字/秒 vs 原片 {(meta.get('prefix') or {}).get('rate_cps')}、字錯率 {meta.get('script_cer')}）", "",
              "### 每一題", "", "| 題 | 內容 | 平均 | 標準差 |", "|---|---|---|---|"]
    for iid, it in r["items"].items():
        lines.append(f"| {iid} | {it['text']} | {fmt(it['mean'], 2)} | {fmt(it['sd'], 2)} |")
    if r["wrong_claims"]:
        lines += ["", "### 評分者標為錯誤的陳述", ""] + [f"- 評分者 {w['rater']}［{w.get('t')}］{w.get('claim')}：{w.get('note', '')}" for w in r["wrong_claims"]]
    if L:
        lines += ["", "## 學習成效分（AI 學生前後測）", "",
                  f"- 學習成效分 {fmt(L['mean'])}" + (f"（95% CI {L['ci95'][0]:.0f}–{L['ci95'][1]:.0f}）" if L["ci95"] else ""),
                  f"- 前測正確率 {fmt(100 * L['pre'] if L['pre'] is not None else None)}% → 後測（有影片依據才算滿分）{fmt(100 * L['post'] if L['post'] is not None else None)}%；正規化增益 g = {fmt(L['gain'], 2)}",
                  f"- 前測持有迷思的比例 {fmt(100 * L['misconception_held_pre'] if L['misconception_held_pre'] is not None else None)}%，其中看完後修正 {fmt(100 * L['misconception_resolved'] if L['misconception_resolved'] is not None else None)}%",
                  f"- 改答且答對的題目中，能引用影片原句的比例 {fmt(100 * L['grounded_rate'] if L['grounded_rate'] is not None else None)}%",
                  f"- 看完仍以高信心答錯：{L['strong_wrong_post']} 題次；控制題前後測差 {fmt(L['control_shift'], 2)}（應接近 0）", "",
                  "| 學習目標 | 前測 | 後測 |", "|---|---|---|"]
        for oid, ob in L["objectives"].items():
            if ob["post"] is None:          # 只有控制題的目標不列
                continue
            lines.append(f"| {oid} {ob['text']} | {fmt(100 * ob['pre'] if ob['pre'] is not None else None, 0)}% | {fmt(100 * ob['post'] if ob['post'] is not None else None, 0)}% |")
        lines += ["", "AI 學生自評（1–5，不進總分）：" + "、".join(f"{e['text']} {fmt(e['mean'])}" for e in L["experience"].values()), ""]
        for p in r["per_student"]:
            lines.append(f"- 學生 {p['student']}：學習成效 {fmt(p['L'])}。解釋：「{p['explain']}」" + (f"　還不懂：「{p['still_confused']}」" if p["still_confused"] else ""))
    if r["warnings"]:
        lines += ["", "## 計分過程的警告", ""] + [f"- {w}" for w in r["warnings"]]
    lines += ["", "## 怎麼解讀", "",
              "- 信賴區間只反映這幾位評分者／AI 學生之間的差異（評分者抽樣誤差），不包含規準本身的系統偏誤。",
              "- 規準分的效度要靠跟人工評分比對才能確立（見 instrument/README.md〈效度〉）；在那之前，比較同一題不同版本的**相對高低**比看絕對分數可靠。",
              "- 學習成效分來自模擬學生，LLM 很難真的「不懂」：看〈注意〉裡的角色檢核與控制題。"]
    return "\n".join(lines) + "\n"


def feedback_md(r, raters, per_student, rubric):
    """給 AI 老師改片用。刻意不列試題，避免老師針對考題改。"""
    lines = [f"# 改片建議：{r['case_id']}（run {r['run_id']}）", "",
             f"總分 {fmt(r['overall'])}；規準分 {fmt(r['rubric']['mean'])}；學習成效分 {fmt(r['learning']['mean'] if r['learning'] else None)}。",
             "這份只列「哪裡不好、為什麼、怎麼改」，**不列試題**：請針對學生的困惑改，不要針對考題改。", ""]
    if r["wrong_claims"]:
        lines += ["## 先修：內容錯誤", ""] + [f"- ［{w.get('t')}］{w.get('claim')} — {w.get('note', '')}" for w in r["wrong_claims"]] + [""]
    unanswered = sorted({q.get("q") for s in r["subquestions"] for q in s["items"] if not q.get("answered_at") and q.get("q")})
    if unanswered:
        lines += ["## 沒有回答到的子問題", ""] + [f"- {q}" for q in unanswered] + [""]
    weak = sorted([(iid, it) for iid, it in r["items"].items() if it["mean"] is not None and it["mean"] < 4.5], key=lambda x: x[1]["mean"])
    if weak:
        lines += ["## 規準上失分的地方（由低到高）", ""]
        for iid, it in weak:
            lines.append(f"### {iid} {it['text']}（平均 {it['mean']:.1f} / 5）")
            for rr in raters:
                x = next((y for y in rr.get("ratings", []) if y.get("id") == iid), None)
                if x and (x.get("fix") or "").strip():
                    lines.append(f"- 評分者 {rr.get('rater')}：{x['fix'].strip()}　（依據：{(x.get('evidence') or '').strip()}）")
            lines.append("")
    L = r["learning"]
    if L:
        low = [(oid, o) for oid, o in L["objectives"].items() if o["post"] is not None and o["post"] < 0.75]
        if low:
            lines += ["## AI 學生看完還沒學會的", ""] + [f"- {o['text']}（看完後達成 {100 * o['post']:.0f}%）" for _, o in low] + [""]
        conf = [p["still_confused"] for p in per_student if p.get("still_confused")]
        if conf:
            lines += ["## AI 學生說還不懂的地方", ""] + [f"- 「{c}」" for c in conf] + [""]
    oc = [rr.get("overall_comment") for rr in raters if rr.get("overall_comment")]
    if oc:
        lines += ["## 評分者認為最該改的一件事", ""] + [f"- {c}" for c in oc] + [""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["freeze", "panel", "score"])
    ap.add_argument("--eval", default="eval")
    ap.add_argument("--case", default="input/case_input.json")
    ap.add_argument("--run", help="指定 run id（預設最新一次 observe）")
    ap.add_argument("--students", type=int, default=3)
    ap.add_argument("--raters", type=int, default=3)
    ap.add_argument("--label", help="這一版的名稱，例如 v2-改銜接句")
    ap.add_argument("--new-form", action="store_true", help="freeze：換一份新的平行試題")
    ap.add_argument("--no-video", action="store_true")
    a = ap.parse_args()
    {"freeze": cmd_freeze, "panel": cmd_panel, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
