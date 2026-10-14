#!/usr/bin/env python
"""本機 voice-clone TTS（跑在 GPU 上）：一次合成一整批句子，模型只載入一次。

    tts_local/.venv/bin/python tts_local/engine.py jobs.json [--best-of 3] [--model cosyvoice3|indextts2|qwen3tts]

jobs.json：[{"text": "繁體中文句子", "voice": "voices 裡的名字 或 某個 .wav 的路徑", "rate": "+0%", "out": "a.wav"}, ...]
voice 的 wav 旁邊要有同名 .txt（這段參考音檔說的話；沒有的話會用 Whisper 聽一次）。
輸出 24 kHz 單聲道 wav；有任何一句失敗就 exit 1（speak.py 會改用 edge-tts）。

best-of：同一句合成 N 次，用 Whisper 聽回來算 CER（字錯率），留最像的一次；CER 為 0 就提前停。
這是 clone 模型不穩時最有效的保險（偶爾會吞字、重複、唸錯多音字）。

模型由 --model 或環境變數 ASKBACK_CLONE_MODEL 指定，預設看 tts_local/setup_tts.sh 裝了哪一個（model.txt）。
每個模型一個 adapter（adapters/ 目錄），介面只有兩個方法：load() 與 synth(text, ref_wav, ref_text, speed) -> np.float32 24k。
"""
import argparse, json, os, pathlib, re, sys, time

HERE = pathlib.Path(__file__).resolve().parent
SR_OUT = 24000


def clean(text: str) -> str:
    """殘留的數字與算式先改成中文讀法（zhnum.py），再全形標點、去空白、句尾補句號。"""
    from zhnum import verbalize
    text = verbalize(text.strip())
    text = text.translate(str.maketrans({",": "，", "!": "！", "?": "？", ";": "；", ":": "：", "(": "（", ")": "）"}))
    text = re.sub(r"[…⋯]+", "，", text)
    text = re.sub(r"[，、；：]+$", "", text)
    if text and not re.search(r"[。！？]$", text):
        text += "。"
    return text


def rate_to_speed(rate: str) -> float:
    """edge-tts 風格的 '-6%' → 0.94 倍速。"""
    m = re.fullmatch(r"([+-]?\d+)%", (rate or "+0%").strip())
    return max(0.7, min(1.4, 1 + int(m[1]) / 100)) if m else 1.0


def ref_text_for(wav: pathlib.Path, asr) -> str:
    txt = wav.with_suffix(".txt")
    if txt.exists() and txt.read_text(encoding="utf-8").strip():
        return txt.read_text(encoding="utf-8").strip()
    if asr is None:
        raise RuntimeError(f"{txt} 不存在，而且沒有 Whisper 可以聽寫參考音檔")
    text = asr.transcribe_file(wav)
    txt.write_text(text + "\n", encoding="utf-8")
    return text


def preload_cuda():
    """CTranslate2 要 libcublas.so.12 / cuDNN 9（pip 的 nvidia-* 套件），先用 RTLD_GLOBAL 載入。"""
    import ctypes, glob, site
    for pat in ("nvidia/cuda_runtime/lib/libcudart.so.12", "nvidia/cublas/lib/libcublasLt.so.12", "nvidia/cublas/lib/libcublas.so.12",
                "nvidia/cudnn/lib/libcudnn.so.9", "nvidia/cudnn/lib/libcudnn_*.so.9"):
        for r in site.getsitepackages():
            for p in sorted(glob.glob(f"{r}/{pat}")):
                try:
                    ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


class ASR:
    """faster-whisper（GPU）：聽寫參考音檔、檢查合成結果。"""

    def __init__(self, name=None):
        preload_cuda()
        from faster_whisper import WhisperModel
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        name = name or os.environ.get("ASKBACK_ASR_MODEL", "large-v3" if dev == "cuda" else "small")
        self.m = WhisperModel(name, device=dev, compute_type="float16" if dev == "cuda" else "int8")
        try:
            import opencc
            self.cc = opencc.OpenCC("s2twp").convert
        except ImportError:
            self.cc = lambda t: t

    def transcribe(self, audio16k) -> str:
        segs, _ = self.m.transcribe(audio16k, language="zh", beam_size=3, condition_on_previous_text=False,
                                    initial_prompt="以下是臺灣老師說的繁體中文句子。")
        return self.cc("".join(s.text.strip() for s in segs))

    def transcribe_file(self, wav) -> str:
        import soundfile as sf, librosa
        y, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        y = y.mean(axis=1)
        if sr != 16000:
            y = librosa.resample(y, orig_sr=sr, target_sr=16000)
        return self.transcribe(y)


DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")


def norm(t: str) -> str:
    from zhnum import verbalize
    t = verbalize(re.sub(r"(?<=\d),(?=\d{3})", "", t))      # Whisper 常把數字寫成 1,598；兩邊都轉成中文讀法再比
    try:
        import opencc
        t = opencc.OpenCC("t2s").convert(t)
    except ImportError:
        pass
    return re.sub(r"[^一-鿿]", "", t.translate(DIGITS))


def cer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        prev, d[0] = d[0], i
        for k, hc in enumerate(h, 1):
            prev, d[k] = d[k], min(d[k] + 1, d[k - 1] + 1, prev + (rc != hc))
    return d[len(h)] / max(len(r), 1)


def load_adapter(name: str):
    import importlib
    try:
        mod = importlib.import_module(f"adapters.{name}")
    except ModuleNotFoundError as e:
        raise SystemExit(f"[engine] 沒有這個模型的 adapter：{name}（{e}）。可用：" +
                         "、".join(p.stem for p in (HERE / "adapters").glob("*.py") if not p.stem.startswith("_")))
    return mod.Adapter()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs")
    ap.add_argument("--best-of", type=int, default=int(os.environ.get("ASKBACK_BEST_OF", "3")))
    ap.add_argument("--model", default=os.environ.get("ASKBACK_CLONE_MODEL") or
                    ((HERE / "model.txt").read_text().strip() if (HERE / "model.txt").exists() else "cosyvoice3"))
    ap.add_argument("--seed", type=int, default=1234)
    a = ap.parse_args()
    sys.path.insert(0, str(HERE))
    jobs = json.loads(pathlib.Path(a.jobs).read_text(encoding="utf-8"))
    for j in jobs:
        for k in ("text", "voice", "out"):
            if not j.get(k):
                sys.exit(f"[engine] job 少了 {k}：{j}")

    t0 = time.time()
    import numpy as np, soundfile as sf, torch
    ad = load_adapter(a.model)
    ad.load()
    print(f"[engine] {a.model} 載入 {time.time() - t0:.1f}s（{'GPU ' + torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}）", file=sys.stderr, flush=True)
    asr = None
    if a.best_of > 1:
        try:
            asr = ASR()
            print(f"[engine] Whisper 載入（共 {time.time() - t0:.1f}s）", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[engine] 沒有 Whisper 可做 best-of（{e}），每句只合成一次", file=sys.stderr, flush=True)

    refs, failed = {}, 0
    for j in jobs:
        t1 = time.time()
        try:
            v = j["voice"]
            wav = pathlib.Path(v) if str(v).endswith(".wav") else HERE / "voices" / f"{v}.wav"
            if not wav.exists():
                raise FileNotFoundError(f"找不到參考音檔 {wav}")
            if str(wav) not in refs:
                refs[str(wav)] = (wav, ref_text_for(wav, asr))
            ref_wav, ref_text = refs[str(wav)]
            text = clean(j["text"])
            speed = rate_to_speed(j.get("rate", "+0%"))
            best = None
            for n in range(max(1, a.best_of if asr else 1)):
                torch.manual_seed(a.seed + n)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(a.seed + n)
                audio = ad.synth(text, ref_wav, ref_text, speed)
                audio = np.asarray(audio, dtype=np.float32).reshape(-1)
                if audio.size < SR_OUT * 0.2 or float(np.abs(audio).max()) < 1e-3:
                    continue
                if asr:
                    import librosa
                    hyp = asr.transcribe(librosa.resample(audio, orig_sr=SR_OUT, target_sr=16000))
                    score = cer(j["text"], hyp)
                else:
                    hyp, score = "", 0.0
                if best is None or score < best[0]:
                    best = (score, audio, hyp, n)
                if score <= 0.02:
                    break
            if best is None:
                raise RuntimeError("合成結果是空的或無聲")
            score, audio, hyp, n = best
            out = pathlib.Path(j["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            peak = float(np.abs(audio).max())
            if peak > 0.98:
                audio = audio / peak * 0.95
            sf.write(str(out), audio, SR_OUT, subtype="PCM_16")
            flag = "" if score <= 0.1 else "  ⚠ 和原句差很多，請聽一下"
            print(f"[engine] ok {out.name} {audio.size / SR_OUT:.2f}s take={n} cer={score:.2f} {time.time() - t1:.1f}s | {text}" +
                  (f" → 聽到：{hyp}" if hyp and score > 0 else "") + flag, file=sys.stderr, flush=True)
        except Exception as e:
            failed += 1
            print(f"[engine] FAIL {j.get('out')}: {e!r}", file=sys.stderr, flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
