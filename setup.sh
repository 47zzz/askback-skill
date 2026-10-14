#!/usr/bin/env bash
# 一次裝好這個 skill 需要的東西。可以重複執行。
#   bash SKILL/setup.sh            基本（Pillow 引擎 + edge-tts）＋ Remotion 引擎（有 node 才裝）＋ GPU 工具（有 nvidia-smi 才裝）
#   bash SKILL/setup.sh --minimal  只裝基本：沒有 node、沒有 GPU 的環境也能出片
#   bash SKILL/setup.sh --tts      另外把本機 voice-clone TTS 裝起來（tts_local/setup_tts.sh，需要 GPU，約 5–10 GB）
# Python 套件裝進目前的 python3（系統不讓裝就自動建 SKILL/.venv）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-full}"

PY="${ASKBACK_PYTHON:-python3}"
if ! "$PY" -m pip install -q -r "$ROOT/requirements.txt" 2>/dev/null; then
  echo "[setup] 系統 python 不讓裝套件，改用 $ROOT/.venv"
  "$PY" -m venv "$ROOT/.venv"
  PY="$ROOT/.venv/bin/python"
  "$PY" -m pip install -q -r "$ROOT/requirements.txt"
fi
echo "[setup] 基本套件 ok（$PY）"

if [ "$MODE" = "--minimal" ]; then
  echo "[setup] 完成（minimal）。render.py 會用 Pillow 引擎 + edge-tts。"; exit 0
fi

# GPU 工具：語音辨識（faster-whisper large-v3）、VAD、音高分析；沒有 GPU 也裝得起來（會用 CPU 的 small 模型）
"$PY" -m pip install -q -r "$ROOT/requirements-gpu.txt" || echo "[setup] ⚠ GPU 工具沒裝成，look.py 會略過語音辨識與聲音分析"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  # CTranslate2 要的 CUDA 函式庫用 pip 裝，不需要 root
  "$PY" -m pip install -q "nvidia-cublas-cu12" "nvidia-cudnn-cu12==9.*" || true
  echo "[setup] GPU：$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
fi

# Remotion 引擎：需要 node 18+；沒有的話 render.py 自動退回 Pillow 引擎
if command -v node >/dev/null 2>&1 && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 18 ]; then
  (cd "$ROOT/remotion" && npm install --silent --no-audit --no-fund && npx remotion browser ensure >/dev/null 2>&1) \
    && echo "[setup] Remotion 引擎 ok" || echo "[setup] ⚠ Remotion 沒裝成（render.py 會用 Pillow 引擎）"
  mkdir -p "$ROOT/remotion/public/fonts" && cp -n "$ROOT"/fonts/*.ttf "$ROOT/remotion/public/fonts/" 2>/dev/null || true
else
  echo "[setup] 沒有 node 18+：跳過 Remotion 引擎（render.py 會用 Pillow 引擎）"
fi

if [ "$MODE" = "--tts" ]; then
  bash "$ROOT/tts_local/setup_tts.sh"
fi
echo "[setup] 完成。"
