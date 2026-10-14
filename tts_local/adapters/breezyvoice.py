"""BreezyVoice（MediaTek Research，CosyVoice 架構、臺灣國語）adapter。

參考音檔：16 kHz 單聲道、≤30 秒（會自動裁），要附逐字稿。
文字：繁體直接輸入；多音字可在字後加注音 `長[:ㄔㄤ2]`，其他沒標的交給 G2PW 自動判斷。
"""
import os, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent.parent
BV = HERE / "BreezyVoice"
MODEL = os.environ.get("BREEZY_MODEL", "MediaTek-Research/BreezyVoice-300M")


class Adapter:
    sr = 22050

    def load(self):
        sys.path[:0] = [str(BV), str(BV / "third_party" / "Matcha-TTS")]
        os.environ.setdefault("PYTHONUTF8", "1")
        from single_inference import CustomCosyVoice, get_bopomofo_rare
        from g2pw import G2PWConverter
        import opencc
        self.cv = CustomCosyVoice(MODEL)
        self.g2p = G2PWConverter()
        self.rare = get_bopomofo_rare
        self.s2t = opencc.OpenCC("s2twp")
        self._pins = {}

    def _load_wav(self, path, sr):
        import soundfile as sf, torch
        import torchaudio.functional as AF
        data, orig = sf.read(str(path), dtype="float32", always_2d=True)
        wav = torch.from_numpy(data.mean(axis=1)).unsqueeze(0)
        if orig != sr:
            wav = AF.resample(wav, orig, sr)
        return wav[:, : sr * 29]              # CosyVoice 前端限制 ≤30 秒

    def _prep(self, text):
        """繁體 → 帶注音的輸入。G2PW 在繁體上跑（BreezyVoice 原本先轉簡體，會把「邊長」判成 ㄓㄤ3）。"""
        if re.search(r"[0-9A-Za-z%]", re.sub(r"\[.*?\]", "", text)):
            text = self.s2t.convert(self.cv.frontend.zh_tn_model.normalize(text)).replace(".", "。")
        return self.rare(text, self.g2p)

    def _pin(self, ref_wav, ref_text):
        key = str(ref_wav)
        if key not in self._pins:
            self._pins[key] = self.cv.frontend.frontend_zero_shot("。", self._prep(ref_text), self._load_wav(ref_wav, 16000))
        return self._pins[key]

    def synth(self, text, ref_wav, ref_text, speed=1.0):
        import torch
        pin = self._pin(ref_wav, ref_text)
        pieces = []
        with torch.no_grad():
            for sent in chunks(self._prep(text)):
                tok, tok_len = self.cv.frontend._extract_text_token(sent)
                out = self.cv.model.inference(**{**pin, "text": tok, "text_len": tok_len})["tts_speech"]
                pieces.append(out)
        audio = torch.cat(pieces, dim=1).squeeze(0).cpu().numpy()
        audio = resample(audio, self.sr, 24000)
        if abs(speed - 1.0) > 0.02:
            audio = stretch(audio, 24000, speed)
        return audio


def chunks(text, max_chars=50):
    """在。！？切句，太短的併在一起（兩三個字的句子會含糊）。"""
    out = []
    for sent in [x for x in re.split(r"(?<=[。！？])", text) if x.strip()]:
        n = lambda t: len(re.sub(r"\[.*?\]", "", t))
        if out and n(out[-1]) + n(sent) <= max_chars:
            out[-1] += sent
        else:
            out.append(sent)
    return out or [text]


def resample(audio, sr, to):
    if sr == to:
        return audio
    import librosa
    return librosa.resample(audio, orig_sr=sr, target_sr=to)


def stretch(audio, sr, speed):
    """語速：用 librosa 的時間伸縮（不變音高）。speed>1 變快。"""
    import librosa
    return librosa.effects.time_stretch(audio, rate=float(speed))
