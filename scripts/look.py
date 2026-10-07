#!/usr/bin/env python3
"""看：把 input/ 整理成 agent 看得懂的材料。

    python3 scripts/look.py [--input input] [--work work]

產出 work/look/：
  case.txt        題目重點（提問、年段、已知概念、進度摘要）
  overview.jpg    整段 prefix 的 12 格縮圖（看原片的形式與節奏）
  end_1..4.jpg    提問前最後 20 秒的 4 張原尺寸截圖（看學生卡住時畫面上有什麼）
  transcript.txt  逐字稿：有 prefix.vtt 就用它，沒有就語音辨識；都不行就略過
  palette.txt     最後一格的主要顏色（照抄進 lesson.json 的 style）

    python3 scripts/look.py --at 02:38 01:05     另外截指定時間的畫面 → work/look/at_0238.jpg
"""
import argparse, json, os, pathlib, re, shutil, subprocess, sys


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("找不到 ffmpeg。請先執行：python3 -m pip install imageio-ffmpeg")


def grab(src, t, dst, width=None):
    vf = ["-vf", f"scale={width}:-2"] if width else []
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(t, 0):.2f}",
                    "-i", str(src), "-frames:v", "1", *vf, "-q:v", "3", str(dst)], check=False)
    return pathlib.Path(dst).exists()


def stamp(s):
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def vtt_to_text(vtt):
    out = []
    for block in vtt.read_text(encoding="utf-8").split("\n\n"):
        m = re.search(r"(\d+):(\d+):([\d.]+)\s+-->", block)
        if not m:
            continue
        text = " ".join(l.strip() for l in block.splitlines() if l.strip() and "-->" not in l)
        out.append(f"[{stamp(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]))}] {text}")
    return "\n".join(out)


def asr(video, work):
    """沒有字幕時才用。回傳 (文字, 來源) 或 (None, 原因)。"""
    wav = work / "audio.wav"
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=False)
    if not wav.exists():
        return None, "影片沒有音軌"
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            mp3 = work / "audio.mp3"
            subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                            "-b:a", "48k", str(mp3)], check=True)
            r = OpenAI().audio.transcriptions.create(
                model="whisper-1", file=open(mp3, "rb"), language="zh",
                response_format="verbose_json", prompt="以下是繁體中文的教學影片。")
            return "\n".join(f"[{stamp(s.start)}] {s.text.strip()}" for s in r.segments), "OpenAI whisper-1（機器辨識，會有錯字，數字與專有名詞以畫面為準）"
        except Exception as e:
            print(f"  OpenAI 辨識失敗（{e}），改試本機模型")
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(os.environ.get("ASKBACK_ASR_MODEL", "small"), device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(wav), language="zh", initial_prompt="這是一段臺灣老師上課的錄音，請用繁體中文。",
                                   vad_filter=True, condition_on_previous_text=False)
        rows = [f"[{stamp(s.start)}] {s.text.strip()}" for s in segs if "繁體中文" not in s.text]
        return "\n".join(rows), "faster-whisper（機器辨識，會有錯字，數字與專有名詞以畫面為準）"
    except ImportError:
        return None, "沒有語音辨識工具（可選：python3 -m pip install faster-whisper）"
    except Exception as e:
        return None, f"語音辨識失敗：{e}"


def palette(img_path):
    """背景色＋最顯眼的幾個強調色（依色相分群）。"""
    import colorsys
    from collections import Counter
    from PIL import Image
    px = list(Image.open(img_path).convert("RGB").resize((240, 135)).getdata())
    bg = Counter((r // 16, g // 16, b // 16) for r, g, b in px).most_common(1)[0][0]
    out = ["背景 bg   #%02x%02x%02x" % tuple(min(255, c * 16 + 8) if c else 0 for c in bg)]
    bins = {}
    for r, g, b in px:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0.45 and v > 0.45:
            bins.setdefault(int(h * 12), []).append((r, g, b))
    for _, pts in sorted(bins.items(), key=lambda kv: -len(kv[1]))[:5]:
        if len(pts) < 20:
            continue
        pts = sorted(pts, key=lambda c: -(max(c) - min(c)))[:max(5, len(pts) // 5)]   # 取最飽和的，避開邊緣雜色
        r, g, b = (sum(c[i] for c in pts) // len(pts) for i in range(3))
        out.append("強調色    #%02x%02x%02x  %.1f%%" % (r, g, b, len(pts) * 100 / len(px)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="input")
    ap.add_argument("--work", default="work")
    ap.add_argument("--at", nargs="+", metavar="MM:SS", help="另外截這幾個時間點的原尺寸畫面（當作 lesson 的圖）")
    a = ap.parse_args()
    src = pathlib.Path(a.input)
    out = pathlib.Path(a.work) / "look"
    out.mkdir(parents=True, exist_ok=True)

    case = json.loads((src / "case_input.json").read_text(encoding="utf-8"))
    t = float(case.get("timestamp_seconds") or case.get("allowed_source_end_seconds") or 0)
    video = src / (case.get("video_path") or "prefix.mp4")
    if a.at:
        for at in a.at:
            sec = sum(float(x) * 60 ** i for i, x in enumerate(reversed(at.split(":"))))
            dst = out / f"at_{int(sec) // 60:02d}{int(sec) % 60:02d}.jpg"
            print(dst if grab(video, min(sec, t - 0.2), dst) else f"截不到 {at}")
        return
    prof = case.get("learner_profile") or {}
    lines = [
        f"題號：{case.get('case_id')}　{case.get('level')} {case.get('grade')} {case.get('subject')}",
        f"提問時間：{stamp(t)}（第 {t:g} 秒）",
        f"學生的問題：{case.get('question')}",
        "學生已經會的：" + "；".join(prof.get("known_concepts") or []),
        f"可能的迷思：{prof.get('misconception', '（未提供，請自行判斷）')}",
        f"影片講到哪裡：{case.get('context_summary')}",
        f"來源標示：{case.get('attribution')}",
    ]
    (out / "case.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    # 整段 12 格：看形式與節奏
    from PIL import Image, ImageDraw
    n, tw = 12, 480
    tiles = []
    for i in range(n):
        ts = t * (i + 0.5) / n
        p = out / f"_o{i}.jpg"
        if grab(video, ts, p, tw):
            tiles.append((ts, Image.open(p).convert("RGB")))
    if tiles:
        th = tiles[0][1].height
        sheet = Image.new("RGB", (tw * 4, th * 3), "black")
        d = ImageDraw.Draw(sheet)
        for i, (ts, im) in enumerate(tiles):
            x, y = (i % 4) * tw, (i // 4) * th
            sheet.paste(im, (x, y))
            d.rectangle([x, y, x + 62, y + 18], fill="black")
            d.text((x + 4, y + 3), stamp(ts), fill="yellow")
        sheet.save(out / "overview.jpg", quality=85)
    for p in out.glob("_o*.jpg"):
        p.unlink()

    # 提問前最後 20 秒：學生卡住時看到的畫面
    for i, back in enumerate([20, 10, 4, 0.5], 1):
        grab(video, t - back, out / f"end_{i}.jpg")
    end = out / "end_4.jpg"
    if end.exists():
        (out / "palette.txt").write_text("\n".join(palette(end)) + "\n", encoding="utf-8")

    vtt = src / "prefix.vtt"
    if vtt.exists():
        text, how = vtt_to_text(vtt), "prefix.vtt（人工字幕）"
    else:
        text, how = asr(video, out)
    if text:
        (out / "transcript.txt").write_text(f"# 來源：{how}\n{text}\n", encoding="utf-8")
    print(f"\n逐字稿：{how}")
    print(f"材料在 {out}/ ：" + "、".join(sorted(p.name for p in out.iterdir() if p.suffix in ('.jpg', '.txt'))))


if __name__ == "__main__":
    main()
