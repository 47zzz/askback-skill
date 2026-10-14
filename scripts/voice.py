#!/usr/bin/env python3
"""聲音：從原片挑出老師的聲音當 clone 的參考音檔；或把自己的錄音做成參考聲音。

    python3 scripts/voice.py profile            # 分析 input/prefix.mp4 → work/voice/（look.py 會自動跑）
    python3 scripts/voice.py prep my.wav --name me_f --gender female     # 自己的錄音 → tts_local/voices/me_f.{wav,txt}
    python3 scripts/voice.py bank               # 列出聲音庫，順便把缺的輪廓算出來

profile 做三件事：
1. 找出 prefix.mp4 裡講話的段落（silero VAD），量主要說話者的音高（F0）、語速（字/秒）、停頓長度
   → work/voice/profile.json。render.py 用它的 rate 當預設語速，影片才會和原片一樣快慢。
2. 從主要說話者的段落裡挑最乾淨的 4–12 秒（講話連續、雜音少、不在片頭）當 one-shot 參考音檔，
   連同這段的逐字稿（Whisper 聽這一段；沒有 Whisper 就用 prefix.vtt 對時間）
   → work/voice/teacher.wav、teacher.txt，另存 ref_1..3.wav 候選。這就是 clone 用的聲音。
3. 在 tts_local/voices/bank.json 裡挑性別、音高、語速最接近的授權聲音當備援（profile.json 的 match）。

參考音檔來源可以用 ASKBACK_CLONE_SOURCE 切換：video（預設，原片老師）或 bank（只用聲音庫）。
提醒：素材包聲明不含原講者的聲紋授權；要不要用原講者的聲音是使用者的決定，角標無論如何都會保留。
"""
import argparse, json, math, os, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import TTS_LOCAL, die, ffmpeg, gpu, run, stamp

BANK = TTS_LOCAL / "voices"
SR = 16000
REF_SR = 24000


def extract(src, dst, sr=SR, start=None, end=None, af=None):
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if end is not None:
        cmd += ["-t", f"{max(0.1, end - (start or 0)):.3f}"]
    if af:
        cmd += ["-af", af]
    run(cmd + ["-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", dst], check=True)
    return pathlib.Path(dst).exists()


def load(path):
    import soundfile as sf
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return y.mean(axis=1), sr


def vad_segments(y, sr):
    """[(start_s, end_s)]：有人講話的段落。silero VAD 跑 CPU 就夠快。"""
    try:
        import torch
        from silero_vad import load_silero_vad, get_speech_timestamps
        model = load_silero_vad()
        ts = get_speech_timestamps(torch.from_numpy(y), model, sampling_rate=sr, return_seconds=True,
                                   min_speech_duration_ms=400, min_silence_duration_ms=250)
        return [(t["start"], t["end"]) for t in ts]
    except ImportError:
        import numpy as np
        hop = int(sr * 0.03)
        e = np.array([float(np.sqrt(np.mean(y[i:i + hop] ** 2)) + 1e-9) for i in range(0, len(y) - hop, hop)])
        thr = np.percentile(e, 60)
        on, segs, st = False, [], 0
        for i, v in enumerate(e):
            if v > thr and not on:
                on, st = True, i
            elif v <= thr and on:
                on = False
                if (i - st) * hop / sr > 0.4:
                    segs.append((st * hop / sr, i * hop / sr))
        return segs


def f0_stats(y, sr, segs):
    """每段：((s, e), 中位 F0, 有聲比例)。"""
    import librosa, numpy as np
    out = []
    for s, e in segs:
        a = y[int(s * sr):int(e * sr)]
        if len(a) < sr * 0.4:
            continue
        f0, voiced, _ = librosa.pyin(a, fmin=70, fmax=400, sr=sr, frame_length=1024)
        v = f0[voiced & ~np.isnan(f0)]
        if len(v) < 10:
            continue
        out.append(((s, e), float(np.median(v)), float(len(v) / max(1, len(f0)))))
    return out


def main_speaker(stats):
    """兩個說話者（角色劇）時，用音高分成兩群，取講話時間最長的那群。單一說話者就原樣回傳。"""
    if len(stats) < 4:
        return stats
    import numpy as np
    x = np.array([math.log(f) for _, f, _ in stats])
    c = np.array([x.min(), x.max()])
    for _ in range(30):
        lab = np.argmin(np.abs(x[:, None] - c[None, :]), axis=1)
        c = np.array([x[lab == k].mean() if (lab == k).any() else c[k] for k in range(2)])
    dur = [sum((e - s) for ((s, e), _, _), l in zip(stats, lab) if l == k) for k in range(2)]
    if abs(c[1] - c[0]) < math.log(1.4) or min(dur) < 0.2 * sum(dur):   # 兩群差不到 40%、或一群太小：同一個人
        return stats
    k = int(np.argmax(dur))
    return [st for st, l in zip(stats, lab) if l == k]


def snr_db(y, sr, s, e, segs):
    """粗略訊噪比：這段的能量 ÷ 它前後最近的非語音區能量。"""
    import numpy as np
    sp = y[int(s * sr):int(e * sr)]
    gaps = []
    for (a, b), (c, d) in zip(segs, segs[1:]):
        if d <= s + 0.01 or c >= e - 0.01 or (b <= s and c >= e):
            if c - b > 0.25 and abs((b + c) / 2 - (s + e) / 2) < 30:
                gaps.append(y[int(b * sr):int(c * sr)])
    if not gaps:
        return 25.0
    noise = np.concatenate(gaps)
    rs, rn = float(np.sqrt(np.mean(sp ** 2)) + 1e-9), float(np.sqrt(np.mean(noise ** 2)) + 1e-9)
    return min(40.0, 20 * math.log10(rs / rn))


def pick_reference(y, sr, stats, segs, total):
    """從主要說話者的段落裡挑 4–12 秒最適合當參考的幾個窗口。回傳 [(score, s, e)] 由好到壞。"""
    blocks = []
    for (s, e), f0, vr in stats:
        if blocks and s - blocks[-1][1] < 0.5 and e - blocks[-1][0] <= 14:
            blocks[-1][1] = e
            blocks[-1][2].append(vr)
        else:
            blocks.append([s, e, [vr]])
    cands = []
    for s, e, vrs in blocks:
        if e - s > 12:
            s = e - 11.5
        L = e - s
        if L < 3.5:
            continue
        snr = snr_db(y, sr, s, e, segs)
        score = min(L, 9) / 9 * 3 + sum(vrs) / len(vrs) * 2 + min(snr, 30) / 30 * 3
        if s < 4:
            score -= 2            # 片頭常有音樂、片名
        if s > total * 0.2:
            score += 0.5          # 進入正課後語氣比較穩
        cands.append((score, s, e, snr))
    cands.sort(reverse=True)
    return cands[:3]


def vtt_text_between(vtt, s, e):
    if not vtt or not pathlib.Path(vtt).exists():
        return None
    out = []
    for block in pathlib.Path(vtt).read_text(encoding="utf-8").split("\n\n"):
        m = re.search(r"(\d+):(\d+):([\d.]+)\s+-->\s+(\d+):(\d+):([\d.]+)", block)
        if not m:
            continue
        a = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        b = int(m[4]) * 3600 + int(m[5]) * 60 + float(m[6])
        if b > s + 0.3 and a < e - 0.3:
            out.append(" ".join(l.strip() for l in block.splitlines() if l.strip() and "-->" not in l))
    return "".join(out) or None


def transcribe(wav):
    """逐字稿（zero-shot clone 要附）。有 GPU 用 large-v3，沒有用 small。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        if gpu():
            m = WhisperModel(os.environ.get("ASKBACK_ASR_MODEL", "large-v3"), device="cuda", compute_type="float16")
        else:
            m = WhisperModel("small", device="cpu", compute_type="int8")
        y, sr = load(wav)                       # 直接餵陣列，避開 PyAV 版本不合
        if sr != 16000:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=16000)
        segs, _ = m.transcribe(y, language="zh", initial_prompt="這是一段臺灣老師上課的錄音，請用繁體中文。",
                               vad_filter=False, condition_on_previous_text=False)
        text = "".join(s.text.strip() for s in segs)
        try:
            import opencc
            text = opencc.OpenCC("s2twp").convert(text)
        except ImportError:
            pass
        return text or None
    except Exception as e:
        print(f"  ⚠ 語音辨識失敗（{e}）")
        return None


def speaking_rate(transcript_path, segs):
    if not transcript_path or not pathlib.Path(transcript_path).exists():
        return None
    txt = pathlib.Path(transcript_path).read_text(encoding="utf-8")
    chars = len(re.findall(r"[一-鿿]", txt))
    speech = sum(e - s for s, e in segs)
    return chars / speech if speech > 5 and chars > 20 else None


def profile(video, work, transcript=None, vtt=None, t_end=None):
    vdir = work / "voice"
    vdir.mkdir(parents=True, exist_ok=True)
    wav = vdir / "prefix16k.wav"
    if not extract(video, wav, end=t_end):
        die("影片沒有音軌，無法分析聲音")
    y, sr = load(wav)
    total = len(y) / sr
    segs = vad_segments(y, sr)
    if not segs:
        die("prefix.mp4 裡聽不到人聲")
    stats = f0_stats(y, sr, segs)
    main = main_speaker(stats) or stats
    import numpy as np
    f0 = float(np.median([f for _, f, _ in main])) if main else None
    gender = None if f0 is None else ("female" if f0 >= 165 else "male")
    rate = speaking_rate(transcript, [s for s, _, _ in main] or segs)
    gaps = [b[0] - a[1] for a, b in zip(segs, segs[1:]) if 0 < b[0] - a[1] < 3]
    prof = dict(
        f0_hz=round(f0) if f0 else None, gender=gender,
        chars_per_sec=round(rate, 2) if rate else None,
        pause_sec=round(float(np.median(gaps)), 2) if gaps else None,
        speech_sec=round(sum(e - s for s, e in segs), 1), segments=len(segs),
        speakers=2 if len(main) < len(stats) else 1,
    )
    # 參考音檔：原片老師最乾淨的一段
    refs = []
    for i, (score, s, e, snr) in enumerate(pick_reference(y, sr, main, segs, total), 1):
        dst = vdir / f"ref_{i}.wav"
        extract(video, dst, sr=REF_SR, start=max(0, s - 0.1), end=min(total, e + 0.2), af="loudnorm=I=-18:TP=-2:LRA=9")
        refs.append(dict(file=dst.name, start=round(s, 2), end=round(e, 2), seconds=round(e - s, 1), snr_db=round(snr, 1), score=round(score, 2)))
    if refs:
        best = vdir / refs[0]["file"]
        import shutil
        shutil.copy(best, vdir / "teacher.wav")
        text = transcribe(vdir / "teacher.wav") or vtt_text_between(vtt, refs[0]["start"], refs[0]["end"])
        (vdir / "teacher.txt").write_text((text or "") + "\n", encoding="utf-8")
        refs[0]["text"] = text
        prof["teacher_ref"] = dict(file="teacher.wav", seconds=refs[0]["seconds"], snr_db=refs[0]["snr_db"],
                                   text=text, from_=f"{stamp(refs[0]['start'])}–{stamp(refs[0]['end'])}")
        if not text:
            print("  ⚠ 參考音檔沒有逐字稿（語音辨識不可用、也沒有 prefix.vtt）：請聽 work/voice/teacher.wav 把內容寫進 teacher.txt")
        if refs[0]["snr_db"] < 8:
            print(f"  ⚠ 原片人聲和背景分不太開（SNR {refs[0]['snr_db']} dB），clone 出來可能帶雜音；可改 ASKBACK_CLONE_SOURCE=bank")
    prof["refs"] = refs
    prof.update(match_bank(prof))
    (vdir / "profile.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    wav.unlink(missing_ok=True)
    return prof


# ───────────────────────── 聲音庫 ─────────────────────────
def bank():
    p = BANK / "bank.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("voices", [])


def save_bank(voices):
    BANK.mkdir(parents=True, exist_ok=True)
    (BANK / "bank.json").write_text(json.dumps({"voices": voices}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def match_bank(prof):
    """性別相同優先；再比音高（對數差）與語速。回傳 {"match", "rate"}。"""
    vs = [v for v in bank() if (BANK / f"{v['name']}.wav").exists()]
    rate = "+0%"
    tgt = prof.get("chars_per_sec")
    if tgt:
        pct = max(-20, min(20, round((tgt / 4.0 - 1) * 100 / 2) * 2))   # 以 4 字/秒為一般語速
        rate = f"{pct:+d}%"
    if not vs:
        return {"match": None, "rate": rate}

    def score(v):
        s = 0 if (prof.get("gender") in (None, v.get("gender"))) else 10
        if prof.get("f0_hz") and v.get("f0_hz"):
            s += abs(math.log(prof["f0_hz"] / v["f0_hz"])) * 4
        if tgt and v.get("chars_per_sec"):
            s += abs(tgt - v["chars_per_sec"]) * 0.5
        return s
    return {"match": min(vs, key=score)["name"], "rate": rate}


def prep(src, name, gender, text=None, start=None, end=None):
    """自己的錄音 → tts_local/voices/<name>.wav（24 kHz 單聲道、裁 3–20 秒、響度正規化）+ .txt + bank.json。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
        die("名字只能用英數、底線、連字號")
    BANK.mkdir(parents=True, exist_ok=True)
    tmp = BANK / f"{name}.tmp.wav"
    if not extract(src, tmp, start=start, end=end):
        die(f"讀不了 {src}")
    y, sr = load(tmp)
    segs = vad_segments(y, sr)
    if not segs:
        die("錄音裡聽不到人聲")
    best, cur = tuple(segs[0]), list(segs[0])
    for s, e in segs[1:]:
        if s - cur[1] < 0.6 and e - cur[0] <= 20:
            cur[1] = e
        else:
            cur = [s, e]
        if cur[1] - cur[0] > best[1] - best[0]:
            best = tuple(cur)
    if best[1] - best[0] < 3:
        die("找不到 3 秒以上連續講話的段落；請錄長一點、中間少停頓")
    s, e = max(0, best[0] - 0.15), min(len(y) / sr, best[1] + 0.25)
    out = BANK / f"{name}.wav"
    extract(tmp, out, sr=REF_SR, start=s, end=e, af="loudnorm=I=-18:TP=-2:LRA=9")
    tmp.unlink(missing_ok=True)
    text = text or transcribe(out)
    if not text:
        die(f"請把 {out} 裡說的話一字不差寫進 {out.with_suffix('.txt')}，再跑一次 voice.py bank")
    out.with_suffix(".txt").write_text(text.strip() + "\n", encoding="utf-8")
    entry = measure(name)
    entry.update(gender=gender, source="自己錄製（本人同意作為參考聲音）")
    save_bank([v for v in bank() if v["name"] != name] + [entry])
    print(f"{out}（{e - s:.1f} 秒）\n逐字稿：{text}\n輪廓：{entry}")


def measure(name):
    wav, txt = BANK / f"{name}.wav", BANK / f"{name}.txt"
    y, sr = load(wav)
    segs = vad_segments(y, sr) or [(0, len(y) / sr)]
    stats = f0_stats(y, sr, segs)
    import numpy as np
    f0 = float(np.median([f for _, f, _ in stats])) if stats else None
    chars = len(re.findall(r"[一-鿿]", txt.read_text(encoding="utf-8"))) if txt.exists() else 0
    speech = sum(e - s for s, e in segs)
    return dict(name=name, f0_hz=round(f0) if f0 else None,
                chars_per_sec=round(chars / speech, 2) if chars and speech else None,
                gender=("female" if f0 and f0 >= 165 else "male"), seconds=round(len(y) / sr, 1))


def cmd_bank():
    vs = {v["name"]: v for v in bank()}
    for wav in sorted(BANK.glob("*.wav")):
        n = wav.stem
        if n not in vs or not vs[n].get("f0_hz"):
            m = measure(n)
            vs[n] = {**m, **{k: v for k, v in vs.get(n, {}).items() if v}}
    save_bank(list(vs.values()))
    if not vs:
        print(f"{BANK} 裡還沒有聲音。用 `voice.py prep` 加一個，或跑 tts_local/setup_tts.sh 取得內附示範聲音。")
    for v in vs.values():
        print(f"{v['name']:<14} {v.get('gender','?'):<7} F0 {v.get('f0_hz','?')} Hz  {v.get('chars_per_sec','?')} 字/秒  {v.get('seconds','?')} 秒  {v.get('source','')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile"); p.add_argument("--input", default="input"); p.add_argument("--work", default="work")
    p = sub.add_parser("prep"); p.add_argument("wav"); p.add_argument("--name", required=True)
    p.add_argument("--gender", choices=["female", "male"], required=True)
    p.add_argument("--text", help="錄音內容逐字稿（不給就用 Whisper 辨識）")
    p.add_argument("--start", type=float); p.add_argument("--end", type=float)
    sub.add_parser("bank")
    a = ap.parse_args()
    if a.cmd == "profile":
        src = pathlib.Path(a.input)
        case = json.loads((src / "case_input.json").read_text(encoding="utf-8"))
        t = float(case.get("timestamp_seconds") or 0) or None
        video = src / (case.get("video_path") or "prefix.mp4")
        work = pathlib.Path(a.work)
        prof = profile(video, work, work / "look" / "transcript.txt", src / "prefix.vtt", t)
        print(json.dumps({k: v for k, v in prof.items() if k != "refs"}, ensure_ascii=False))
    elif a.cmd == "prep":
        prep(a.wav, a.name, a.gender, a.text, a.start, a.end)
    else:
        cmd_bank()


if __name__ == "__main__":
    main()
