#!/usr/bin/env python3
"""跨多支影片的信度與效度分析：評量工具本身好不好，要靠很多次評分累積起來看。

    python3 SKILL/scripts/analyze.py <資料夾或 report.json …> [--human human.csv] [--groups groups.csv] [--out analysis.md]

會遞迴找出所有 eval/runs/*/report.json，然後算：

信度
  · 評分者間：每個構念的 ICC(2,1)（單一評分者）、ICC(2,k)（k 位平均），與 Krippendorff's α
  · 內部一致性：每個構念的 Cronbach's α（各題取評分者平均）
  · 重測：同一支影片（相同 sha256）評過兩次以上時，兩次總分的相關與平均差
  · 學習成效試題：每題難度（後測答對率）與鑑別度（校正後點二系列相關）
效度
  · 已知組別：groups.csv（欄位 sha256 或 label、group）標「好／壞」等組別，比較組間總分（Cohen's d）
  · 聚斂：規準分與學習成效分的相關
  · 效標：human.csv（欄位 sha256 或 label、human_total，可加 C1–C4）和人工評分的相關與平均絕對誤差
"""
import argparse, csv, glob, json, pathlib, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import INSTRUMENT, load_json
from psychometrics import cohen_d, cronbach_alpha, icc, item_analysis, kripp_alpha, pearson, spearman


def find_reports(paths):
    out = []
    for p in paths:
        p = pathlib.Path(p)
        if p.is_file() and p.name == "report.json":
            out.append(p)
        elif p.is_dir():
            out += [pathlib.Path(x) for x in glob.glob(str(p / "**" / "runs" / "*" / "report.json"), recursive=True)]
    return sorted(set(out))


def rater_matrix(rdir):
    """回傳 {rater: {item: score}}（已套用 score.py 的一致性規則前的原始分數）。"""
    out = {}
    for p in sorted(glob.glob(str(rdir / "panel" / "out" / "rater_*.json"))):
        r = load_json(p)
        out[r.get("rater")] = {x.get("id"): x.get("score") for x in r.get("ratings", [])}
    return out


def read_csv(p):
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def key_of(rep, row):
    return row.get("sha256") == rep.get("sha256") or (row.get("label") and row.get("label") == rep.get("label"))


def f(x, d=2):
    return "—" if x is None else f"{x:.{d}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--human")
    ap.add_argument("--groups")
    ap.add_argument("--out", default="analysis.md")
    a = ap.parse_args()

    reps = [(p, load_json(p)) for p in find_reports(a.paths)]
    if not reps:
        sys.exit("找不到任何 report.json")
    rubric = load_json(INSTRUMENT / "rubric.json")
    L = [f"# 評量工具的信度與效度分析", "", f"共 {len(reps)} 份報告，"
         f"{len({r['sha256'] for _, r in reps})} 支不同影片，{len({r['case_id'] for _, r in reps})} 題。", ""]

    # ── 評分者間信度 ──
    L += ["## 信度", "", "### 評分者間（同一支影片、不同評分者）", "",
          "ICC(2,1) 是「隨便挑一位評分者」的信度；ICC(2,k) 是「k 位平均」的信度。一般 ≥ 0.75 好、0.5–0.75 中等、< 0.5 差（Koo & Li, 2016）。", "",
          "| 構念 | 影片數 | ICC(2,1) | ICC(2,k) | Krippendorff α | Cronbach α |", "|---|---|---|---|---|---|"]
    mats = [(rep, rater_matrix(p.parent)) for p, rep in reps]
    kmin = min((len(m) for _, m in mats if m), default=0)
    for c in rubric["constructs"]:
        ids = [it["id"] for it in c["items"]]
        X, units, A = [], [], []
        for rep, m in mats:
            raters = sorted(m)[:kmin]
            if len(raters) < 2:
                continue
            per = []
            for r in raters:
                v = [m[r].get(i) for i in ids if m[r].get(i) is not None]
                per.append(st.fmean(v) if v else None)
            if all(x is not None for x in per):
                X.append(per)
            units.append(per)
            item_means = []
            for i in ids:
                v = [m[r].get(i) for r in raters if m[r].get(i) is not None]
                item_means.append(st.fmean(v) if v else None)
            if all(x is not None for x in item_means):
                A.append(item_means)
        i1, ik = icc(X) if len(X) >= 2 else (None, None)
        L.append(f"| {c['id']} {c['name']} | {len(X)} | {f(i1)} | {f(ik)} | {f(kripp_alpha(units))} | {f(cronbach_alpha(A))} |")
    tot = [[p["rubric_total"] for p in rep["per_rater"][:kmin]] for rep, _ in reps if len(rep["per_rater"]) >= max(2, kmin)]
    tot = [row for row in tot if all(x is not None for x in row)]
    i1, ik = icc(tot) if len(tot) >= 2 else (None, None)
    L += [f"| 規準總分 | {len(tot)} | {f(i1)} | {f(ik)} | {f(kripp_alpha(tot))} | — |", ""]
    if len(reps) < 5:
        L += [f"> 影片少於 5 支，ICC 與 α 的估計非常不穩定，只當參考。", ""]

    # ── 重測 ──
    by_sha = defaultdict(list)
    for _, rep in reps:
        by_sha[rep["sha256"]].append(rep)
    pairs = [(v[0]["overall"], v[1]["overall"]) for v in by_sha.values() if len(v) >= 2 and v[0]["overall"] is not None and v[1]["overall"] is not None]
    L += ["### 重測（同一支影片重評）", ""]
    if pairs:
        d = [abs(x - y) for x, y in pairs]
        L += [f"{len(pairs)} 支影片評過兩次：相關 r = {f(pearson(*zip(*pairs)))}，總分平均差 {st.fmean(d):.1f} 分（最大 {max(d):.1f}）。", ""]
    else:
        L += ["還沒有同一支影片評兩次的紀錄。在同一個資料夾再跑一次 observe → panel → score 就會有。", ""]

    # ── 試題分析 ──
    L += ["### 學習成效試題", "", "難度 p＝後測答對率（太接近 1 代表題目太簡單、AI 學生本來就會）；鑑別度＝這題答對與否和其他題總分的相關（< 0.2 的題目建議修改）。", ""]
    by_case = defaultdict(list)
    for _, rep in reps:
        for s in rep.get("per_student", []):
            by_case[(rep["case_id"], rep["items_sha256"])].append({r["id"]: r["credit"] for r in s["rows"] if r["role"] != "control"})
    for (cid, h), rows in by_case.items():
        ids = sorted({k for r in rows for k in r})
        R = [[r.get(i, 0) for i in ids] for r in rows]
        if len(R) < 3:
            L.append(f"- {cid}（試題 {h[:8]}）：作答紀錄只有 {len(R)} 筆，至少 3 筆才算。")
            continue
        ia = item_analysis(R)
        L.append(f"- {cid}（試題 {h[:8]}，{len(R)} 筆作答）：" + "、".join(f"{i} p={f(p_)} r={f(rpb)}" for i, (p_, rpb) in zip(ids, ia)))
    L.append("")

    # ── 效度 ──
    L += ["## 效度", ""]
    rub = [rep["rubric"]["mean"] for _, rep in reps]
    lrn = [(rep.get("learning") or {}).get("mean") for _, rep in reps]
    L += ["### 聚斂效度：規準分 vs 學習成效分", "",
          f"Pearson r = {f(pearson(rub, lrn))}，Spearman ρ = {f(spearman(rub, lrn))}（兩種方法量的是相關但不同的東西，預期中度正相關）。", ""]
    if a.groups:
        g = read_csv(a.groups)
        grp = defaultdict(list)
        for _, rep in reps:
            row = next((x for x in g if key_of(rep, x)), None)
            if row:
                grp[row["group"]].append(rep["overall"])
        L += ["### 已知組別（區辨效度）", "", "| 組別 | n | 平均總分 |", "|---|---|---|"] + \
             [f"| {k} | {len(v)} | {f(st.fmean(v), 1)} |" for k, v in grp.items()]
        ks = list(grp)
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                L.append(f"\n{ks[i]} vs {ks[j]}：Cohen's d = {f(cohen_d(grp[ks[i]], grp[ks[j]]))}")
        L.append("")
    if a.human:
        h = read_csv(a.human)
        xs, ys, per_c = [], [], defaultdict(lambda: ([], []))
        for _, rep in reps:
            row = next((x for x in h if key_of(rep, x)), None)
            if row and row.get("human_total"):
                xs.append(rep["overall"])
                ys.append(float(row["human_total"]))
                for c in ("C1", "C2", "C3", "C4"):
                    if row.get(c):
                        per_c[c][0].append(rep["rubric"]["constructs"][c]["mean"])
                        per_c[c][1].append(float(row[c]))
        L += ["### 效標關聯效度：和人工評分比", ""]
        if xs:
            mae = st.fmean(abs(x - y) for x, y in zip(xs, ys))
            L += [f"{len(xs)} 支影片：總分 r = {f(pearson(xs, ys))}、ρ = {f(spearman(xs, ys))}、平均絕對誤差 {mae:.1f} 分。"]
            for c, (x, y) in per_c.items():
                L.append(f"- {c}：r = {f(pearson(x, y))}（n = {len(x)}）")
        else:
            L.append("human.csv 裡沒有對得上的影片。")
        L.append("")
    pathlib.Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n✓ {a.out}")


if __name__ == "__main__":
    main()
