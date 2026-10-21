#!/usr/bin/env python3
"""觀察：把要評的影片（與原片片段）轉成 AI 學生與評分者看得懂的材料，並量出客觀指標。

在有 input/ 與 output/ 的那一層執行：
    python3 SKILL/scripts/observe.py                       # 預設評 output/askback.mp4
    python3 SKILL/scripts/observe.py --video other.mp4     # 評別支影片（題目仍讀 input/case_input.json）

產出 eval/runs/<影片雜湊前 8 碼>-<第幾次>/observe/（每次評量一個新資料夾）：
    meta.json            客觀指標（長度、響度、靜音比例、語速、辨識信心、與腳本的字錯率…）
    transcript.txt       逐字稿，每行 [起–迄] 文字
    sheet_segments*.jpg  每一句講完時的畫面＋那一句的逐字稿（看「音畫對不對得上」）
    sheet_uniform.jpg    每隔幾秒一格（看整體節奏與版面）
    frames/              上面用到的單張截圖（要放大看細節時開這裡）
以及同一層的 prefix/：原片 overview.jpg（整段 12 格）、end_*.jpg（提問前的畫面）、transcript.txt
"""
import argparse, hashlib, json, math, os, pathlib, re, sys, textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import ROOT, cer, die, dump_json, ffmpeg, load_json, run, stamp, to_trad

SHEET_W = 1280


# ───────────────────────── 基本資訊 ─────────────────────────
def probe(video):
    r = run([ffmpeg(), "-hide_banner", "-i", video])
    err = r.stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    v = re.search(r"Stream #.*Video: .*?(\d{2,5})x(\d{2,5}).*?(?:([\d.]+) fps)?", err)
    fps = re.search(r"([\d.]+) fps", err)
    return dict(duration=round(dur, 2), has_video=bool(v), has_audio="Audio:" in err,
                width=int(v[1]) if v else None, height=int(v[2]) if v else None,
                fps=float(fps[1]) if fps else None)


def loudness(video):
    r = run([ffmpeg(), "-hide_banner", "-nostats", "-i", video, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else ""
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    lra = re.search(r"LRA:\s+([\d.]+) LU", tail)
    pk = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail)
    f = lambda m: None if not m or m[1] == "-inf" else float(m[1])
    return dict(lufs=f(i), lra=f(lra), true_peak_dbfs=f(pk))


def silences(video, dur, db=-35, min_len=0.4):
    r = run([ffmpeg(), "-hide_banner", "-nostats", "-i", video, "-vn", "-af", f"silencedetect=n={db}dB:d={min_len}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    if len(ends) < len(starts):
        ends.append(dur)
    spans = [(max(0, s), e) for s, e in zip(starts, ends)]
    total = sum(e - s for s, e in spans)
    inner = [e - s for s, e in spans if s > 0.3 and e < dur - 0.3]     # 不算頭尾留白
    return dict(silence_total=round(total, 2), speech_ratio=round(1 - total / dur, 3) if dur else 0,
                longest_inner_silence=round(max(inner), 2) if inner else 0.0)


def grab(video, t, dst, width=640):
    dst.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0, t):.2f}", "-i", video,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", dst])
    return dst if dst.exists() else None


# ───────────────────────── 語音辨識 ─────────────────────────
def asr(media, start=0.0, length=None, topic=""):
    """faster-whisper。有 GPU 用 large-v3，沒有用 small。回傳 [(s, e, text, avg_logprob)]，失敗回傳 None。"""
    try:
        try:
            import torch
            cuda = torch.cuda.is_available()
        except Exception:
            cuda = bool(os.environ.get("CUDA_VISIBLE_DEVICES")) and os.path.exists("/dev/nvidia0")
        from faster_whisper import WhisperModel
        import numpy as np
    except ImportError as e:
        print(f"  ⚠ 沒有 faster-whisper（{e}），跳過語音辨識")
        return None
    wav = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"askstu_{os.getpid()}.wav"
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-i", media]
    if length:
        cmd += ["-t", f"{length:.2f}"]
    run(cmd + ["-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", wav])
    if not wav.exists() or wav.stat().st_size < 3200:
        return []
    y = np.frombuffer(wav.read_bytes(), dtype=np.int16).astype("float32") / 32768
    wav.unlink(missing_ok=True)
    name = os.environ.get("ASKSTU_ASR_MODEL") or ("large-v3" if cuda else "small")
    try:
        model = WhisperModel(name, device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8")
        segs, _ = model.transcribe(y, language="zh", beam_size=5, vad_filter=True,
                                   initial_prompt="以下是繁體中文的教學影片旁白。" + (f"主題：{topic[:120]}" if topic else ""))
        out = [(start + s.start, start + s.end, to_trad(s.text.strip()), s.avg_logprob) for s in segs if s.text.strip()]
    except Exception as e:
        print(f"  ⚠ 語音辨識失敗（{e}）")
        return None
    print(f"  語音辨識：{name}（{'GPU' if cuda else 'CPU'}），{len(out)} 段")
    asr.model = name
    return out


def cjk_len(t):
    return len(re.findall(r"[\u3400-\u9fff]", t))


def read_vtt(p, until=None):
    out, cur = [], None
    for line in pathlib.Path(p).read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"(?:(\d+):)?(\d+):([\d.]+)\s*-->\s*(?:(\d+):)?(\d+):([\d.]+)", line)
        if m:
            s = int(m[1] or 0) * 3600 + int(m[2]) * 60 + float(m[3])
            e = int(m[4] or 0) * 3600 + int(m[5]) * 60 + float(m[6])
            cur = [s, e, ""]
            out.append(cur)
        elif cur is not None and line.strip() and not line.strip().isdigit():
            cur[2] += re.sub(r"<[^>]+>", "", line.strip())
    return [(s, e, t, None) for s, e, t in out if t and (until is None or s < until)]


# ───────────────────────── 拼圖 ─────────────────────────
def font(size):
    from PIL import ImageFont
    for p in [ROOT / "fonts/NotoSansTC.ttf", ROOT.parent / "askback-skill/fonts/NotoSansTC.ttf",
              "/System/Library/Fonts/STHeiti Medium.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"]:
        if pathlib.Path(p).exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def sheet(tiles, dst, cols=3, caption_lines=2):
    """tiles: [(jpg, 標題, 說明)]。說明會換行到 caption_lines 行。"""
    from PIL import Image, ImageDraw
    tiles = [t for t in tiles if t[0] and pathlib.Path(t[0]).exists()]
    if not tiles:
        return None
    tw = SHEET_W // cols
    im0 = Image.open(tiles[0][0])
    th = int(tw * im0.height / im0.width)
    f_head, f_cap = font(18), font(16)
    cap_h = 26 + 21 * caption_lines
    rows = math.ceil(len(tiles) / cols)
    canvas = Image.new("RGB", (SHEET_W, rows * (th + cap_h)), "white")
    d = ImageDraw.Draw(canvas)
    per_line = max(8, (tw - 12) // 16)
    for i, (jpg, head, cap) in enumerate(tiles):
        x, y = (i % cols) * tw, (i // cols) * (th + cap_h)
        canvas.paste(Image.open(jpg).convert("RGB").resize((tw - 4, th - 4)), (x + 2, y + 2))
        d.text((x + 6, y + th + 2), head, fill="#c0392b", font=f_head)
        for j, line in enumerate(textwrap.wrap(cap or "", per_line)[:caption_lines]):
            d.text((x + 6, y + th + 24 + j * 21), line, fill="#222", font=f_cap)
    canvas.save(dst, quality=85)
    return dst


# ───────────────────────── 主程式 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="output/askback.mp4")
    ap.add_argument("--case", default="input/case_input.json")
    ap.add_argument("--prefix", default="input/prefix.mp4")
    ap.add_argument("--vtt", default="input/prefix.vtt")
    ap.add_argument("--script", help="老師的腳本 lesson.json（用來算字錯率，只當客觀指標，不給學生看）。評 output/askback.mp4 時預設用 output/lesson.json")
    ap.add_argument("--eval", default="eval")
    ap.add_argument("--force", action="store_true", help="還沒凍結試題也照跑（不建議）")
    a = ap.parse_args()

    video, ev = pathlib.Path(a.video), pathlib.Path(a.eval)
    if not video.exists():
        die(f"找不到影片 {video}。沒有影片這一題就是 0 分：直接執行 score.py score --no-video 記錄。")
    if not pathlib.Path(a.case).exists():
        die(f"找不到 {a.case}")
    if not (ev / "items.lock").exists() and not a.force:
        die("還沒凍結試題。請先只看 case_input.json 出題（見 SKILL.md 第 1 步），再執行 score.py freeze。\n"
            "  試題要在看影片之前定下來，才不會被影片內容牽著走。")

    sha = hashlib.sha256(video.read_bytes()).hexdigest()
    runs = ev / "runs"
    n = 1 + max([int(d.name.rsplit("-", 1)[1]) for d in runs.glob(f"{sha[:8]}-*") if d.name.rsplit("-", 1)[1].isdigit()] or [0])
    rid = f"{sha[:8]}-{n}"                     # 每次評量一個新資料夾：同一支影片重評（重測）也不會混到上一輪的結果
    rdir = runs / rid
    out = rdir / "observe"
    out.mkdir(parents=True, exist_ok=True)
    (runs / "LATEST").write_text(rid, encoding="utf-8")
    case = load_json(a.case)
    print(f"評分對象：{video}（run {rid}）　題目：{case.get('case_id')} {case.get('title') or case.get('question', '')[:30]}")

    meta = dict(run_id=rid, video=str(video.resolve()), sha256=sha, case_id=case.get("case_id"))
    meta.update(probe(str(video)))
    dur = meta["duration"]
    print(f"  長度 {dur:.1f} 秒，{meta['width']}×{meta['height']}，音軌：{'有' if meta['has_audio'] else '沒有'}")
    if meta["has_audio"]:
        meta.update(loudness(str(video)))
        meta.update(silences(str(video), dur))
        print(f"  響度 {meta['lufs']} LUFS，說話時間比例 {meta['speech_ratio']:.0%}，最長停頓 {meta['longest_inner_silence']} 秒")

    # 逐字稿
    topic = " ".join(filter(None, [case.get("title"), case.get("question")]))
    segs = asr(str(video), topic=topic) if meta["has_audio"] else []
    if segs is None:                          # 辨識器沒裝好或壞掉：學生沒辦法引用原句，學習成效分會失真
        meta["asr_ok"] = False
        if not a.force:
            die("語音辨識沒有成功（見上面的 ⚠）。先執行 python3 -m pip install -r " + str(ROOT / "requirements.txt") +
                "，確定能下載 Whisper 模型後重跑。真的要在沒有逐字稿的情況下評，加 --force（學習成效分會不可比較）。")
        segs = []
    else:
        meta["asr_ok"] = True
    lines = []
    if segs:
        lines = [f"[{stamp(s)}–{stamp(e)}] {t}" for s, e, t, _ in segs]
        chars = sum(cjk_len(t) for *_, t, _ in segs)
        speech = sum(e - s for s, e, *_ in segs)
        meta["asr"] = dict(model=getattr(asr, "model", None), segments=len(segs), chars=chars, speech_sec=round(speech, 1),
                           rate_cps=round(chars / speech, 2) if speech else None,
                           confidence=round(sum(math.exp(lp) * (e - s) for s, e, _, lp in segs) / speech, 3) if speech else None)
        # 字錯率只當參考資訊（有沒有腳本會讓量法不同，不進分數）。只有評 output/askback.mp4 時才預設拿 output/lesson.json 比對
        default_video = video.resolve() == pathlib.Path("output/askback.mp4").resolve()
        sp = pathlib.Path(a.script) if a.script else pathlib.Path("output/lesson.json") if default_video else None
        if sp and sp.is_file():
            try:
                lesson = load_json(sp)
                says = "".join(b.get("say", "") for sc in lesson.get("scenes", []) for b in sc.get("beats", []))
                meta["script_cer"] = round(cer(says, "".join(t for _, _, t, _ in segs)), 3)
                print(f"  逐字稿與腳本的字錯率 {meta['script_cer']:.3f}（參考資訊，不進分數）")
            except Exception as e:
                print(f"  ⚠ 讀不了 {sp}（{e}），不算字錯率")
    meta["audible"] = bool(meta["has_audio"] and segs and (meta.get("lufs") is not None and meta["lufs"] > -50))
    if meta["has_audio"] and not meta["audible"]:
        print("  ⚠⚠ 有音軌但聽不到講話（太小聲或沒有可辨識的語音）。計分時會當作「沒有聲音」→ 0 分。")
    (out / "transcript.txt").write_text("\n".join(lines) + "\n" if lines else "（沒有可辨識的語音）\n", encoding="utf-8")

    # 截圖
    fdir = out / "frames"
    tiles, made = [], ["meta.json", "transcript.txt"]
    if meta["has_video"]:
        for i, (s, e, t, _) in enumerate(segs or []):
            jpg = grab(str(video), min(s + 0.75 * (e - s), dur - 0.1), fdir / f"seg{i + 1:02d}_{int(e):03d}s.jpg")
            tiles.append((jpg, f"#{i + 1}  {stamp(s)}–{stamp(e)}", t))
        for k in range(0, len(tiles), 12):
            if sheet(tiles[k:k + 12], out / f"sheet_segments{'' if k == 0 else f'_{k // 12 + 1}'}.jpg"):
                made.append(f"sheet_segments{'' if k == 0 else f'_{k // 12 + 1}'}.jpg")
        step = max(2.0, dur / 24)
        uni = [(grab(str(video), t, fdir / f"t{int(t):03d}.jpg"), stamp(t), "") for t in [step / 2 + i * step for i in range(int(dur // step))]]
        if sheet(uni, out / "sheet_uniform.jpg", cols=4, caption_lines=0):
            made.append("sheet_uniform.jpg")
    if not any(m.startswith("sheet_segments") for m in made):
        print("  ⚠ 沒有產生 sheet_segments.jpg（沒有逐字稿可以對齊），評分者與學生只能看 sheet_uniform.jpg。")

    # 原片（放在 run 底下的 prefix/，和影片材料分開：前測的 AI 學生只能看這裡）
    pre = pathlib.Path(a.prefix)
    t_ask = float(case.get("timestamp_seconds") or 0)
    if pre.exists():
        pdir = rdir / "prefix"
        pdur = probe(str(pre))["duration"] or t_ask
        ov = [(grab(str(pre), pdur * (i + 0.5) / 12, pdir / f"ov{i:02d}.jpg", 480), stamp(pdur * (i + 0.5) / 12), "") for i in range(12)]
        sheet(ov, pdir / "overview.jpg", cols=4, caption_lines=0)
        for k, back in enumerate([20, 10, 4, 0.5], 1):
            grab(str(pre), pdur - back, pdir / f"end_{k}.jpg", 1280)
        vtt = pathlib.Path(a.vtt)
        if vtt.exists():
            psegs = read_vtt(vtt, until=pdur + 1)
            src = "頻道字幕"
        else:
            win = min(120.0, pdur)
            psegs = asr(str(pre), start=pdur - win, length=win, topic=topic) or []
            src = f"語音辨識（最後 {win:.0f} 秒）"
        (pdir / "transcript.txt").write_text(f"# 原片逐字稿，來源：{src}\n" + "\n".join(f"[{stamp(s)}–{stamp(e)}] {t}" for s, e, t, _ in psegs) + "\n", encoding="utf-8")
        tail = [(s, e, t) for s, e, t, _ in psegs if s >= pdur - 120]
        speech = sum(e - s for s, e, _ in tail)
        meta["prefix"] = dict(duration=pdur, transcript_source=src,
                              rate_cps=round(sum(cjk_len(t) for *_, t in tail) / speech, 2) if speech > 5 else None)
        print(f"  原片：{pdur:.0f} 秒，逐字稿來源 {src}，最後兩分鐘語速 {meta['prefix']['rate_cps']} 字/秒")
    else:
        print("  （沒有 input/prefix.mp4：「延續原片教法」只能靠 case_input.json 的 style 與 context_summary 判斷）")

    dump_json(meta, out / "meta.json")
    print(f"\n✓ {out}/：" + "、".join(made) + (f"；原片在 {rdir / 'prefix'}/" if pre.exists() else ""))
    print(f"  下一步：python3 {ROOT}/scripts/score.py panel")


if __name__ == "__main__":
    main()
