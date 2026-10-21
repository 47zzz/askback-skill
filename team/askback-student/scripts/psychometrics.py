"""心理計量的基本統計（只用標準函式庫）。

單一影片內（score.py 用）：
    rwg_star        多題量表的評分者一致性 r*wg(J)（Lindell, Brandt & Whitney, 1999；不隨題數膨脹）
    mean_ci         平均數與 95% 信賴區間（t 分配；可設標準差下限，避免少數人剛好同分時區間變成 0）
    hake_gain       正規化學習增益（Hake, 1998）
跨多支影片（analyze.py 用）：
    icc             組內相關 ICC(2,1)、ICC(2,k)：雙因子隨機、絕對一致（同一批評分者評所有對象時用）
    icc1            組內相關 ICC(1,1)、ICC(1,k)：單因子（每個對象的評分者不同時用；Shrout & Fleiss, 1979）
    cronbach_alpha  內部一致性
    kripp_alpha     Krippendorff's alpha（區間尺度，可有缺值）
    item_analysis   試題難度 p 與校正後的點二系列相關（鑑別度）
    pearson, spearman, cohen_d, sem
"""
import math, statistics as st

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
       12: 2.179, 15: 2.131, 20: 2.086, 25: 2.060, 30: 2.042, 60: 2.000}


def t95(df):
    """表上沒有的自由度取「下一個較小」的自由度（t 值較大，區間較保守）。"""
    if df is None or df < 1:
        return float("nan")
    keys = [k for k in sorted(T95) if k <= df]
    if df > 120:
        return 1.96
    return T95[keys[-1]]


def mean_ci(xs, sd_floor=0.0):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None, None, None
    m = st.fmean(xs)
    if len(xs) < 2:
        return m, None, None
    se = max(st.stdev(xs), sd_floor) / math.sqrt(len(xs))
    h = t95(len(xs) - 1) * se
    return m, se, (m - h, m + h)


def rwg_star(ratings, null_var=2.0):
    """r*wg(J) = 1 − 平均題目變異數 / 虛無變異數（Lindell et al., 1999），不像 James 的 rwg(J) 會隨題數膨脹。
    ratings: 每位評分者一列、每題一欄（同一個構念的 J 題）。null 會被略過。
    null_var：完全隨機作答時的變異數；5 點量表的均勻分配是 2，輕度偏態（評分者普遍寬鬆）約 1.34。
    回傳 0–1，習慣上 ≥ 0.7 算一致。"""
    cols = list(zip(*ratings)) if ratings else []
    vs = []
    for c in cols:
        c = [x for x in c if x is not None]
        if len(c) >= 2:
            vs.append(st.variance(c))
    if not vs:
        return None
    return round(max(0.0, 1 - st.fmean(vs) / null_var), 3)


def hake_gain(pre, post):
    if pre is None or post is None:
        return None
    if pre >= 1:
        return None
    return (post - pre) / (1 - pre)


def pearson(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = st.fmean(xs), st.fmean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return sxy / (sx * sy) if sx and sy else None


def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    return pearson(_rank(list(xs)), _rank(list(ys)))


def icc(X):
    """X：n 個評分對象 × k 位評分者，不能有缺值。回傳 (ICC(2,1), ICC(2,k))。"""
    n, k = len(X), len(X[0]) if X else 0
    if n < 2 or k < 2:
        return None, None
    g = st.fmean(v for row in X for v in row)
    rm = [st.fmean(row) for row in X]
    cm = [st.fmean(X[i][j] for i in range(n)) for j in range(k)]
    ssr = k * sum((m - g) ** 2 for m in rm)
    ssc = n * sum((m - g) ** 2 for m in cm)
    sst = sum((v - g) ** 2 for row in X for v in row)
    sse = sst - ssr - ssc
    msr, msc = ssr / (n - 1), ssc / (k - 1)
    mse = sse / ((n - 1) * (k - 1)) if (n - 1) * (k - 1) else 0
    d1 = msr + (k - 1) * mse + k * (msc - mse) / n
    d2 = msr + (msc - mse) / n
    return (round((msr - mse) / d1, 3) if d1 else None, round((msr - mse) / d2, 3) if d2 else None)


def icc1(X):
    """X：n 個對象 × k 個評分（每個對象的 k 位評分者可以不同人），不能有缺值。回傳 (ICC(1,1), ICC(1,k))。"""
    n, k = len(X), len(X[0]) if X else 0
    if n < 2 or k < 2:
        return None, None
    g = st.fmean(v for row in X for v in row)
    rm = [st.fmean(row) for row in X]
    msb = k * sum((m - g) ** 2 for m in rm) / (n - 1)
    msw = sum((v - rm[i]) ** 2 for i, row in enumerate(X) for v in row) / (n * (k - 1))
    d1 = msb + (k - 1) * msw
    return (round((msb - msw) / d1, 3) if d1 else None, round((msb - msw) / msb, 3) if msb else None)


def cronbach_alpha(X):
    """X：n 個對象 × k 題，不能有缺值。"""
    n, k = len(X), len(X[0]) if X else 0
    if n < 3 or k < 2:
        return None
    item_var = sum(st.variance([X[i][j] for i in range(n)]) for j in range(k))
    tot_var = st.variance([sum(row) for row in X])
    return round(k / (k - 1) * (1 - item_var / tot_var), 3) if tot_var else None


def kripp_alpha(units):
    """units：每個評分對象一列，列裡是各評分者的分數（可有 None）。區間尺度。"""
    vals = [[v for v in u if v is not None] for u in units]
    vals = [u for u in vals if len(u) >= 2]
    N = sum(len(u) for u in vals)
    if N < 4:
        return None
    Do = sum(sum((a - b) ** 2 for i, a in enumerate(u) for j, b in enumerate(u) if i != j) / (len(u) - 1) for u in vals) / N
    allv = [v for u in vals for v in u]
    De = sum((a - b) ** 2 for i, a in enumerate(allv) for j, b in enumerate(allv) if i != j) / (N * (N - 1))
    return round(1 - Do / De, 3) if De else None


def item_analysis(R):
    """R：n 位作答者 × k 題的 0/1（或部分給分）矩陣。回傳每題 (難度 p, 校正後點二系列相關)。"""
    n, k = len(R), len(R[0]) if R else 0
    out = []
    for j in range(k):
        col = [R[i][j] for i in range(n)]
        rest = [sum(R[i]) - R[i][j] for i in range(n)]
        out.append((round(st.fmean(col), 3) if n else None, pearson(col, rest)))
    return out


def cohen_d(a, b):
    a, b = [x for x in a if x is not None], [x for x in b if x is not None]
    if len(a) < 2 or len(b) < 2:
        return None
    sp = math.sqrt(((len(a) - 1) * st.variance(a) + (len(b) - 1) * st.variance(b)) / (len(a) + len(b) - 2))
    return (st.fmean(a) - st.fmean(b)) / sp if sp else None


def sem(sd, reliability):
    if sd is None or reliability is None:
        return None
    return sd * math.sqrt(max(0.0, 1 - reliability))
