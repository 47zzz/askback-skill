#!/usr/bin/env python3
"""做：lesson.json → 一支有畫面、有語音、有字幕的補充教學影片。

    python3 scripts/render.py [work/lesson.json] [--out output/askback.mp4]

一句旁白（beat）＝一個畫面動作。時間軸完全由實際語音長度決定，改一句不會讓其他句錯位。
流程：驗算 checks → 逐句 TTS（有快取）→ 逐格繪圖 → ffmpeg 合成 → 檢查長度 → 輸出 preview.jpg。
lesson.json 的寫法見 SKILL.md；可直接參考 examples/。
"""
import argparse, ast, asyncio, hashlib, json, math, operator, os, pathlib, re, shutil, subprocess, sys, wave
from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
W, H, FPS = 1280, 720, 20
SUB_H = 104                      # 底部字幕帶
BADGE = "AI 生成教學示範，非原講者本人"
MIN_S, MAX_S = 30, 90

FORMS = {  # 四種形式：跟著原片選，不要每題都用同一種
    "board": dict(bg="#000000", ink="#ffffff", font="hand", reveal="write", size=50,
                  colors=dict(w="#ffffff", y="#ffd94a", g="#5fd36a", b="#4fb0ff", p="#e065ff", r="#ff6b5e", o="#ff9f43")),
    "slides": dict(bg="#f6f5f0", ink="#222831", font="sans", reveal="fade", size=44,
                   colors=dict(w="#222831", y="#b7791f", g="#2f855a", b="#2b6cb0", p="#6b46c1", r="#c53030", o="#c05621")),
    "story": dict(bg="#fff4dc", ink="#3b2f2f", font="hand", reveal="pop", size=42,
                  colors=dict(w="#3b2f2f", y="#b7791f", g="#2f855a", b="#2b6cb0", p="#805ad5", r="#c53030", o="#dd6b20")),
    "lab": dict(bg="#14171c", ink="#f2f2f2", font="sans", reveal="fade", size=44,
                colors=dict(w="#f2f2f2", y="#ffd94a", g="#6ee7a0", b="#63b3ed", p="#d6a4ff", r="#ff7b72", o="#ffab5e")),
}
FONT_FILES = {"hand": "Iansui-Regular.ttf", "sans": "NotoSansTC.ttf"}
VOICES = ["zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-TW-HsiaoYuNeural"]


def die(msg):
    sys.exit(f"\n✗ {msg}\n")


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        die("找不到 ffmpeg。請先執行：python3 -m pip install imageio-ffmpeg")


# ───────────────────────── 驗算：能算的就真的算 ─────────────────────────
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Eq: operator.eq, ast.NotEq: operator.ne,
        ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
_FUNCS = dict(sqrt=math.sqrt, abs=abs, round=round, min=min, max=max, sum=sum, gcd=math.gcd,
              isclose=lambda a, b, tol=1e-9: math.isclose(a, b, rel_tol=tol, abs_tol=tol),
              F=Fraction, pi=math.pi, sin=math.sin, cos=math.cos, tan=math.tan, log=math.log,
              log10=math.log10, exp=math.exp, radians=math.radians)


def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float, bool)):
        return n.value
    if isinstance(n, ast.BinOp):
        return _OPS[type(n.op)](_ev(n.left), _ev(n.right))
    if isinstance(n, ast.UnaryOp):
        return _OPS[type(n.op)](_ev(n.operand))
    if isinstance(n, ast.BoolOp):
        vals = [_ev(v) for v in n.values]
        return all(vals) if isinstance(n.op, ast.And) else any(vals)
    if isinstance(n, ast.Compare):
        left = _ev(n.left)
        for op, right in zip(n.ops, n.comparators):
            right = _ev(right)
            if not _OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(n, ast.Name) and n.id in _FUNCS:
        return _FUNCS[n.id]
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
        return _FUNCS[n.func.id](*[_ev(x) for x in n.args])
    if isinstance(n, (ast.List, ast.Tuple)):
        return [_ev(x) for x in n.elts]
    raise ValueError("只支援數字運算與比較")


def run_checks(checks):
    bad = []
    for c in checks or []:
        try:
            ok = _ev(ast.parse(c, mode="eval").body) is True
        except Exception as e:
            ok, c = False, f"{c}   ← 無法計算：{e}"
        print(f"  {'✓' if ok else '✗'} {c}")
        if not ok:
            bad.append(c)
    if bad:
        die("驗算沒過，影片不會產生。先把內容改對，再重跑：\n  " + "\n  ".join(bad))


# ───────────────────────── 語音：逐句產生，有快取 ─────────────────────────
def _to_wav(src, dst):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1", "-ar", "44100",
                    "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                           "silenceremove=start_periods=1:start_threshold=-50dB,areverse",
                    "-c:a", "pcm_s16le", str(dst)], check=True)


def _edge(text, voice, rate, tmp):
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice, rate=rate).save(str(tmp))
    for attempt in range(3):
        try:
            asyncio.run(asyncio.wait_for(go(), 40))
            if tmp.exists() and tmp.stat().st_size > 500:
                return
        except Exception:
            if attempt == 2:
                raise
    raise RuntimeError("edge-tts 沒有回傳音訊")


def _openai(text, voice, rate, tmp):
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("沒有 OPENAI_API_KEY")
    from openai import OpenAI
    v = "onyx" if "Yun" in voice else "nova"
    with OpenAI().audio.speech.with_streaming_response.create(
            model=os.environ.get("ASKBACK_OPENAI_TTS", "gpt-4o-mini-tts"), voice=v, input=text,
            instructions="用臺灣華語、親切清楚的老師語氣朗讀。") as r:
        r.stream_to_file(str(tmp))


def _gtts(text, voice, rate, tmp):
    from gtts import gTTS
    gTTS(text, lang="zh-TW").save(str(tmp))


def _say(text, voice, rate, tmp):
    aiff = tmp.with_suffix(".aiff")
    if shutil.which("say"):
        subprocess.run(["say", "-v", "Meijia", "-o", str(aiff), text], check=True)
    elif shutil.which("espeak-ng"):
        aiff = tmp.with_suffix(".wav")
        subprocess.run(["espeak-ng", "-v", "cmn", "-s", "150", "-w", str(aiff), text], check=True)
    else:
        raise RuntimeError("沒有系統語音")
    shutil.move(str(aiff), str(tmp))


def speak(text, voice, rate, cache):
    """回傳 wav 路徑。同一句、同一個聲音只合成一次。"""
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    wav = cache / f"{key}.wav"
    if wav.exists():
        return wav
    errors = []
    for name, fn, ext in [("edge-tts", _edge, ".mp3"), ("openai", _openai, ".mp3"),
                          ("gtts", _gtts, ".mp3"), ("system", _say, ".snd")]:
        tmp = cache / f"{key}_raw{ext}"
        try:
            fn(text, voice, rate, tmp)
            _to_wav(tmp, wav)
            tmp.unlink(missing_ok=True)
            return wav
        except Exception as e:
            errors.append(f"{name}: {e}")
    die("所有語音服務都失敗。可改用環境裡任何 TTS 產生這一句的 wav，存成\n  "
        f"{wav}\n再重跑（render.py 會直接採用）。\n句子：{text}\n" + "\n".join(errors))


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


# ───────────────────────── 文字：顏色、圈、上下標、分數 ─────────────────────────
class Fonts:
    def __init__(self, primary):
        self.paths = [ROOT / "fonts" / FONT_FILES[primary]] + [ROOT / "fonts" / f for k, f in FONT_FILES.items() if k != primary]
        for p in self.paths:
            if not p.exists():
                die(f"缺少字型 {p}（應隨 repo 一起下載）")
        self.cache, self.cmaps = {}, []
        try:
            from fontTools.ttLib import TTFont
            self.cmaps = [set(TTFont(str(p), lazy=True).getBestCmap()) for p in self.paths]
        except Exception:
            pass

    def get(self, size, ch="中", bold=False):
        idx = 0
        if self.cmaps and ord(ch) not in self.cmaps[0]:
            idx = next((i for i, c in enumerate(self.cmaps) if ord(ch) in c), 0)
        key = (idx, int(size), bold)
        if key not in self.cache:
            f = ImageFont.truetype(str(self.paths[idx]), int(size))
            if "NotoSansTC" in self.paths[idx].name:
                try:
                    f.set_variation_by_axes([700 if bold else 500])
                except Exception:
                    pass
            self.cache[key] = f
        return self.cache[key]


SUPSUB = {**{c: ("^", d) for c, d in zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+−")},
          **{c: ("_", d) for c, d in zip("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+−")}}


def parse(text, color="w"):
    """標記 → [(字串, 屬性)]。屬性：color、mark('!'圈 '~'劃掉 '_'底線)、pos('^' '_' '')、frac=(分子, 分母)"""
    runs, i, n = [], 0, len(text)

    def add(s, **kw):
        if s:
            runs.append((s, dict(dict(color=color, mark="", pos=""), **kw)))

    def braced(j):
        depth, k = 0, j
        while k < n:
            depth += text[k] == "{"
            depth -= text[k] == "}"
            if depth == 0:
                return text[j + 1:k], k + 1
            k += 1
        return text[j + 1:], n

    buf = ""
    while i < n:
        c = text[i]
        if c in SUPSUB:
            add(buf); buf = ""
            add(SUPSUB[c][1], pos=SUPSUB[c][0]); i += 1
        elif c in "^_" and i + 1 < n and (text[i + 1] == "{" or text[i + 1].isalnum() or text[i + 1] in "+−-"):
            add(buf); buf = ""
            if text[i + 1] == "{":
                inner, i2 = braced(i + 1)
            else:
                inner, i2 = text[i + 1], i + 2
            add(inner, pos=c); i = i2
        elif c == "{":
            add(buf); buf = ""
            inner, i = braced(i)
            if "|" in inner and re.fullmatch(r"[a-z#0-9A-F]*[!~_]*", inner.split("|", 1)[0]):
                head, body = inner.split("|", 1)
                col = re.sub(r"[!~_]", "", head) or color
                mark = "".join(ch for ch in head if ch in "!~_")
                gid = object()
                for s, at in parse(body, col):
                    at["mark"], at["gid"] = mark, gid
                    runs.append((s, at))
            elif "//" in inner:
                a, b = inner.split("//", 1)
                runs.append(("", dict(color=color, mark="", pos="", frac=(a, b))))
            else:
                buf += "{" + inner + "}"
        else:
            buf += c; i += 1
    add(buf)
    return runs


class Line:
    """一行排好版的文字。可以只畫前 k 個字（板書逐字出現）。"""

    def __init__(self, text, size, fonts, palette, color="w", bold=False):
        self.size, self.glyphs, x = size, [], 0.0
        for s, at in parse(text, color):
            col = palette.get(at["color"], at["color"] if at["color"].startswith("#") else palette["w"])
            if at.get("frac"):
                fs = size * 0.8
                num = Line(at["frac"][0], fs, fonts, palette, at["color"], bold)
                den = Line(at["frac"][1], fs, fonts, palette, at["color"], bold)
                w = max(num.width, den.width) + 10
                self.glyphs.append(dict(kind="frac", x=x, w=w, num=num, den=den, color=col, mark=at["mark"], gid=at.get("gid")))
                x += w + 4
                continue
            fs = size * (0.6 if at["pos"] else 1.0)
            dy = -size * 0.30 if at["pos"] == "^" else (size * 0.34 if at["pos"] == "_" else 0)
            for ch in s:
                f = fonts.get(fs, ch, bold)
                w = f.getlength(ch)
                self.glyphs.append(dict(kind="ch", ch=ch, x=x, w=w, font=f, dy=dy, color=col,
                                        mark=at["mark"], gid=at.get("gid")))
                x += w
        self.width = x
        self.tall = any(g["kind"] == "frac" for g in self.glyphs)
        self.advance = size * (2.1 if self.tall else 1.5)

    def draw(self, d, x0, y0, upto=None, alpha_color=None):
        if self.tall:
            y0 += self.size * 0.3
        shown = self.glyphs if upto is None else self.glyphs[:max(0, int(upto))]
        for g in shown:
            col = alpha_color or g["color"]
            if g["kind"] == "frac":
                cx = x0 + g["x"] + g["w"] / 2
                g["num"].draw(d, cx - g["num"].width / 2, y0 - self.size * 0.42, alpha_color=alpha_color)
                g["den"].draw(d, cx - g["den"].width / 2, y0 + self.size * 0.62, alpha_color=alpha_color)
                yb = y0 + self.size * 0.66
                d.line([x0 + g["x"] + 2, yb, x0 + g["x"] + g["w"] - 2, yb], fill=col, width=max(2, int(self.size / 18)))
            else:
                d.text((x0 + g["x"], y0 + g["dy"]), g["ch"], font=g["font"], fill=col)
        # 圈、劃掉、底線：同一段標記整段都出現後才畫
        groups = {}
        for i, g in enumerate(self.glyphs):
            if g["mark"] and g.get("gid") is not None:
                groups.setdefault(id(g["gid"]), []).append(i)
        for idx in groups.values():
            if idx[-1] >= len(shown):
                continue
            a, b = self.glyphs[idx[0]], self.glyphs[idx[-1]]
            x1, x2 = x0 + a["x"], x0 + b["x"] + b["w"]
            col, wd = alpha_color or a["color"], max(3, int(self.size / 14))
            top, bot = y0 + self.size * 0.08, y0 + self.size * 1.22
            if any(self.glyphs[i]["kind"] == "frac" for i in idx):
                top, bot = top - self.size * 0.5, bot + self.size * 0.5
            if "!" in a["mark"]:
                d.ellipse([x1 - 14, top - 6, x2 + 14, bot + 6], outline=col, width=wd)
            if "~" in a["mark"]:
                d.line([x1 - 4, y0 + self.size * 0.70, x2 + 4, y0 + self.size * 0.60], fill=col, width=wd)
            if "_" in a["mark"]:
                d.line([x1, bot, x2, bot], fill=col, width=wd)


def mix(c1, c2, t):
    a, b = (Image.new("RGB", (1, 1), c).getpixel((0, 0)) for c in (c1, c2))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def wrap_plain(text, font, max_w):
    """字幕最多兩行；要換行就在最靠近中間的標點後面換，不留孤字。"""
    if font.getlength(text) <= max_w:
        return [text]
    mid = len(text) / 2
    cuts = [i + 1 for i, ch in enumerate(text[:-1]) if ch in "，。；：！？、,;:!? "]
    cuts = [c for c in cuts if font.getlength(text[:c]) <= max_w and font.getlength(text[c:]) <= max_w]
    c = min(cuts, key=lambda c: abs(c - mid)) if cuts else math.ceil(mid)
    return [text[:c].strip(), text[c:].strip()]


# ───────────────────────── 畫面 ─────────────────────────
class Scene:
    def __init__(self, spec, style, fonts, base_dir):
        self.spec, self.style, self.fonts = spec, style, fonts
        self.title = spec.get("title")
        self.layout = spec.get("layout", "text")
        self.cols = int(spec.get("cols", 1))
        top = 120 if self.title else 64
        area = [56, top, W - 56, H - SUB_H - 24]
        self.text_rect = self.fig_rect = None
        fig = spec.get("figure") or {}
        if self.layout == "text":
            self.text_rect = area
        elif self.layout == "figure":
            self.fig_rect = [area[0], area[1], area[2], area[3] - 84]
            self.text_rect = [area[0], area[3] - 76, area[2], area[3]]
        else:
            split = area[0] + (area[2] - area[0]) * float(spec.get("split", 0.52))
            left, right = [area[0], area[1], split - 18, area[3]], [split + 18, area[1], area[2], area[3]]
            self.fig_rect, self.text_rect = (left, right) if self.layout == "figure-left" else (right, left)
        self.img = None
        if fig.get("image"):
            p = pathlib.Path(fig["image"])
            p = p if p.is_absolute() or p.exists() else base_dir / p
            if not p.exists():
                die(f"找不到圖片：{fig['image']}")
            im = Image.open(p).convert("RGB")
            if fig.get("crop"):
                x1, y1, x2, y2 = fig["crop"]
                im = im.crop((int(x1 * im.width), int(y1 * im.height), int(x2 * im.width), int(y2 * im.height)))
            fw, fh = self.fig_rect[2] - self.fig_rect[0], self.fig_rect[3] - self.fig_rect[1]
            k = min(fw / im.width, fh / im.height)
            self.img = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
        if self.fig_rect:
            fw, fh = self.fig_rect[2] - self.fig_rect[0], self.fig_rect[3] - self.fig_rect[1]
            iw, ih = self.img.size if self.img else (fw, fh)
            ox, oy = self.fig_rect[0] + (fw - iw) / 2, self.fig_rect[1] + (fh - ih) / 2
            self.fig_box = [ox, oy, ox + iw, oy + ih]
        self.items = []          # (beat_index, kind, payload)

    def add(self, bi, show):
        for it in show or []:
            if it.get("clear"):
                self.items.append((bi, "clear", it))
            elif "line" in it:
                self.items.append((bi, "line", it))
            elif "shape" in it:
                if not self.fig_rect:
                    die(f"第 {bi + 1} 句用了 shape，但這個 scene 的 layout 沒有圖區（改用 figure / figure-left / figure-right）")
                self.items.append((bi, "shape", it))
            else:
                die(f"第 {bi + 1} 句的 show 看不懂：{it}")

    def fit(self):
        """算出字級：同一個 scene 所有行用同一個基準字級，放不下就一起縮。"""
        x1, y1, x2, y2 = self.text_rect
        col_w = (x2 - x1 - (self.cols - 1) * 48) / self.cols
        base = float(self.style["size"]) * float(self.spec.get("size", 1.0))
        story = self.style["form"] == "story"
        while base >= 22:
            most, cur, ok = {}, {}, True
            for bi, kind, it in self.items:
                if kind == "clear":
                    cur = {}
                elif kind == "line":
                    c = int(it.get("col", 1))
                    sz = base * float(it.get("size", 1.0))
                    ln = Line(it["line"], sz, self.fonts, self.style["colors"], it.get("color", "w"), it.get("bold", False))
                    if ln.width > col_w - (64 if story else 0):
                        ok = False
                    cur[c] = cur.get(c, 0) + (sz * 1.9 if story else ln.advance)
                    most[c] = max(most.get(c, 0), cur[c])
            if ok and all(v <= (y2 - y1) + 4 for v in most.values()):
                break
            base -= 2
        else:
            die(f"「{self.title or self.layout}」這個畫面塞不下。少放一點：刪字、用 {{\"clear\": true}} 擦掉舊內容，或拆成兩個 scene。")
        self.base, self.col_w = base, col_w

    def paint(self, bi, prog):
        """畫出第 bi 句進行到 prog（0–1）時的畫面。"""
        st = self.style
        im = Image.new("RGB", (W, H), st["bg"])
        d = ImageDraw.Draw(im)
        pal, ink = st["colors"], st["ink"]
        if self.title:
            sz = 46
            while sz > 26 and Line(self.title, sz, self.fonts, pal, bold=True).width > W - 56 - 360:
                sz -= 2
            Line(self.title, sz, self.fonts, pal, st.get("title_color", "y"), bold=True).draw(d, 56, 40 + (46 - sz) / 2)
            if st["form"] == "slides":
                d.line([56, 106, W - 56, 106], fill=mix(st["bg"], ink, 0.25), width=3)
        if self.fig_rect:
            bx = self.fig_box
            if self.img:
                im.paste(self.img, (int(bx[0]), int(bx[1])))
                d.rectangle([bx[0] - 2, bx[1] - 2, bx[2] + 1, bx[3] + 1], outline=mix(st["bg"], ink, 0.35), width=2)
        x1, y1, x2, y2 = self.text_rect
        if self.cols == 2 and st["form"] == "board":
            mx = x1 + self.col_w + 24
            d.line([mx, y1, mx, y2], fill=mix(st["bg"], pal["b"], 0.7), width=3)
        # 找出最後一次 clear 之後、到目前為止的項目
        start = 0
        for k, (b, kind, it) in enumerate(self.items):
            if kind == "clear" and b <= bi:
                start = k + 1
        ys, story_side = {}, {}
        for b, kind, it in self.items[start:]:
            if b > bi:
                break
            p = 1.0 if b < bi else prog
            if kind == "line":
                c = int(it.get("col", 1))
                sz = self.base * float(it.get("size", 1.0))
                ln = Line(it["line"], sz, self.fonts, pal, it.get("color", "w"), it.get("bold", False))
                cx = x1 + (c - 1) * (self.col_w + 48)
                y = ys.get(c, y1)
                story = st["form"] == "story"
                ys[c] = y + (sz * 1.9 if story else ln.advance)
                x = cx + float(it.get("indent", 0)) * sz
                if it.get("align") == "center" or self.layout == "figure":
                    x = cx + (self.col_w - ln.width) / 2
                if story:
                    who = it.get("who", "")
                    side = story_side.setdefault(who, len(story_side) % 2)
                    bw = ln.width + 56
                    bx0 = cx if side == 0 else cx + self.col_w - bw
                    if p > 0.05:
                        fillc = mix(st["bg"], pal["b" if side == 0 else "o"], 0.22)
                        d.rounded_rectangle([bx0, y, bx0 + bw, y + sz * 1.6], radius=22, fill=fillc,
                                            outline=mix(st["bg"], ink, 0.5), width=2)
                        ln.draw(d, bx0 + 28, y + sz * 0.12)
                    continue
                if st["reveal"] == "write":
                    ln.draw(d, x, y, upto=None if p >= 1 else math.ceil(len(ln.glyphs) * p))
                elif p >= 1:
                    ln.draw(d, x, y)
                elif p > 0:
                    layer = Image.new("RGB", (W, H), st["bg"])
                    Line.draw(ln, ImageDraw.Draw(layer), x, y)
                    box = (int(x - 20), int(y - 10), min(W, int(x + ln.width + 24)), int(y + sz * 1.5))
                    im.paste(Image.blend(im.crop(box), layer.crop(box), p), box)
                    d = ImageDraw.Draw(im)
            elif kind == "shape":
                self.shape(d, it, p)
        return im

    def shape(self, d, it, p):
        bx = self.fig_box
        fw, fh = bx[2] - bx[0], bx[3] - bx[1]
        P = lambda xy: (bx[0] + xy[0] * fw, bx[1] + xy[1] * fh)
        pal = self.style["colors"]
        col = pal.get(it.get("color", "y"), it.get("color", "y"))
        wd = int(it.get("width", 5))
        k = it["shape"]
        if p <= 0:
            return
        if k == "circle":
            cx, cy = P(it["at"]); r = float(it.get("r", 0.08)) * fw
            ry = float(it["ry"]) * fh if "ry" in it else r
            d.arc([cx - r, cy - ry, cx + r, cy + ry], -90, -90 + 360 * p, fill=col, width=wd)
        elif k == "box":
            x1, y1 = P(it["at"][:2]); x2, y2 = P(it["at"][2:])
            if it.get("fill"):
                d.rectangle([x1, y1, x2, y2], fill=pal.get(it["fill"], it["fill"]))
            d.rectangle([x1, y1, x2, y2], outline=col, width=wd)
        elif k in ("line", "arrow"):
            (x1, y1), (x2, y2) = P(it["from"]), P(it["to"])
            x2, y2 = x1 + (x2 - x1) * p, y1 + (y2 - y1) * p
            if it.get("dash"):
                n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 18))
                for s in range(0, n, 2):
                    d.line([x1 + (x2 - x1) * s / n, y1 + (y2 - y1) * s / n,
                            x1 + (x2 - x1) * (s + 1) / n, y1 + (y2 - y1) * (s + 1) / n], fill=col, width=wd)
            else:
                d.line([x1, y1, x2, y2], fill=col, width=wd)
            if k == "arrow" and p > 0.3:
                ang, L = math.atan2(y2 - y1, x2 - x1), 9 + wd * 2.6
                d.polygon([(x2 + math.cos(ang) * L * 0.6, y2 + math.sin(ang) * L * 0.6),
                           (x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45)),
                           (x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))], fill=col)
        elif k == "polygon":
            pts = [P(q) for q in it["points"]]
            if it.get("fill"):
                d.polygon(pts, fill=mix(self.style["bg"], pal.get(it["fill"], it["fill"]), 0.55 * p))
            d.line(pts + [pts[0]], fill=col, width=wd, joint="curve")
        elif k == "dot":
            cx, cy = P(it["at"]); r = float(it.get("r", 0.012)) * fw
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
        elif k == "label":
            sz = float(it.get("size", 0.8)) * self.style["size"]
            ln = Line(it["text"], sz, self.fonts, pal, it.get("color", "y"), bold=True)
            cx, cy = P(it["at"])
            x, y = cx - ln.width / 2, cy - sz * 0.7
            if self.img or it.get("plate"):
                d.rounded_rectangle([x - 10, y - 2, x + ln.width + 10, y + sz * 1.4], radius=8, fill=self.style["bg"])
            ln.draw(d, x, y)
        else:
            die(f"不認得的 shape：{k}（可用 circle box line arrow polygon dot label）")


def chrome(im, style, fonts, sub, who, credit):
    """每一格都有的東西：AI 標示、字幕、來源。"""
    d = ImageDraw.Draw(im)
    bg, ink = style["bg"], style["ink"]
    f = fonts.get(20, bold=True)
    tw = f.getlength(BADGE)
    d.rounded_rectangle([W - tw - 44, 14, W - 16, 50], radius=18, fill=mix(bg, ink, 0.14), outline=mix(bg, ink, 0.45))
    d.text((W - tw - 30, 19), BADGE, font=f, fill=ink)
    d.rectangle([0, H - SUB_H, W, H], fill=mix(bg, "#000000", 0.55) if style["form"] != "slides" else mix(bg, ink, 0.9))
    if sub:
        f = fonts.get(32, bold=True)
        text = (f"{who}：" if who else "") + sub
        lines = wrap_plain(text, f, W - 120)[:2]
        y = H - SUB_H + (SUB_H - 22 - len(lines) * 40) / 2
        for row in lines:
            ln = Line(row, 32, fonts, {"w": "#ffffff"}, bold=True)
            ln.draw(d, (W - ln.width) / 2, y)
            y += 40
    if credit:
        f = fonts.get(14)
        d.text((16, H - 20), credit, font=f, fill="#9aa0a6")
    return im


# ───────────────────────── 主流程 ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lesson", nargs="?", default="work/lesson.json")
    ap.add_argument("--out", default="output/askback.mp4")
    ap.add_argument("--work", default="work")
    a = ap.parse_args()
    lesson_path = pathlib.Path(a.lesson)
    try:
        L = json.loads(lesson_path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"讀不了 {lesson_path}：{e}")
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache = pathlib.Path(a.work) / "audio"
    cache.mkdir(parents=True, exist_ok=True)

    st = dict(L.get("style") or {})
    form = st.get("form", "board")
    if form not in FORMS:
        die(f"style.form 要是 {' / '.join(FORMS)} 其中之一")
    style = dict(FORMS[form], form=form)
    style["colors"] = dict(FORMS[form]["colors"], **(st.get("colors") or {}))
    for k in ("bg", "ink", "font", "reveal", "size", "title_color"):
        if k in st:
            style[k] = st[k]
    style["colors"]["w"] = st.get("ink", style["colors"]["w"])
    fonts, ui = Fonts(style["font"]), Fonts("sans")
    rate, gap = st.get("rate", "+0%"), float(st.get("gap", 0.35))
    voices = st.get("voices") or {}
    default_voice = st.get("voice", VOICES[0])

    print("① 驗算")
    run_checks(L.get("checks"))
    if not L.get("checks"):
        print("  （沒有 checks。只有完全沒有數字或計算的題目才可以省略。）")

    scenes, beats = [], []
    for si, sc in enumerate(L.get("scenes") or []):
        s = Scene(sc, style, fonts, lesson_path.parent)
        for b in sc.get("beats") or []:
            if not (b.get("say") or "").strip():
                die("每個 beat 都要有 say（要唸出來的那一句）")
            s.add(len(beats), b.get("show"))
            beats.append(dict(b, scene=s))
        s.fit()
        scenes.append(s)
    if not beats:
        die("lesson.json 裡沒有任何 beat")

    print(f"② 語音（{len(beats)} 句）")
    seen = {}
    for b in beats:
        who = b.get("who", "")
        if who not in voices and who not in seen:
            seen[who] = VOICES[len(seen) % len(VOICES)] if who else default_voice
        b["voice"] = voices.get(who, seen.get(who, default_voice))
        b["wav"] = speak(b["say"], b["voice"], b.get("rate", rate), cache)
        b["dur"] = wav_len(b["wav"])
    lead, tail, t = 0.5, 1.0, 0.5
    for b in beats:
        b["start"], b["hold"] = t, b["dur"] + gap + float(b.get("pause", 0))
        t += b["hold"]
    total = t - gap + tail
    chars = sum(len(re.sub(r"[\s，。、？！：；,.?!]", "", b["say"])) for b in beats)
    for i, b in enumerate(beats, 1):
        if len(b.get("sub", b["say"])) > 44:
            print(f"  ⚠ 第 {i} 句字幕 {len(b.get('sub', b['say']))} 字，太長。一句只說一件事：拆成兩句，或用 sub 寫短一點。")
    print(f"  旁白 {chars} 字，預計 {total:.1f} 秒")

    # 聲音：一條 wav，精確對齊
    audio = pathlib.Path(a.work) / "narration.wav"
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        pos = 0
        for b in beats:
            want = int(b["start"] * 44100)
            w.writeframes(b"\0\0" * (want - pos)); pos = want
            with wave.open(str(b["wav"])) as r:
                data = r.readframes(r.getnframes())
            w.writeframes(data); pos += len(data) // 2
        w.writeframes(b"\0\0" * max(0, int(total * 44100) - pos))

    print("③ 畫面與合成")
    credit = L.get("credit") or "教材來源：均一教育平台 Junyi Academy，CC BY-NC-SA 3.0 TW"
    proc = subprocess.Popen(
        [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(audio),
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", str(out)],
        stdin=subprocess.PIPE)
    n_frames, bi, stills = int(total * FPS), 0, []
    last_key, last_bytes = None, None
    for fi in range(n_frames):
        ts = fi / FPS
        while bi + 1 < len(beats) and ts >= beats[bi + 1]["start"]:
            bi += 1
        b = beats[bi]
        local = ts - b["start"]
        if local < 0:
            prog, sub = 0.0, None
        else:
            window = min(max(0.5, b["dur"] * 0.55), 2.4)
            prog = min(1.0, local / window)
            sub = b.get("sub", b["say"]) if local < b["dur"] + 0.25 else None
        key = (bi, round(prog, 3) if prog < 1 else 1, sub)
        if key != last_key:
            im = b["scene"].paint(bi, prog)
            chrome(im, style, ui, sub, b.get("who"), credit)
            last_key, last_bytes = key, im.tobytes()
            if prog >= 1 and (not stills or stills[-1][0] != bi):
                stills.append((bi, im.copy()))
        proc.stdin.write(last_bytes)
    proc.stdin.close()
    if proc.wait() != 0 or not out.exists():
        die("ffmpeg 合成失敗")

    # 預覽：每句講完時的畫面，供回頭檢查
    cols = 3
    rows = math.ceil(len(stills) / cols)
    sheet = Image.new("RGB", (cols * 640, rows * 360), "#202020")
    d = ImageDraw.Draw(sheet)
    for i, (k, im) in enumerate(stills):
        x, y = (i % cols) * 640, (i // cols) * 360
        sheet.paste(im.resize((640, 360), Image.LANCZOS), (x, y))
        d.rectangle([x, y, x + 46, y + 26], fill="#d00000")
        d.text((x + 8, y + 2), f"{k + 1}", font=ui.get(18, bold=True), fill="white")
    preview = out.with_name("preview.jpg")
    sheet.save(preview, quality=85)
    shutil.copy(lesson_path, out.with_name("lesson.json"))

    ok = MIN_S <= total <= MAX_S
    print(f"\n{'✓' if ok else '✗'} {out}　{total:.1f} 秒　{len(beats)} 句　{chars} 字")
    print(f"  預覽：{preview}（請打開看：有沒有字疊在一起、跑出畫面、圈錯地方）")
    if not ok:
        per = total / max(chars, 1)
        if total > MAX_S:
            print(f"✗ 超過 {MAX_S} 秒。大約要刪掉 {math.ceil((total - 80) / per)} 個字——刪掉最不必要的那一句，而不是每句都縮一點。")
        else:
            print(f"✗ 不到 {MIN_S} 秒。大約要再多 {math.ceil((38 - total) / per)} 個字——補上學生最需要的那一步，而不是加客套話。")
        sys.exit(2)


if __name__ == "__main__":
    main()
