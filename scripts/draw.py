"""畫面：lesson.json 的文字標記、排版、四種形式的畫法（Pillow 引擎），以及給 Remotion 引擎用的排版資料。
render.py 會用；單獨不執行。
"""
import math, pathlib, re, sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT, die

W, H = 1280, 720
SUB_H = 104                      # 底部字幕帶
BADGE = "AI 生成教學示範，非原講者本人"

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


_GID = [0]


def next_gid():
    _GID[0] += 1
    return _GID[0]


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
                gid = next_gid()
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
                groups.setdefault(g["gid"], []).append(i)
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




# ───────────────────────── 給 Remotion 引擎的排版資料 ─────────────────────────
def runs_json(text, palette, color="w"):
    """標記文字 → 可序列化的 runs（顏色已換成 hex）。分數會帶 num/den 兩組 runs。"""
    out = []
    for s, at in parse(text, color):
        col = at["color"]
        col = palette.get(col, col if col.startswith("#") else palette["w"])
        r = dict(s=s, color=col, mark=at["mark"], pos=at["pos"], gid=at.get("gid"))
        if at.get("frac"):
            r["frac"] = dict(num=runs_json(at["frac"][0], palette, at["color"]),
                             den=runs_json(at["frac"][1], palette, at["color"]))
        out.append(r)
    return out


def scene_props(sc: "Scene", base_dir: pathlib.Path, images: dict) -> dict:
    """Scene（fit() 之後）→ Remotion 要的資料。images：{原路徑: public 相對路徑}，由 render.py 複製。"""
    pal = sc.style["colors"]
    items = []
    for bi, kind, it in sc.items:
        if kind == "clear":
            items.append(dict(beat=bi, kind="clear"))
        elif kind == "line":
            sz = sc.base * float(it.get("size", 1.0))
            ln = Line(it["line"], sz, sc.fonts, pal, it.get("color", "w"), it.get("bold", False))
            items.append(dict(beat=bi, kind="line", runs=runs_json(it["line"], pal, it.get("color", "w")), size=sz,
                              width=ln.width, tall=ln.tall, advance=ln.advance, col=int(it.get("col", 1)),
                              indent=float(it.get("indent", 0)), align=it.get("align"), who=it.get("who", ""),
                              bold=bool(it.get("bold", False))))
        else:
            d = dict(it)
            d["color"] = pal.get(it.get("color", "y"), it.get("color", "y"))
            if d.get("fill"):
                d["fill"] = pal.get(d["fill"], d["fill"])
            if it["shape"] == "label":
                d["runs"] = runs_json(it["text"], pal, it.get("color", "y"))
                d["size"] = float(it.get("size", 0.8)) * sc.style["size"]
            items.append(dict(beat=bi, kind="shape", **d))
    fig = sc.spec.get("figure") or {}
    img = None
    if fig.get("image"):
        p = pathlib.Path(fig["image"])
        p = p if p.is_absolute() or p.exists() else base_dir / p
        img = images.get(str(p))
    title = None
    if sc.title:
        sz = 46
        while sz > 26 and Line(sc.title, sz, sc.fonts, pal, bold=True).width > W - 56 - 360:
            sz -= 2
        title = dict(runs=runs_json(sc.title, pal, sc.style.get("title_color", "y")), size=sz)
    return dict(title=title, layout=sc.layout, cols=sc.cols, text_rect=sc.text_rect, fig_box=getattr(sc, "fig_box", None),
                image=img, crop=fig.get("crop"), base=sc.base, col_w=sc.col_w, items=items)
