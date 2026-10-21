#!/usr/bin/env python3
"""出題、鎖定試題、派發評分小組、計分。

在有 input/ 的那一層執行：
    python3 SKILL/scripts/score.py exam                    # 產生給出題者的指令檔 eval/exam.md（出題者不能看影片）
    python3 SKILL/scripts/score.py freeze                  # 檢查 eval/items.json 的藍圖並鎖定
    python3 SKILL/scripts/score.py panel [--students 3 --raters 3]
                                                           # 為最新一次 observe 產生每位 AI 學生／評分者的指令檔
    python3 SKILL/scripts/score.py score [--label 名稱]    # 收齊結果後計分 → report.json、report.md、feedback.md
    python3 SKILL/scripts/score.py score --no-video        # 沒有影片：直接記 0 分
"""
import argparse, datetime, glob, hashlib, json, math, pathlib, random, re, statistics as st, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import INSTRUMENT, PROMPTS, ROOT, die, dump_json, fuzzy_contains, has_keyword, load_json, norm_zh, parse_stamp
from psychometrics import mean_ci, rwg_star, t95

ROLES = {"target": 2, "diagnostic": 2, "near": 1, "far": 1, "control": 1}
TYPES = {"mcq", "tf", "numeric", "short"}
SCORE_PY = pathlib.Path(__file__).resolve()


# ───────────────────────── 共用 ─────────────────────────
def paths(a):
    ev = pathlib.Path(a.eval)
    return ev, ev / "items.json", ev / "items.lock", ev / "items_public.json"


def latest_run(ev, rid=None):
    rid = rid or ((ev / "runs" / "LATEST").read_text().strip() if (ev / "runs" / "LATEST").exists() else None)
    if not rid or not (ev / "runs" / rid / "observe" / "meta.json").exists():
        die("找不到 observe 的結果。先執行 observe.py（或用 --run 指定）。")
    return rid, ev / "runs" / rid


def sha_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def fill(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace(f"<<{k}>>", str(v))
    return tpl


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ───────────────────────── exam ─────────────────────────
def cmd_exam(a):
    ev, fi, fl, fp = paths(a)
    ev.mkdir(parents=True, exist_ok=True)
    case, vtt = pathlib.Path(a.case).resolve(), pathlib.Path(a.vtt)
    p = ev / "exam.md"
    p.write_text(fill((PROMPTS / "examiner.md").read_text(encoding="utf-8"), CASE=case, SPEC=(INSTRUMENT / "test_spec.md").resolve(),
                      VTT_LINE=f"- `{vtt.resolve()}`：原片到提問時間為止的字幕" if vtt.exists() else "",
                      OUT=fi.resolve(), CWD=pathlib.Path.cwd(), SCORE=SCORE_PY,
                      NEWFORM=f" --eval {ev.resolve()} --case {case}" + (" --new-form" if fl.exists() else "")), encoding="utf-8")
    print(f"✓ 出題者指令檔：{p.resolve()}")
    print("  交給一個**沒看過要評的影片**的執行者（subagent 或新的 headless 行程），指令只寫「完整照著 <這個檔案> 做」。")
    print(f"  例如：{headless_cmd(p.resolve())}")


def headless_cmd(p):
    return (f"claude -p --permission-mode acceptEdits --add-dir {ROOT} \"完整照著 {p} 做\""
            f"　或　codex exec --sandbox workspace-write \"完整照著 {p} 做\"")


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
        tag, role, t = q.get("id", "?"), q.get("role"), q.get("type")
        if role not in ROLES:
            errs.append(f"{tag}：role 不對（{role}）")
        if t not in TYPES:
            errs.append(f"{tag}：type 不對（{t}）")
        if role == "control":
            if q.get("objective") != "control":
                errs.append(f"{tag}：控制題的 objective 要填 \"control\"")
        else:
            if q.get("objective") not in objs:
                errs.append(f"{tag}：objective {q.get('objective')} 不在 objectives 裡")
            kw = q.get("evidence_keywords")
            if not isinstance(kw, list) or not 2 <= len([k for k in kw if str(k).strip()]) <= 5:
                errs.append(f"{tag}：要有 2–5 個 evidence_keywords（見 test_spec.md 第 5 條）")
        if not str(q.get("stem", "")).strip():
            errs.append(f"{tag}：沒有題幹")
        if not str(q.get("rationale", "")).strip():
            warns.append(f"{tag}：沒有 rationale")
        if t == "mcq":
            opts = q.get("options") or {}
            if not 3 <= len(opts) <= 5 or any(k not in "ABCDE" for k in opts):
                errs.append(f"{tag}：選項要 3–5 個，代號 A–E")
            if q.get("key") not in opts:
                errs.append(f"{tag}：key 不在選項裡")
            keys.append(q.get("key"))
            if role == "diagnostic":
                mo = q.get("misconception_option")
                if mo not in opts or mo == q.get("key"):
                    errs.append(f"{tag}：diagnostic 選擇題要有 misconception_option，而且不能是正解")
        elif t == "tf" and not isinstance(q.get("key"), bool):
            errs.append(f"{tag}：是非題的 key 要是 true／false")
        elif t == "numeric" and (isinstance(q.get("key"), bool) or not isinstance(q.get("key"), (int, float))):
            errs.append(f"{tag}：數字題的 key 要是單一數字")
        elif t == "short" and not (isinstance(q.get("accept"), list) and q["accept"]):
            errs.append(f"{tag}：簡答題要有 accept 清單")
    if len(keys) >= 4 and max(keys.count(k) for k in set(keys)) / len(keys) > 0.6:
        warns.append("選擇題的正解集中在同一個選項，請打散")
    return errs, warns


def cmd_freeze(a):
    ev, fi, fl, fp = paths(a)
    if not fi.exists():
        die(f"找不到 {fi}。先用 score.py exam 請出題者出題。")
    try:
        items = load_json(fi)
    except json.JSONDecodeError as e:
        die(f"{fi} 不是合法的 JSON：{e}")
    errs, warns = check_items(items, load_json(a.case))
    for w in warns:
        print(f"  ⚠ {w}")
    if errs:
        die("試題不符合藍圖：\n  " + "\n  ".join(errs))
    h = sha_file(fi)
    if fl.exists():
        old = load_json(fl)
        if old["sha256"] == h:
            print(f"✓ 試題沒變（複本 {old['form']}），不用重新鎖定。")
            return
        if not a.new_form:
            die("試題已經鎖定過，而且內容不同。同一題的不同版本影片要用同一份試題才能比較。\n"
                "  真的要換一份（平行複本）：加 --new-form，舊的會留在 eval/forms/。")
        form = old["form"] + 1
    else:
        form = 1
    pub = {"case_id": items["case_id"], "items": [{k: v for k, v in q.items() if k in ("id", "type", "stem", "options", "unit")} for q in items["items"]]}
    dump_json(pub, fp)
    dump_json(dict(sha256=h, form=form, n_items=len(items["items"]), frozen_at=now()), fl)
    (ev / "forms").mkdir(exist_ok=True)                      # 每一份複本都留底，換新複本後舊的還找得到
    (ev / "forms" / f"items_form{form}.json").write_text(fi.read_text(encoding="utf-8"), encoding="utf-8")
    roles = {r: sum(q["role"] == r for q in items["items"]) for r in ROLES}
    print(f"✓ 試題已鎖定（複本 {form}，{len(items['items'])} 題：" + "、".join(f"{r} {n}" for r, n in roles.items()) + "）")


# ───────────────────────── panel ─────────────────────────
def prefix_block(rdir, who):
    pdir = (rdir / "prefix").resolve()
    if not pdir.exists():
        return "（沒有原片片段可看，只能依上面的描述。）"
    head = ("## 你已經看過的原片\n\n這些是你在提問前看過的內容，打開看，讓自己「進入狀況」：" if who == "student"
            else "## 原片（對照「延續原片教法」用）\n\n打開看：")
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
    lock = load_json(fl)
    man_p = pdir / "manifest.json"
    if man_p.exists():
        man = load_json(man_p)
        if man["items_sha256"] != lock["sha256"]:
            die(f"run {rid} 已經用另一份試題派過工。換了試題就重新 observe（會開新的 run）。")
        n_s, n_r = max(a.students, man["students"]), max(a.raters, man["raters"])
    else:
        n_s, n_r = a.students, a.raters
    dump_json(dict(run=rid, items_sha256=lock["sha256"], form=lock["form"], students=n_s, raters=n_r, created=now()), man_p)

    case, pub, rubric = load_json(a.case), load_json(fp), load_json(INSTRUMENT / "rubric.json")
    lp = case.get("learner_profile", {})
    common = dict(GRADE=case.get("grade", ""), LEVEL=case.get("level", ""), SUBJECT=case.get("subject", ""),
                  TITLE=case.get("title") or f"{case.get('case_id', '')}（題目沒附課名）",
                  KNOWN="、".join(lp.get("known_concepts", [])) or "（沒寫）", MISCONCEPTION=lp.get("misconception", "（沒寫）"),
                  T=case.get("timestamp_seconds", "?"), QUESTION=case.get("question", ""), CONTEXT=case.get("context_summary", ""),
                  STYLE=case.get("style") or "題目沒提供，請看原片判斷", OBS=obs.resolve(), ITEMS=fp.resolve())
    ids = [q["id"] for q in pub["items"]]
    stage1, stage2 = [], []
    for i in range(1, n_s + 1):
        order = ids[:]
        random.Random(1000 + i).shuffle(order)
        pre_out, post_out = (out / f"student_{i}_pre.json").resolve(), (out / f"student_{i}.json").resolve()
        p1, p2 = pdir / f"student_{i}_pre.md", pdir / f"student_{i}_post.md"
        p1.write_text(fill((PROMPTS / "student_pre.md").read_text(encoding="utf-8"), N=i, ORDER="、".join(order), OUT=pre_out,
                           PREFIX_BLOCK=prefix_block(rdir, "student"), **common), encoding="utf-8")
        p2.write_text(fill((PROMPTS / "student_post.md").read_text(encoding="utf-8"), N=i, ORDER="、".join(order), OUT=post_out, PRE=pre_out,
                           PREFIX_BLOCK=prefix_block(rdir, "student"), **common), encoding="utf-8")
        if not pre_out.exists():
            stage1.append(p1)
        if not post_out.exists():
            stage2.append(p2)
    cids = [c["id"] for c in rubric["constructs"]]
    for i in range(1, n_r + 1):
        k = (i - 1) % len(cids)
        p = pdir / f"rater_{i}.md"
        ro = (out / f"rater_{i}.json").resolve()
        p.write_text(fill((PROMPTS / "rater.md").read_text(encoding="utf-8"), N=i, RUBRIC=(INSTRUMENT / "rubric.json").resolve(),
                          ORDER=" → ".join(cids[k:] + cids[:k]), OUT=ro, PREFIX_BLOCK=prefix_block(rdir, "rater"), **common), encoding="utf-8")
        if not ro.exists():
            stage1.append(p)
    print(f"✓ 評分小組指令檔（run {rid}，{n_s} 位 AI 學生、{n_r} 位評分者）。還沒交回結果的：")
    print("\n第一批（可以同時跑）：" if stage1 else "\n第一批：全部完成")
    for p in stage1:
        print(f"  {p.resolve()}")
    print("\n第二批（每位學生的前測交回後，才派他的後測；不同學生之間可以同時跑）：" if stage2 else "\n第二批：全部完成")
    for p in stage2:
        print(f"  {p.resolve()}")
    print("\n每一份都交給**獨立的**執行者（新的 subagent 或新的 headless 行程），指令只寫「完整照著 <檔案路徑> 做」。")
    print(f"  沒有 subagent 時：{headless_cmd('<檔案路徑>')}")
    print(f"\n全部完成後：python3 {SCORE_PY} score --run {rid}")


# ───────────────────────── 讀檔與格式正規化 ─────────────────────────
def load_out(p, warns):
    try:
        txt = pathlib.Path(p).read_text(encoding="utf-8").strip()
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)       # 有些執行者會包 ```json
        return json.loads(txt)
    except Exception as e:
        warns.append(f"{pathlib.Path(p).name} 讀不了（{e}），這位成員不計，請重派")
        return None


def rows_of(x):
    """作答可能是 [{id, answer…}] 或 {Q1: {…}} 或 {Q1: "A"}；統一成 {id: dict}。"""
    out = {}
    if isinstance(x, dict):
        for k, v in x.items():
            out[str(k)] = v if isinstance(v, dict) else {"answer": v}
    elif isinstance(x, list):
        for r in x:
            if isinstance(r, dict) and r.get("id") is not None:
                out[str(r["id"])] = r
    return out


def to_num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def evidence_list(e):
    if isinstance(e, dict):
        e = [e]
    if isinstance(e, str):
        m = re.match(r"\s*\[?(\d+:\d+(?:\.\d+)?)\]?\s*(.*)", e)
        e = [{"t": m[1], "quote": m[2]}] if m else [{"quote": e}]
    return [x for x in (e or []) if isinstance(x, dict)]


# ───────────────────────── 判分 ─────────────────────────
TF = {"true": True, "t": True, "o": True, "對": True, "是": True, "正確": True, "yes": True, "1": True, "○": True,
      "false": False, "f": False, "x": False, "錯": False, "否": False, "不對": False, "錯誤": False, "no": False, "0": False, "×": False}


def parse_answer(q, ans):
    """把作答轉成可比較的標準值；看不懂回傳 None。"""
    if ans is None:
        return None
    t = q["type"]
    if t == "mcq":
        if isinstance(ans, str) and ans.strip().upper() in "ABCDE" and len(ans.strip()) == 1:
            return ans.strip().upper()
        m = re.search(r"(?<![A-Za-z])([A-Ea-e])(?![A-Za-z])", str(ans))
        return m[1].upper() if m else None
    if t == "tf":
        if isinstance(ans, bool):
            return ans
        return TF.get(str(ans).strip().lower().rstrip("。.!！"))
    if t == "numeric":
        if isinstance(ans, (int, float)) and not isinstance(ans, bool):
            return float(ans)
        m = re.search(r"-?\d+(?:\.\d+)?(?:\s*/\s*\d+)?", str(ans).replace(",", ""))
        if not m:
            return None
        s = m[0].replace(" ", "")
        if "/" in s:
            n, d = s.split("/")
            return float(n) / float(d) if float(d) else None
        return float(s)
    if t == "short":
        return norm_zh(str(ans))
    return None


def is_correct(q, ans):
    v = parse_answer(q, ans)
    if v is None:
        return False
    if q["type"] == "mcq":
        return v == str(q["key"]).upper()
    if q["type"] == "tf":
        return v == q["key"]
    if q["type"] == "numeric":
        return abs(v - float(q["key"])) <= float(q.get("tolerance", 0)) + 1e-9
    return any(norm_zh(x) and norm_zh(x) == v for x in q["accept"])       # 簡答：正規化後完全相同


def same_answer(q, a1, a2):
    v1, v2 = parse_answer(q, a1), parse_answer(q, a2)
    if v1 is None or v2 is None:
        return False
    if q["type"] == "numeric":
        return abs(v1 - v2) <= float(q.get("tolerance", 0)) + 1e-9
    return v1 == v2


def grounded(q, evidence, segs, cfg):
    """引文要 (1) 寫了時間、(2) 真的出現在那個時間附近的逐字稿、(3) 引文或那一句裡有這題的 evidence_keywords。"""
    kws = [k for k in q.get("evidence_keywords") or [] if norm_zh(k)]
    for e in evidence_list(evidence):
        quote, t = str(e.get("quote", "")), parse_stamp(e.get("t"))
        if t is None or len(norm_zh(quote)) < 4:
            continue
        w = cfg["window_sec"]
        near = [s for s in segs if s[1] >= t - w and s[0] <= t + w]
        if fuzzy_contains(quote, " ".join(x[2] for x in near)) < cfg["min_quote_match"]:
            continue
        if not kws:
            return True
        ctx = quote + "".join(x[2] for x in near if x[0] <= t + 3 and x[1] >= t - 3)
        if any(has_keyword(ctx, k) for k in kws):
            return True
    return False


def transcript_segments(obs):
    segs = []
    for line in (obs / "transcript.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[(\d+:[\d.]+)–(\d+:[\d.]+)\]\s*(.*)", line)
        if m:
            segs.append((parse_stamp(m[1]), parse_stamp(m[2]), m[3]))
    return segs


def score_students(items, students, segs, cfg, warns):
    qs = {q["id"]: q for q in items["items"]}
    rw, cr, hi = cfg["role_weights"], cfg["credit"], cfg["high_confidence"]
    if any(q["role"] != "control" and not q.get("evidence_keywords") for q in qs.values()):
        warns.append("試題沒有 evidence_keywords（舊格式）：只檢查引文存在，學習成效分可能高估")
    per = []
    for s in students:
        pre, post = rows_of(s.get("pre")), rows_of(s.get("post"))
        miss = [i for i in qs if i not in pre or i not in post]
        if miss:
            warns.append(f"學生 {s.get('student')} 少答 {', '.join(miss)}（當作答錯）")
        rows = []
        for qid, q in qs.items():
            a0, a1 = pre.get(qid, {}), post.get(qid, {})
            c0, c1 = is_correct(q, a0.get("answer")), is_correct(q, a1.get("answer"))
            g = grounded(q, a1.get("evidence"), segs, cfg["grounding"])
            credit = cr["correct_grounded"] if (c1 and g) else cr["correct_ungrounded"] if c1 else cr["wrong"]
            conf0, conf1 = to_num(a0.get("confidence")), to_num(a1.get("confidence"))
            if q["type"] == "mcq" and q.get("misconception_option"):
                held = parse_answer(q, a0.get("answer")) == q["misconception_option"]
            else:
                held = q["role"] == "diagnostic" and not c0
            rows.append(dict(id=qid, role=q["role"], objective=q.get("objective"), pre=int(c0), post=int(c1), credit=credit, grounded=g,
                             changed=not same_answer(q, a0.get("answer"), a1.get("answer")), conf0=conf0, conf1=conf1,
                             held_misconception=bool(held), strong_wrong_post=(not c1) and (conf1 or 0) >= hi))
        scored = [r for r in rows if r["role"] != "control"]
        wsum = sum(rw[r["role"]] for r in scored)
        per.append(dict(student=s.get("student"), L=100 * sum(rw[r["role"]] * r["credit"] for r in scored) / wsum if wsum else None,
                        pre=st.fmean(r["pre"] for r in scored) if scored else None, post=st.fmean(r["post"] for r in scored) if scored else None,
                        rows=rows, experience=s.get("experience") if isinstance(s.get("experience"), dict) else {},
                        explain=str(s.get("explain") or ""), still_confused=str(s.get("still_confused") or "")))
    return per


# ───────────────────────── 評分者 ─────────────────────────
def clip_ci(ci, hi=100.0):
    return None if not ci else (max(0.0, ci[0]), min(hi, ci[1]))


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
    out = {"duration": band(meta.get("duration"), o["duration"]["ok"], o["duration"]["zero_at"])}
    L = meta.get("lufs")
    if L is not None:
        lo, hi = o["loudness_lufs"]["ok"]
        out["loudness"] = 1.0 if lo <= L <= hi else clamp01(1 - o["loudness_lufs"]["minus_per_lu"] * min(abs(L - lo), abs(L - hi)) / 100)
    sr = meta.get("speech_ratio")
    if sr is not None:
        out["speech_ratio"] = clamp01((sr - o["speech_ratio"]["zero_at"]) / (o["speech_ratio"]["full_at"] - o["speech_ratio"]["zero_at"]))
    it, conf = o["intelligibility"], (meta.get("asr") or {}).get("confidence")
    if conf is not None:      # 固定用辨識信心（有沒有老師腳本都一樣量法，版本之間才能比）
        out["intelligibility"] = clamp01((conf - it["asr_conf_zero_at"]) / (it["asr_conf_full_at"] - it["asr_conf_zero_at"]))
    pr, vr = (meta.get("prefix") or {}).get("rate_cps"), (meta.get("asr") or {}).get("rate_cps")
    pace = band(vr / pr, o["pace_ratio_vs_prefix"]["ok"], o["pace_ratio_vs_prefix"]["zero_at"]) if pr and vr else None
    c4 = [v for v in out.values() if v is not None]
    return ({k: (None if v is None else round(100 * v, 1)) for k, v in out.items()},
            (100 * st.fmean(c4) if c4 else None), (None if pace is None else 100 * pace))


def clean_rater(r, rubric, warns):
    items = {it["id"]: it for c in rubric["constructs"] for it in c["items"]}
    got = rows_of(r.get("ratings"))
    tag = r.get("_file", r.get("rater"))
    out = {}
    for iid, it in items.items():
        x = got.get(iid)
        s = None if x is None else x.get("score")
        if x is None:
            warns.append(f"{tag} 沒評 {iid}")
        elif s is None and "na_when" not in it:
            warns.append(f"{tag} 把 {iid} 填 null，但這題不允許不適用（當作缺值）")
        elif s is not None:
            v = to_num(s)
            if v is None:
                warns.append(f"{tag} 的 {iid} 分數不是數字（{s}）")
                s = None
            else:
                s = int(round(v))
                if not 1 <= s <= 5:
                    warns.append(f"{tag} 的 {iid} 分數超出 1–5（{s}）")
                    s = max(1, min(5, s))
        out[iid] = s
    claims = [c for c in (r.get("claims") or []) if isinstance(c, dict)]
    wrong = [c for c in claims if str(c.get("verdict", "")).lower() == "wrong"]
    if wrong and out.get("C1.1") is not None and out["C1.1"] > 2:
        warns.append(f"{tag} 列了 {len(wrong)} 個 wrong 卻給 C1.1={out['C1.1']}：依規準降為 2")
        out["C1.1"] = 2
    sq = [q for q in (r.get("subquestions") or []) if isinstance(q, dict)]
    if sq and out.get("C2.1") is not None:
        frac = sum(1 for q in sq if q.get("answered_at")) / len(sq)
        cap = 1 + 4 * frac
        if out["C2.1"] > cap + 1e-9:
            warns.append(f"{tag} 的子問題只回答 {frac:.0%} 卻給 C2.1={out['C2.1']}：依規準降為 {cap:.1f}")
            out["C2.1"] = round(cap, 2)
    return out, wrong


def text_of(x):
    return x.strip() if isinstance(x, str) else "" if x is None else str(x)


# ───────────────────────── score ─────────────────────────
def welch(parts):
    """parts：[(加權後的 SE, 自由度)]。回傳 (合併 SE, Welch–Satterthwaite 自由度)。"""
    parts = [(se, df) for se, df in parts if se is not None and df and df > 0]
    if not parts:
        return None, None
    v = sum(se ** 2 for se, _ in parts)
    den = sum(se ** 4 / df for se, df in parts)
    return math.sqrt(v), (v ** 2 / den if den else None)


def cmd_score(a):
    ev, fi, fl, fp = paths(a)
    cfg, rubric = load_json(INSTRUMENT / "scoring.json"), load_json(INSTRUMENT / "rubric.json")
    case = load_json(a.case) if pathlib.Path(a.case).exists() else {}
    if a.no_video:
        rep = dict(case_id=case.get("case_id"), label=a.label, overall=0.0, flags=["沒有影片"], scored_at=now())
        ev.mkdir(parents=True, exist_ok=True)
        dump_json(rep, ev / "report_no_video.json")
        with open(ev / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False) + "\n")
        print("✓ 沒有影片，記 0 分。")
        return
    rid, rdir = latest_run(ev, a.run)
    obs, out = rdir / "observe", rdir / "panel" / "out"
    meta, items, lock = load_json(obs / "meta.json"), load_json(fi), load_json(fl)
    if not meta.get("has_video") or not meta.get("audible", meta.get("has_audio")):
        # 沒畫面或聽不到聲音：直接 0 分，不用派評分小組
        rep = dict(case_id=meta.get("case_id"), run_id=rid, label=a.label or rid, video=meta.get("video"), sha256=meta.get("sha256"),
                   items_form=lock["form"], items_sha256=lock["sha256"], overall=0.0, overall_se=None, overall_df=None, overall_ci95=None,
                   flags=["沒有畫面或聽不到聲音 → 0 分（沒有派評分小組）"], scored_at=now())
        dump_json(rep, rdir / "report.json")
        (rdir / "report.md").write_text(f"# AI 學生評量報告：{rep['case_id']}\n\n**0 / 100**：影片沒有畫面或聽不到講話"
                                        f"（音軌：{'有' if meta.get('has_audio') else '沒有'}，響度 {meta.get('lufs')} LUFS）。\n", encoding="utf-8")
        with open(ev / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: rep[k] for k in ("case_id", "run_id", "label", "sha256", "items_form", "items_sha256", "overall", "scored_at")}, ensure_ascii=False) + "\n")
        print(f"總分 0（沒有畫面或聽不到聲音，不用派評分小組）\n✓ {rdir}/report.md")
        return
    if sha_file(fi) != lock["sha256"]:
        die("eval/items.json 在鎖定後被改過。改回來，或用 freeze --new-form 換一份新試題重跑整套。")
    man = load_json(rdir / "panel" / "manifest.json") if (rdir / "panel" / "manifest.json").exists() else None
    if not man:
        die(f"run {rid} 還沒派工（找不到 panel/manifest.json）。先執行 score.py panel。")
    if man["items_sha256"] != lock["sha256"]:
        die(f"run {rid} 的作答用的是另一份試題（複本 {man['form']}），不能用現在的複本 {lock['form']} 計分。重新 observe → panel。")
    warns, flags = [], []
    rel = cfg["reliability"]

    raters = []
    for i in range(1, man["raters"] + 1):
        p = out / f"rater_{i}.json"
        if not p.exists():
            warns.append(f"評分者 {i} 沒有交回 {p.name}")
            continue
        r = load_out(p, warns)
        if isinstance(r, dict):
            r["_file"] = f"評分者 {i}"
            r["rater"] = i
            raters.append(r)
    students = []
    for i in range(1, man["students"] + 1):
        p0, p1 = out / f"student_{i}_pre.json", out / f"student_{i}.json"
        if not (p0.exists() and p1.exists()):
            warns.append(f"AI 學生 {i} 的 {'前測' if not p0.exists() else '後測'}沒有交回")
            continue
        s0, s1 = load_out(p0, warns), load_out(p1, warns)
        if isinstance(s0, dict) and isinstance(s1, dict):
            s1 = dict(s1, pre=s0.get("pre"), student=i)
            students.append(s1)
    if len(raters) < rel["min_raters"]:
        die(f"評分者只有 {len(raters)} 位，至少要 {rel['min_raters']} 位。\n  " + "\n  ".join(warns))
    if len(students) < rel["min_students"]:
        warns.append(f"AI 學生只有 {len(students)} 位（至少 {rel['min_students']} 位），不算學習成效分")

    # ── 規準分 ──
    cons, W = rubric["constructs"], cfg["rubric"]["construct_weights"]
    obj_items, c4_obj, c3_pace = objective_scores(meta, cfg)
    clean = [clean_rater(r, rubric, warns) for r in raters]
    k = len(raters)
    wrong_lists = [w for _, w in clean]
    c44 = [sc.get("C4.4") for sc, _ in clean if sc.get("C4.4") is not None]
    no_label = bool(c44) and st.median(c44) <= 2
    caps = cfg["caps"]
    per_rater = []
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
            if c["id"] == "C4" and no_label and v is not None:
                v = min(v, caps["missing_ai_label"]["cap_c4"])
            cs[c["id"]] = v
        avail = {kk: v for kk, v in cs.items() if v is not None}
        per_rater.append(dict(rater=r["rater"], constructs=cs,
                              rubric_total=sum(W[kk] * v for kk, v in avail.items()) / sum(W[kk] for kk in avail) if avail else None))
    if no_label:
        flags.append(f"沒有（或看不清楚）「AI 生成教學示範，非原講者本人」標示 → 影音品質上限 {caps['missing_ai_label']['cap_c4']}")

    floor = rel["sd_floor"]
    rub_m, rub_se, rub_ci = mean_ci([p["rubric_total"] for p in per_rater], floor)
    constructs = {}
    for c in cons:
        vals = [p["constructs"][c["id"]] for p in per_rater]
        m, se, ci = mean_ci(vals, floor)
        r_ = rwg_star([[sc.get(it["id"]) for it in c["items"]] for sc, _ in clean], rel["rwg_null_variance"])
        constructs[c["id"]] = dict(name=c["name"], weight=W[c["id"]], mean=m, se=se, ci95=clip_ci(ci), rwg=r_, by_rater=vals)
        if r_ is not None and r_ < rel["rwg_min"]:
            flags.append(f"{c['id']} {c['name']} 評分者意見分歧（r*wg = {r_:.2f} < {rel['rwg_min']}）→ 檢查各評分者的證據，或加評分者")
    if rub_ci and (rub_ci[1] - rub_ci[0]) / 2 > rel["ci_halfwidth_max"]:
        flags.append(f"規準分的 95% 信賴區間半寬 {(rub_ci[1] - rub_ci[0]) / 2:.0f} 分（> {rel['ci_halfwidth_max']}）→ 要比較版本，建議評分者加到 {max(rel['recommended_raters'], k + 2)} 位（panel --raters {max(rel['recommended_raters'], k + 2)}，只會派新增的）")
    item_stats = {}
    for c in cons:
        for it in c["items"]:
            v = [sc.get(it["id"]) for sc, _ in clean if sc.get(it["id"]) is not None]
            item_stats[it["id"]] = dict(text=it["text"], construct=c["id"], mean=st.fmean(v) if v else None,
                                        sd=st.stdev(v) if len(v) > 1 else None, n=len(v))

    # ── 學習成效分 ──
    segs = transcript_segments(obs)
    if meta.get("asr_ok") is False:
        flags.append("影片沒有逐字稿（語音辨識失敗）→ 學生無法引用原句，學習成效分偏低、不可和其他版本比較")
    per_student = score_students(items, students, segs, cfg["learning"], warns) if len(students) >= rel["min_students"] else []
    learn, lw = None, cfg["overall"]["learning_weight"]
    if per_student:
        Lm, Lse, Lci = mean_ci([p["L"] for p in per_student], floor)
        allrows = [r for p in per_student for r in p["rows"]]
        main_ = [r for r in allrows if r["role"] != "control"]
        diag = [r for r in allrows if r["role"] == "diagnostic"]
        held = [r for r in diag if r["held_misconception"]]
        ctrl = [r for r in allrows if r["role"] == "control"]
        core = [r for r in allrows if r["role"] in ("target", "diagnostic")]
        pre_core = st.fmean(r["pre"] for r in core) if core else None
        pre_all, post_all = st.fmean(r["pre"] for r in main_), st.fmean(r["post"] for r in main_)
        objs = {}
        for o in items.get("objectives", []):
            rr = [r for r in main_ if r["objective"] == o["id"]]
            if rr:
                objs[o["id"]] = dict(text=o["text"], pre=st.fmean(r["pre"] for r in rr), post=st.fmean(r["credit"] for r in rr))
        exp = {}
        for e in rubric["student_experience"]["items"]:
            v = [to_num(p["experience"].get(e["id"])) for p in per_student]
            v = [x for x in v if x is not None]
            exp[e["id"]] = dict(text=e["text"], mean=st.fmean(v) if v else None)
        ctrl_pre_wrong = [r for r in ctrl if not r["pre"]]
        learn = dict(mean=Lm, se=Lse, ci95=clip_ci(Lci), pre=pre_all, post=post_all,
                     gain=(post_all - pre_all) / (1 - pre_all) if pre_all < 1 else None,     # 班級平均的 <g>（Hake, 1998）
                     credit=st.fmean(r["credit"] for r in main_),
                     misconception_held_pre=len(held) / len(diag) if diag else None,
                     misconception_resolved=(sum(r["post"] for r in held) / len(held)) if held else None,
                     strong_wrong_post=sum(r["strong_wrong_post"] for r in main_),
                     grounded_rate=(sum(r["grounded"] for r in main_ if r["post"]) / sum(r["post"] for r in main_)) if any(r["post"] for r in main_) else None,
                     control_pre_correct=(1 - len(ctrl_pre_wrong) / len(ctrl)) if ctrl else None,
                     control_leak=(sum(r["post"] for r in ctrl_pre_wrong) / len(ctrl_pre_wrong)) if ctrl_pre_wrong else None,
                     objectives=objs, experience=exp)
        # 角色檢核：前測在目標／診斷題答對越多，前後測越分不出影片好壞 → 自動降低學習成效分的比重
        ceil = cfg["learning"]["ceiling"]
        if pre_core is not None and pre_core > ceil["full_weight_until"]:
            scale = clamp01((ceil["zero_weight_at"] - pre_core) / (ceil["zero_weight_at"] - ceil["full_weight_until"]))
            lw *= scale
            flags.append(f"角色檢核：AI 學生前測在目標／診斷題已答對 {pre_core:.0%}，沒有守住迷思 → 學習成效分比重降為 {lw:.2f}")
        if learn["control_leak"]:
            flags.append(f"控制題：前測答錯的 AI 學生看完後有 {learn['control_leak']:.0%} 答對（影片不會教這個）→ AI 學生可能用了自己的知識，學習成效分可能高估")
        if learn["control_pre_correct"] is not None and learn["control_pre_correct"] >= 0.75:
            flags.append("控制題前測就答對了，偵測不到 AI 學生灌水 → 下次出題換一個前測會答錯的控制題")
        learn["weight_used"] = lw

    # ── 總分 ──
    dead = not meta.get("has_video") or not meta.get("audible", meta.get("has_audio"))
    rw_ = 1 - lw if per_student else 1.0
    if per_student:
        overall = rw_ * rub_m + lw * learn["mean"]
        se, df = welch([(rw_ * (rub_se or 0), k - 1), (lw * (learn["se"] or 0), len(per_student) - 1)])
    else:
        overall, se, df = rub_m, rub_se, k - 1
        flags.append("沒有學習成效分，總分只用規準分")
    cap = 100.0
    if dead:
        overall, cap = 0.0, 0.0
        flags.append("沒有畫面或聽不到聲音 → 0 分")
    if sum(1 for w in wrong_lists if w) > k / 2:
        cap = min(cap, caps["content_error"]["cap_total"])
        flags.append(f"內容錯誤閘門：{sum(1 for w in wrong_lists if w)}/{k} 位評分者列出錯誤陳述 → 總分上限 {caps['content_error']['cap_total']}")
    if not (cfg["objective"]["duration"]["ok"][0] <= meta.get("duration", 0) <= cfg["objective"]["duration"]["ok"][1]):
        cap = min(cap, caps["duration_out_of_range"]["cap_total"])
        flags.append(f"長度 {meta.get('duration'):.1f} 秒不在 30–90 秒 → 總分上限 {caps['duration_out_of_range']['cap_total']}")
    raw = overall
    overall = min(overall, cap) if overall is not None else None
    ci = None
    if se and df and not dead:
        h = t95(math.floor(df)) * se
        ci = clip_ci((raw - h, raw + h), cap)

    # ── 和上一版比 ──
    hist_p = ev / "history.jsonl"
    prev = None
    if hist_p.exists():
        for line in hist_p.read_text(encoding="utf-8").splitlines():
            try:
                h_ = json.loads(line)
            except json.JSONDecodeError:
                continue
            if h_.get("case_id") == meta.get("case_id") and h_.get("items_sha256") == lock["sha256"] and h_.get("run_id") != rid and h_.get("overall") is not None:
                prev = h_
    comparison = None
    if prev and se and prev.get("overall_se") and prev.get("overall_df") and df:
        d = overall - prev["overall"]
        dse, ddf = welch([(se, df), (prev["overall_se"], prev["overall_df"])])
        hw = t95(math.floor(ddf)) * dse
        comparison = dict(vs_run=prev["run_id"], vs_label=prev.get("label"), delta=d, ci95=(d - hw, d + hw),
                          verdict="進步" if d - hw > 0 else "退步" if d + hw < 0 else "差異在誤差範圍內")

    rep = dict(case_id=meta.get("case_id"), run_id=rid, label=a.label or rid, video=meta.get("video"), sha256=meta.get("sha256"),
               items_form=lock["form"], items_sha256=lock["sha256"], n_raters=k, n_students=len(per_student),
               overall=overall, overall_uncapped=raw, overall_se=se, overall_df=df, overall_ci95=ci, comparison=comparison,
               rubric=dict(mean=rub_m, se=rub_se, ci95=clip_ci(rub_ci), weight_used=rw_, constructs=constructs, objective=obj_items,
                           objective_c4=c4_obj, pace=c3_pace),
               learning=learn, items=item_stats, per_rater=per_rater, per_student=per_student,
               rater_items=[dict(rater=r["rater"], scores=sc) for (sc, _), r in zip(clean, raters)],
               wrong_claims=[dict(rater=r["rater"], **{kk: text_of(v) for kk, v in c.items()}) for r, (_, w) in zip(raters, clean) for c in w],
               subquestions=[dict(rater=r["rater"], items=[q for q in (r.get("subquestions") or []) if isinstance(q, dict)]) for r in raters],
               flags=flags, warnings=warns, scored_at=now())
    dump_json(rep, rdir / "report.json")
    (rdir / "report.md").write_text(report_md(rep, meta, case, raters), encoding="utf-8")
    (rdir / "feedback.md").write_text(feedback_md(rep, raters, per_student, items), encoding="utf-8")
    hist = {kk: rep[kk] for kk in ("case_id", "run_id", "label", "sha256", "items_form", "items_sha256", "overall", "overall_se", "overall_df",
                                   "overall_ci95", "n_raters", "n_students", "scored_at")}
    hist.update(rubric=rub_m, learning=learn["mean"] if learn else None, constructs={c: constructs[c]["mean"] for c in constructs})
    with open(hist_p, "a", encoding="utf-8") as f:
        f.write(json.dumps(hist, ensure_ascii=False) + "\n")

    f1 = lambda x: "—" if x is None else f"{x:.1f}"
    print(f"\n總分 {f1(overall)}" + (f"（95% CI {ci[0]:.1f}–{ci[1]:.1f}）" if ci else "") +
          f"　規準 {f1(rub_m)}　學習成效 {f1(learn['mean'] if learn else None)}")
    print("　".join(f"{c} {constructs[c]['name']} {f1(constructs[c]['mean'])}" for c in constructs))
    if comparison:
        print(f"和 {comparison['vs_label']} 比：{comparison['delta']:+.1f}（95% CI {comparison['ci95'][0]:+.1f}～{comparison['ci95'][1]:+.1f}）→ {comparison['verdict']}")
    for x in flags:
        print(f"  ⚑ {x}")
    for w in warns:
        print(f"  ⚠ {w}")
    print(f"\n✓ {rdir}/report.md（完整報告）、feedback.md（給 AI 老師改片用，不含試題）")


# ───────────────────────── 報告 ─────────────────────────
def fmt(x, d=1):
    return "—" if x is None else f"{x:.{d}f}"


def pct(x, d=0):
    return "—" if x is None else f"{100 * x:.{d}f}%"


def rater_notes(raters, iid):
    out = []
    for r in raters:
        x = rows_of(r.get("ratings")).get(iid)
        if x:
            out.append((r["rater"], x.get("score"), text_of(x.get("evidence")), text_of(x.get("fix"))))
    return out


def report_md(r, meta, case, raters):
    L, R = r["learning"], r["rubric"]
    lines = [f"# AI 學生評量報告：{r['case_id']} {case.get('title') or case.get('question', '')[:40]}", "",
             f"- 影片：`{r['video']}`（run `{r['run_id']}`，標籤 `{r['label']}`）",
             f"- 試題：複本 {r['items_form']}；評分者 {r['n_raters']} 位、AI 學生 {r['n_students']} 位" +
             ("（少於 3 位時信賴區間會很寬，要比較版本建議 3 位以上）" if min(r["n_raters"], r["n_students"] or 99) < 3 else ""), "",
             "## 總分", "",
             f"**{fmt(r['overall'])} / 100**" + (f"　95% 信賴區間 {r['overall_ci95'][0]:.1f}–{r['overall_ci95'][1]:.1f}" if r["overall_ci95"] else ""), "",
             f"= 規準分 {fmt(R['mean'])} × {R['weight_used']:.2f} ＋ 學習成效分 {fmt(L['mean'] if L else None)} × {(L or {}).get('weight_used', 0):.2f}"
             + ("（再套用閘門）" if r["overall"] != r["overall_uncapped"] else ""), ""]
    if r.get("comparison"):
        c = r["comparison"]
        lines += [f"和上一版（{c['vs_label']}）比：**{c['delta']:+.1f}**（95% CI {c['ci95'][0]:+.1f}～{c['ci95'][1]:+.1f}）→ **{c['verdict']}**", ""]
    if r["flags"]:
        lines += ["## 注意", ""] + [f"- {x}" for x in r["flags"]] + [""]
    lines += ["## 規準分（預測助教的四項評分）", "", "| 構念 | 比重 | 分數 | 95% CI | 評分者一致性 r*wg | 各評分者 |", "|---|---|---|---|---|---|"]
    for cid, c in R["constructs"].items():
        ci = f"{c['ci95'][0]:.0f}–{c['ci95'][1]:.0f}" if c["ci95"] else "—"
        lines.append(f"| {cid} {c['name']} | {c['weight']}% | {fmt(c['mean'])} | {ci} | {fmt(c['rwg'], 2)} | " + "、".join(fmt(v, 0) for v in c["by_rater"]) + " |")
    lines += ["", "客觀指標（0–100）：" + "、".join(f"{k} {fmt(v)}" for k, v in R["objective"].items()) + f"；原片語速對照 {fmt(R['pace'])}",
              f"（長度 {meta.get('duration')} 秒、響度 {meta.get('lufs')} LUFS、說話比例 {meta.get('speech_ratio')}、"
              f"語速 {(meta.get('asr') or {}).get('rate_cps')} 字/秒 vs 原片 {(meta.get('prefix') or {}).get('rate_cps')}、"
              f"辨識信心 {(meta.get('asr') or {}).get('confidence')}（{(meta.get('asr') or {}).get('model')}）、與腳本字錯率 {meta.get('script_cer')}（參考）)", ""]
    weak = sorted([(i, it) for i, it in r["items"].items() if it["mean"] is not None], key=lambda x: x[1]["mean"])[:3]
    if weak:
        lines += ["### 失分最多的三題", ""]
        for iid, it in weak:
            lines.append(f"**{iid} {it['text']}**（平均 {it['mean']:.1f}）")
            for rr, sc, evd, _ in rater_notes(raters, iid):
                lines.append(f"- 評分者 {rr}（{sc} 分）：{evd}")
            lines.append("")
    lines += ["### 每一題", "", "| 題 | 內容 | 平均 | 標準差 |", "|---|---|---|---|"]
    for iid, it in r["items"].items():
        lines.append(f"| {iid} | {it['text']} | {fmt(it['mean'], 2)} | {fmt(it['sd'], 2)} |")
    if r["wrong_claims"]:
        lines += ["", "### 評分者標為錯誤的陳述", ""] + [f"- 評分者 {w['rater']}［{w.get('t')}］{w.get('claim')}：{w.get('note', '')}" for w in r["wrong_claims"]]
    if L:
        lines += ["", "## 學習成效分（AI 學生前後測）", "",
                  f"- 學習成效分 {fmt(L['mean'])}" + (f"（95% CI {L['ci95'][0]:.0f}–{L['ci95'][1]:.0f}）" if L["ci95"] else "") +
                  "：後測答對且引用到影片中講這題的原句 = 1 分，答對但沒有影片依據 = 0.5 分",
                  f"- 答對率：前測 {pct(L['pre'])} → 後測 {pct(L['post'])}；班級正規化增益 <g> = {fmt(L['gain'], 2)}",
                  f"- 後測答對的題目中，有影片依據的比例 {pct(L['grounded_rate'])}",
                  f"- 前測持有迷思的比例 {pct(L['misconception_held_pre'])}，其中看完後修正 {pct(L['misconception_resolved'])}",
                  f"- 看完仍以高信心答錯：{L['strong_wrong_post']} 題次",
                  f"- 控制題：前測答對 {pct(L['control_pre_correct'])}；前測答錯者看完後答對 {pct(L['control_leak'])}（應為 0）", "",
                  "| 學習目標 | 前測答對 | 後測（含依據） |", "|---|---|---|"]
        for oid, ob in L["objectives"].items():
            lines.append(f"| {oid} {ob['text']} | {pct(ob['pre'])} | {pct(ob['post'])} |")
        lines += ["", "AI 學生自評（1–5，不進總分）：" + "、".join(f"{e['text']} {fmt(e['mean'])}" for e in L["experience"].values()), ""]
        for p in r["per_student"]:
            lines.append(f"- 學生 {p['student']}：學習成效 {fmt(p['L'])}。解釋：「{p['explain']}」" + (f"　還不懂：「{p['still_confused']}」" if p["still_confused"] else ""))
    if r["warnings"]:
        lines += ["", "## 計分過程的警告", ""] + [f"- {w}" for w in r["warnings"]]
    lines += ["", "## 怎麼解讀", "",
              "- 信賴區間反映這幾位評分者／AI 學生之間的差異（抽樣誤差，每個來源的標準差至少以 5 分計），不包含規準本身的系統偏誤。",
              "- 規準分的效度要靠跟人工評分比對才能確立（見 instrument/README.md〈效度〉）；在那之前，比較同一題不同版本的**相對高低**比看絕對分數可靠。",
              "- 兩個版本有沒有差，看「和上一版比」那一行的差異信賴區間，不要看兩個總分的區間有沒有重疊。",
              "- 學習成效分來自模擬學生，LLM 很難真的「不懂」：看〈注意〉裡的角色檢核與控制題。"]
    return "\n".join(lines) + "\n"


def leaks_item(text, items):
    """學生的話如果幾乎照抄某一題的題幹或選項，就不轉給老師（避免老師針對考題改片）。"""
    for q in items["items"]:
        for s in [q.get("stem", "")] + list((q.get("options") or {}).values()):
            if len(norm_zh(s)) >= 6 and fuzzy_contains(s, text) >= 0.6:
                return True
    return False


def feedback_md(r, raters, per_student, items):
    """給 AI 老師改片用。刻意不列試題，避免老師針對考題改。"""
    L = r["learning"]
    lines = [f"# 改片建議：{r['case_id']}（run {r['run_id']}）", "",
             f"總分 {fmt(r['overall'])}；規準分 {fmt(r['rubric']['mean'])}；學習成效分 {fmt(L['mean'] if L else None)}。",
             "這份只列「哪裡不好、為什麼、怎麼改」，**不列試題**：請針對學生的困惑改，不要針對考題改。", ""]
    if r["wrong_claims"]:
        lines += ["## 先修：內容錯誤", ""] + [f"- ［{w.get('t')}］{w.get('claim')} — {w.get('note', '')}" for w in r["wrong_claims"]] + [""]
    unanswered = sorted({text_of(q.get("q")) for s in r["subquestions"] for q in s["items"] if not q.get("answered_at") and q.get("q")})
    if unanswered:
        lines += ["## 沒有回答到的子問題", ""] + [f"- {q}" for q in unanswered] + [""]
    weak = sorted([(iid, it) for iid, it in r["items"].items() if it["mean"] is not None and it["mean"] < 4.5], key=lambda x: x[1]["mean"])
    if weak:
        lines += ["## 規準上失分的地方（由低到高）", ""]
        for iid, it in weak:
            lines.append(f"### {iid} {it['text']}（平均 {it['mean']:.1f} / 5）")
            for rr, _, evd, fix in rater_notes(raters, iid):
                if fix:
                    lines.append(f"- 評分者 {rr}：{fix}　（依據：{evd}）")
            lines.append("")
    if L:
        low = [o for o in L["objectives"].values() if o["post"] is not None and o["post"] < 0.75]
        if low:
            lines += ["## AI 學生看完還沒學會的", ""] + [f"- {o['text']}（看完後達成 {100 * o['post']:.0f}%）" for o in low] + [""]
        conf = [p["still_confused"] for p in per_student if p.get("still_confused") and not leaks_item(p["still_confused"], items)]
        hidden = sum(1 for p in per_student if p.get("still_confused") and leaks_item(p["still_confused"], items))
        if conf or hidden:
            lines += ["## AI 學生說還不懂的地方", ""] + [f"- 「{c}」" for c in conf] + \
                     ([f"- （另有 {hidden} 則因為提到測驗內容而不轉給老師）"] if hidden else []) + [""]
    oc = [text_of(rr.get("overall_comment")) for rr in raters if text_of(rr.get("overall_comment"))]
    if oc:
        lines += ["## 評分者認為最該改的一件事", ""] + [f"- {c}" for c in oc] + [""]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["exam", "freeze", "panel", "score"])
    ap.add_argument("--eval", default="eval")
    ap.add_argument("--case", default="input/case_input.json")
    ap.add_argument("--vtt", default="input/prefix.vtt")
    ap.add_argument("--run", help="指定 run id（預設最新一次 observe）")
    ap.add_argument("--students", type=int, default=3)
    ap.add_argument("--raters", type=int, default=3)
    ap.add_argument("--label", help="這一版的名稱，例如 v2-改銜接句")
    ap.add_argument("--new-form", action="store_true", help="freeze：換一份新的平行試題")
    ap.add_argument("--no-video", action="store_true")
    a = ap.parse_args()
    {"exam": cmd_exam, "freeze": cmd_freeze, "panel": cmd_panel, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
