#!/usr/bin/env bash
# 本機 voice-clone TTS：預設裝 BreezyVoice（臺灣國語、300M、注音覆寫），跑在 GPU 上。
#   bash tts_local/setup_tts.sh               # BreezyVoice（約 8 GB 磁碟：venv + 權重）
#   bash tts_local/setup_tts.sh cosyvoice3    # 另建 CosyVoice3 的 venv（備援引擎，另約 6 GB）
# 完成後 speak.py 會自動改用 clone 後端（有 GPU 時）。沒有 GPU 也能裝，但合成會很慢、品質較差，只適合測試。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MODEL="${1:-breezyvoice}"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" INSTALLER_NO_MODIFY_PATH=1 sh
# 注意：不要把快取放 /tmp（有些機器的 /tmp 是記憶體做的，好幾 GB 的 CUDA 函式庫會吃光 RAM）
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}"

if [ "$MODEL" = "breezyvoice" ]; then
  V="$HERE/.venv"
  if [ ! -d "$HERE/BreezyVoice" ]; then
    git clone -q https://github.com/mtkresearch/BreezyVoice "$HERE/BreezyVoice"
    git -C "$HERE/BreezyVoice" checkout -q d592c9d
    git -C "$HERE/BreezyVoice" submodule update --init --recursive -q || true
  fi
  [ -d "$V" ] || uv venv -q -p 3.10 "$V"
  uv pip install -q -p "$V" torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cu118
  uv pip install -q -p "$V" -r "$HERE/requirements-breezy.txt"
  uv pip install -q -p "$V" --no-deps "openai-whisper==20250625"   # 模型設定檔用它的 tokenizer
  echo breezyvoice > "$HERE/model.txt"
elif [ "$MODEL" = "cosyvoice3" ]; then
  V="$HERE/.venv-cosyvoice3"
  if [ ! -d "$HERE/CosyVoice" ]; then
    git clone -q --recursive https://github.com/FunAudioLLM/CosyVoice "$HERE/CosyVoice"
  fi
  [ -d "$V" ] || uv venv -q -p 3.10 "$V"
  uv pip install -q -p "$V" torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cu121
  grep -v -i -E '^(torch|torchaudio|vllm|tensorrt|deepspeed)' "$HERE/CosyVoice/requirements.txt" | sed 's/#.*//' | grep -v '^\s*$' > "$HERE/.req-cv3.txt"
  uv pip install -q -p "$V" -r "$HERE/.req-cv3.txt" "transformers==4.51.3" "numpy==1.26.4" opencc-python-reimplemented faster-whisper nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*" huggingface_hub
  rm -f "$HERE/.req-cv3.txt"
  "$V/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='$HERE/pretrained/Fun-CosyVoice3-0.5B-2512')"
  echo cosyvoice3 > "$HERE/model.txt"
else
  echo "不認得的模型：$MODEL（可用 breezyvoice / cosyvoice3）"; exit 2
fi

# 內附示範聲音（BreezyVoice repo 的 example prompt，Apache-2.0）當備援聲音庫；自己的聲音用 scripts/voice.py prep 加
if [ -f "$HERE/BreezyVoice/data/example.wav" ] && [ ! -f "$HERE/voices/demo_f.wav" ]; then
  mkdir -p "$HERE/voices"
  cp "$HERE/BreezyVoice/data/example.wav" "$HERE/voices/demo_f.wav"
  echo "在密碼學中，加密是將明文資訊改變為難以讀取的密文內容，使之不可讀的方法。只有擁有解密方法的對象，經由解密過程，才能將密文還原為正常可讀的內容。" > "$HERE/voices/demo_f.txt"
  printf 'demo_f.wav <- github.com/mtkresearch/BreezyVoice @ d592c9d data/example.wav（repo Apache-2.0 內附示範 prompt，成年女聲，16 kHz）；逐字稿取自 data/batch_files.csv\n' > "$HERE/voices/SOURCES.txt"
fi

# 暖機：下載權重（BreezyVoice-300M / G2PW / Whisper），合成一句確認能跑
mkdir -p "$HERE/samples"
REF="$HERE/voices/demo_f.wav"; [ -f "$REF" ] || REF="$(ls "$HERE"/voices/*.wav 2>/dev/null | head -1 || true)"
if [ -n "${REF:-}" ]; then
  printf '[{"text": "安裝完成，這是本機語音測試。", "voice": "%s", "rate": "+0%%", "out": "%s/samples/setup_check.wav"}]\n' "$REF" "$HERE" > "$HERE/samples/jobs.json"
  if ! "$V/bin/python" "$HERE/engine.py" "$HERE/samples/jobs.json" --best-of 1 --model "$MODEL"; then
    echo "[setup_tts] ✗ 暖機合成失敗（看上面的錯誤）。render.py 仍會用 edge-tts 出片。"; exit 1
  fi
  echo "[setup_tts] ok -> $HERE/samples/setup_check.wav"
else
  echo "[setup_tts] 沒有任何參考聲音可以暖機；跑一題時 look.py 會從原片抽出老師的聲音。"
fi
echo "[setup_tts] 完成：speak.py 在有 GPU 時會自動用 clone 後端（ASKBACK_TTS=clone 可強制）。"
