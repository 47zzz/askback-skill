"""共用小工具：ffmpeg、時間格式、中文正規化（比對逐字稿用）。"""
import functools, json, pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTRUMENT = ROOT / "instrument"
PROMPTS = ROOT / "prompts"


def die(msg):
    sys.exit(f"\n✗ {msg}\n")


def _works(exe) -> bool:
    try:
        return subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


@functools.lru_cache(maxsize=None)
def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found and _works(found):
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if _works(exe):
            return exe
    except ImportError:
        pass
    die("找不到能用的 ffmpeg。請先執行：python3 -m pip install imageio-ffmpeg")


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def stamp(s: float) -> str:
    s = max(0.0, float(s))
    return f"{int(s) // 60:02d}:{s % 60:04.1f}"


def parse_stamp(t) -> float | None:
    """'01:12' / '1:12.5' / '72' / 72 → 秒。"""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return float(t)
    m = re.match(r"^\s*(?:(\d+):)?(\d+(?:\.\d+)?)\s*$", str(t))
    if not m:
        return None
    return int(m[1] or 0) * 60 + float(m[2])


def load_json(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def dump_json(obj, p):
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ───────────── 中文正規化：繁簡、數字、標點，比對引文與逐字稿用 ─────────────
DIG = "零一二三四五六七八九"


def _seg(seg: int) -> str:
    units, s, zero = ["", "十", "百", "千"], "", False
    for p in range(3, -1, -1):
        d = (seg // 10 ** p) % 10
        if d == 0:
            zero = bool(s)
        else:
            s += ("零" if zero else "") + DIG[d] + units[p]
            zero = False
    return s


def int_zh(n: int) -> str:
    if n == 0:
        return "零"
    if n < 0:
        return "負" + int_zh(-n)
    groups = []
    while n:
        groups.append(n % 10000)
        n //= 10000
    out, gap = "", False
    for i in reversed(range(len(groups))):
        seg = groups[i]
        if seg == 0:
            gap = bool(out)
            continue
        if out and (gap or seg < 1000):
            out += "零"
        out += _seg(seg) + ["", "萬", "億", "兆"][i]
        gap = False
    return out[1:] if out.startswith("一十") else out


def verbalize_digits(t: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    t = re.sub(r"(\d+)\.(\d+)", lambda m: int_zh(int(m[1])) + "點" + "".join(DIG[int(c)] for c in m[2]), t)
    return re.sub(r"\d+", lambda m: int_zh(int(m[0])), t)


@functools.lru_cache(maxsize=1)
def _s2t():
    try:
        import opencc
        return opencc.OpenCC("s2t").convert
    except Exception:
        return lambda x: x


def to_trad(t: str) -> str:
    return _s2t()(t)


def norm_zh(t: str) -> str:
    """只留中文字、英數；繁體；數字轉中文讀法。"""
    t = to_trad(verbalize_digits(t or ""))
    return re.sub(r"[^\w]", "", t).replace("_", "").lower()


def cer(ref: str, hyp: str) -> float:
    r, h = norm_zh(ref), norm_zh(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(r)


def fuzzy_contains(quote: str, text: str) -> float:
    """引文有多少比例（最長共同子字串 / 引文長度）出現在 text 裡。0–1。"""
    q, s = norm_zh(quote), norm_zh(text)
    if not q:
        return 0.0
    best, prev = 0, [0] * (len(s) + 1)
    for qc in q:
        cur = [0] * (len(s) + 1)
        for j, sc in enumerate(s, 1):
            if qc == sc:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best / len(q)


@functools.lru_cache(maxsize=1)
def _pinyin():
    try:
        from pypinyin import lazy_pinyin
        return lazy_pinyin
    except Exception:
        return None


def has_keyword(text: str, kw: str) -> bool:
    """關鍵詞有沒有出現在 text 裡。裝了 pypinyin 時用不分聲調的拼音比對，容忍語音辨識的同音錯字（會爬／繪爬）。"""
    t, k = norm_zh(text), norm_zh(kw)
    if not k:
        return False
    if k in t:
        return True
    py = _pinyin()
    if not py or len(k) < 2:
        return False
    tp, kp = py(t), py(k)
    return any(tp[i:i + len(kp)] == kp for i in range(len(tp) - len(kp) + 1))
