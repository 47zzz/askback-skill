#!/usr/bin/env python3
"""做「已知較差」的對照影片，檢查評量工具分得出好壞（已知組別效度）。

    python3 SKILL/scripts/make_probes.py output/askback.mp4 --out probes/

產生（每一支都應該比原片低分；如果沒有，代表工具在那個面向不靈敏）：
    mute.mp4     拿掉聲音               → 影音品質、學習成效應大跌（而且觸發「沒有聲音」閘門）
    half.mp4     只留前半段             → 回應提問（沒講完）、長度、學習成效應下降
    fast.mp4     整支 1.6 倍速          → 影音品質（聽不清楚）、延續原片節奏應下降
    silent_gaps.mp4  每句之間塞 2.5 秒黑畫面靜音 → 節奏、影音品質應下降
另外「答非所問」不用做影片：把同一支影片配上另一題的 case_input.json 去評（observe.py --case 另一題），回應提問應大跌。
"""
import argparse, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import die, ffmpeg, run
from observe import probe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--out", default="probes")
    a = ap.parse_args()
    v, out = pathlib.Path(a.video), pathlib.Path(a.out)
    if not v.exists():
        die(f"找不到 {v}")
    out.mkdir(parents=True, exist_ok=True)
    info = probe(str(v))
    dur, W, H, fps = info["duration"], info["width"] or 1280, info["height"] or 720, info["fps"] or 30
    ff = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", v]
    jobs = {
        "mute.mp4": ["-an", "-c:v", "copy"],
        "half.mp4": ["-t", f"{dur / 2:.2f}", "-c:v", "libx264", "-c:a", "aac"],
        "fast.mp4": ["-filter_complex", "[0:v]setpts=PTS/1.6[v];[0:a]atempo=1.6[a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac"],
    }
    for name, args in jobs.items():
        r = run(ff + args + [out / name])
        print(("✓ " if (out / name).exists() else "✗ ") + name + (f"　{r.stderr.strip()[:200]}" if r.returncode else ""))
    # 句間塞黑畫面：在每段靜音的中點插 2.5 秒
    import re
    sil = run([ffmpeg(), "-hide_banner", "-nostats", "-i", v, "-vn", "-af", "silencedetect=n=-35dB:d=0.3", "-f", "null", "-"]).stderr
    mids = [(float(s) + float(e)) / 2 for s, e in zip([max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", sil)], re.findall(r"silence_end: ([\d.]+)", sil))]
    mids = [m for m in mids if 1 < m < dur - 1][:12]
    if mids:
        cuts = [0.0] + mids + [dur]
        parts, n = [], 0
        for i in range(len(cuts) - 1):
            parts.append(f"[0:v]trim=start={cuts[i]:.3f}:end={cuts[i + 1]:.3f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[v{n}];"
                         f"[0:a]atrim=start={cuts[i]:.3f}:end={cuts[i + 1]:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=mono[a{n}];")
            n += 1
            if i < len(cuts) - 2:
                parts.append(f"color=black:s={W}x{H}:d=2.5:r={fps},format=yuv420p,setsar=1[v{n}];anullsrc=r=44100:cl=mono,atrim=end=2.5[a{n}];")
                n += 1
        fc = "".join(parts) + "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
        r = run(ff + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", out / "silent_gaps.mp4"])
        print(("✓ " if (out / "silent_gaps.mp4").exists() else "✗ ") + "silent_gaps.mp4" + (f"　{r.stderr.strip()[:300]}" if r.returncode else ""))
    print(f"\n用法：每一支各自當 --video 跑 observe → panel → score，並加 --label probe-<名稱>；"
          f"\n最後用 analyze.py 加 --groups 比較原版與各探針。")


if __name__ == "__main__":
    main()
