"""Fun-CosyVoice3-0.5B-2512（FunAudioLLM，Apache-2.0）adapter：備援引擎。

研究備忘（team/docs/2026-10-14_技術決策備忘錄.md）整理出的護欄，這裡都照做：
- 權重 repo 內的 llm.rl.pt 是 RL 版，要改名成 llm.pt 才會載入（預設用它，CER 較低）。
- prompt_text 要加前綴 "You are a helpful assistant.<|endofprompt|>"。
- fp16=False；一句一 call；參考音檔 24 kHz、≤30 秒、逐字稿要吻合。
- 繁體會念錯：tts_text 與 prompt_text 都先 OpenCC t2s（純字形），字幕仍是繁體（render.py 管）。
- 注音標記 `字[:ㄓㄨㄥ4]` 這個引擎不支援，會先拿掉（多音字請改寫句子避開）。
"""
import os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent.parent
CV = HERE / "CosyVoice"
MODEL_DIR = pathlib.Path(os.environ.get("COSYVOICE3_DIR", HERE / "pretrained" / "Fun-CosyVoice3-0.5B-2512"))
PREFIX = "You are a helpful assistant.<|endofprompt|>"


class Adapter:
    sr = 24000

    def load(self):
        sys.path[:0] = [str(CV), str(CV / "third_party" / "Matcha-TTS")]
        rl, base = MODEL_DIR / "llm.rl.pt", MODEL_DIR / "llm.pt"
        if rl.exists() and os.environ.get("COSYVOICE3_RL", "1") == "1" and not base.is_symlink():
            if base.exists():
                base.rename(MODEL_DIR / "llm.base.pt")
            base.symlink_to(rl.name)
        from cosyvoice.cli.cosyvoice import AutoModel
        import opencc
        self.cv = AutoModel(model_dir=str(MODEL_DIR), fp16=False)
        self.t2s = opencc.OpenCC("t2s")
        self._ref = {}

    def _ref_wav(self, path):
        import soundfile as sf, torch
        import torchaudio.functional as AF
        data, orig = sf.read(str(path), dtype="float32", always_2d=True)
        wav = torch.from_numpy(data.mean(axis=1)).unsqueeze(0)
        if orig != 16000:
            wav = AF.resample(wav, orig, 16000)
        return wav[:, : 16000 * 29]

    def synth(self, text, ref_wav, ref_text, speed=1.0):
        import torch, numpy as np
        text = self.t2s.convert(re.sub(r"\[:[^\]]*\]", "", text))
        ptxt = PREFIX + self.t2s.convert(re.sub(r"\[:[^\]]*\]", "", ref_text))
        key = str(ref_wav)
        if key not in self._ref:
            self._ref[key] = self._ref_wav(ref_wav)
        pieces = []
        with torch.no_grad():
            for out in self.cv.inference_zero_shot(text, ptxt, self._ref[key], stream=False, speed=float(speed)):
                pieces.append(out["tts_speech"])
        return torch.cat(pieces, dim=1).squeeze(0).cpu().numpy().astype(np.float32)
