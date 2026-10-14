#!/usr/bin/env python3
"""做：lesson.json → 一支有畫面、有語音、有字幕的補充教學影片。

    python3 scripts/render.py [work/lesson.json] [--out output/askback.mp4] [--engine auto|remotion|pillow]

一句旁白（beat）＝一個畫面動作。時間軸完全由實際語音長度決定，改一句不會讓其他句錯位。
流程：驗算 checks → 逐句語音（speak.py：有 GPU 就 clone 原片老師的聲音，否則 edge-tts；有快取）
     → 排時間軸 → 畫面（remotion/ 引擎：React 動態；沒有 node 就用 Pillow 逐格畫）→ 檢查長度 → preview.jpg
lesson.json 的寫法見 SKILL.md；可直接參考 examples/。
"""
import argparse, ast, json, math, operator, os, pathlib, re, shutil, subprocess, sys, wave
from fractions import Fraction

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT, die, ffmpeg, remotion_ready, run
from draw import BADGE, FONT_FILES, FORMS, H, SUB_H, W, Fonts, Scene, chrome, scene_props
from speak import EDGE_VOICES, backend as tts_backend, default_clone_voice, speak_all

from PIL import Image, ImageDraw

FPS_PILLOW, FPS_REMOTION = 20, 30
MIN_S, MAX_S = 30, 90


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


# ───────────────────────── 時間軸與整條旁白 ─────────────────────────
def timeline(beats, gap, lead=0.5, tail=1.0):
    t = lead
    for b in beats:
        b["start"], b["hold"] = t, b["dur"] + gap + float(b.get("pause", 0))
        t += b["hold"]
    return t - gap + tail


def narration_wav(beats, total, path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        pos = 0
        for b in beats:
            want = int(b["start"] * 44100)
            w.writeframes(b"\0\0" * (want - pos)); pos = want
            with wave.open(str(b["wav"])) as r:
                data = r.readframes(r.getnframes())
            w.writeframes(data); pos += len(data) // 2
        w.writeframes(b"\0\0" * max(0, int(total * 44100) - pos))


def preview_sheet(stills, ui, path):
    cols = 3
    rows = max(1, math.ceil(len(stills) / cols))
    sheet = Image.new("RGB", (cols * 640, rows * 360), "#202020")
    d = ImageDraw.Draw(sheet)
    for i, (k, im) in enumerate(stills):
        x, y = (i % cols) * 640, (i // cols) * 360
        sheet.paste(im.resize((640, 360), Image.LANCZOS), (x, y))
        d.rectangle([x, y, x + 46, y + 26], fill="#d00000")
        d.text((x + 8, y + 2), f"{k + 1}", font=ui.get(18, bold=True), fill="white")
    sheet.save(path, quality=85)


# ───────────────────────── 引擎一：Pillow 逐格畫 ─────────────────────────
def render_pillow(beats, style, ui, total, audio, credit, out):
    FPS = FPS_PILLOW
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
    return stills


# ───────────────────────── 引擎二：Remotion ─────────────────────────
def render_remotion(beats, scenes, style, ui, total, audio, credit, out, lesson_dir, work):
    rdir = ROOT / "remotion"
    pub = rdir / "public" / "job"
    if pub.exists():
        shutil.rmtree(pub)
    pub.mkdir(parents=True)
    fonts_pub = rdir / "public" / "fonts"
    fonts_pub.mkdir(exist_ok=True)
    for f in FONT_FILES.values():
        if not (fonts_pub / f).exists():
            shutil.copy(ROOT / "fonts" / f, fonts_pub / f)
    shutil.copy(audio, pub / "narration.wav")
    images = {}
    for sc in scenes:
        fig = sc.spec.get("figure") or {}
        if fig.get("image"):
            p = pathlib.Path(fig["image"])
            p = p if p.is_absolute() or p.exists() else lesson_dir / p
            name = f"img_{len(images)}{p.suffix.lower() or '.jpg'}"
            shutil.copy(p, pub / name)
            images[str(p)] = f"job/{name}"
    props = dict(
        w=W, h=H, fps=FPS_REMOTION, total=total, subH=SUB_H, badge=BADGE, credit=credit,
        style=dict(form=style["form"], bg=style["bg"], ink=style["ink"], font=style["font"], reveal=style["reveal"],
                   size=style["size"], colors=style["colors"], title_color=style.get("title_color", "y")),
        audio="job/narration.wav",
        beats=[dict(i=i, start=b["start"], dur=b["dur"], hold=b["hold"], say=b["say"], sub=b.get("sub", b["say"]),
                    who=b.get("who", ""), scene=scenes.index(b["scene"])) for i, b in enumerate(beats)],
        scenes=[scene_props(sc, lesson_dir, images) for sc in scenes],
    )
    props_path = work / "remotion_props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    raw = out.with_suffix(".raw.mp4")
    conc = os.environ.get("ASKBACK_REMOTION_CONCURRENCY") or str(max(2, min(8, (os.cpu_count() or 4) - 1)))
    cmd = ["npx", "remotion", "render", "src/index.ts", "Lesson", str(raw.resolve()), f"--props={props_path.resolve()}",
           "--codec=h264", "--crf=19", f"--concurrency={conc}", "--log=error", "--overwrite"]
    browser = os.environ.get("ASKBACK_BROWSER")
    if browser:
        cmd.append(f"--browser-executable={browser}")
    print("  remotion render（第一次會慢一點：打包、開 headless Chrome）")
    r = run(cmd, cwd=rdir)
    if r.returncode != 0 or not raw.exists():
        raise RuntimeError("remotion render 失敗")
    # 固定 GOP、faststart，拖時間軸才順；音訊直接用整條旁白 wav，保證對齊
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", raw, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-g", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", out], check=True)
    raw.unlink(missing_ok=True)
    stills = []
    for i, b in enumerate(beats):
        ts = min(total - 0.05, b["start"] + b["dur"] + 0.05)
        p = work / f"_still_{i}.jpg"
        run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ts:.2f}", "-i", out, "-frames:v", "1", "-q:v", "3", p], check=False)
        if p.exists():
            stills.append((i, Image.open(p).convert("RGB")))
            p.unlink()
    return stills


# ───────────────────────── 主流程 ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lesson", nargs="?", default="work/lesson.json")
    ap.add_argument("--out", default="output/askback.mp4")
    ap.add_argument("--work", default="work")
    ap.add_argument("--engine", default=os.environ.get("ASKBACK_ENGINE", "auto"), choices=["auto", "remotion", "pillow"])
    a = ap.parse_args()
    lesson_path = pathlib.Path(a.lesson)
    try:
        L = json.loads(lesson_path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"讀不了 {lesson_path}：{e}")
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(a.work)
    cache = work / "audio"
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
    gap = float(st.get("gap", 0.35))

    # 語速：lesson 沒寫就跟原片（look.py 量出來的 work/voice/profile.json）
    prof = {}
    pp = work / "voice" / "profile.json"
    if pp.exists():
        try:
            prof = json.loads(pp.read_text(encoding="utf-8"))
        except Exception:
            prof = {}
    rate = st.get("rate") or prof.get("rate") or "+0%"
    voices = st.get("voices") or {}
    be = tts_backend()
    default_voice = st.get("voice") or (default_clone_voice(work) if be == "clone" else EDGE_VOICES[0])

    print("① 驗算")
    run_checks(L.get("checks"))
    if not L.get("checks"):
        print("  （沒有 checks。只有完全沒有數字或計算的題目才可以省略。）")

    scenes, beats = [], []
    for sc in L.get("scenes") or []:
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

    print(f"② 語音（{len(beats)} 句，後端 {be}）")
    seen = {}
    for b in beats:
        who = b.get("who", "")
        if who not in voices and who not in seen:
            seen[who] = (EDGE_VOICES[len(seen) % len(EDGE_VOICES)] if be != "clone" else default_voice) if who else default_voice
        b["voice"] = voices.get(who, seen.get(who, default_voice))
        b["rate"] = b.get("rate", rate)
    used = speak_all(beats, cache, work)
    total = timeline(beats, gap)
    chars = sum(len(re.sub(r"[\s，。、？！：；,.?!]", "", b["say"])) for b in beats)
    for i, b in enumerate(beats, 1):
        if len(b.get("sub", b["say"])) > 44:
            print(f"  ⚠ 第 {i} 句字幕 {len(b.get('sub', b['say']))} 字，太長。一句只說一件事：拆成兩句，或用 sub 寫短一點。")
    print(f"  旁白 {chars} 字，預計 {total:.1f} 秒；聲音：" + "、".join(sorted({str(b['voice']).split('/')[-1] for b in beats})) + f"，語速 {rate}")

    audio = work / "narration.wav"
    narration_wav(beats, total, audio)

    engine = a.engine
    if engine == "auto":
        engine = "remotion" if remotion_ready() else "pillow"
    print(f"③ 畫面與合成（{engine}）")
    credit = L.get("credit") or "教材來源：均一教育平台 Junyi Academy，CC BY-NC-SA 3.0 TW"
    stills = None
    if engine == "remotion":
        try:
            stills = render_remotion(beats, scenes, style, ui, total, audio, credit, out, lesson_path.parent, work)
        except Exception as e:
            if a.engine == "remotion":
                die(f"{e}。先到 remotion/ 跑 `npm ci && npx remotion browser ensure`，或改用 --engine pillow")
            print(f"  ⚠ Remotion 引擎失敗（{e}），改用 Pillow 引擎。")
            engine = "pillow"
    if engine == "pillow":
        stills = render_pillow(beats, style, ui, total, audio, credit, out)

    preview = out.with_name("preview.jpg")
    preview_sheet(stills, ui, preview)
    shutil.copy(lesson_path, out.with_name("lesson.json"))

    ok = MIN_S <= total <= MAX_S
    print(f"\n{'✓' if ok else '✗'} {out}　{total:.1f} 秒　{len(beats)} 句　{chars} 字　語音 {used}　畫面 {engine}")
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
