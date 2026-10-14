#!/usr/bin/env bash
# 3090 計算節點：look.py（GPU Whisper、聲音分析、參考音檔）→ 補截圖 → clone 配音（Pillow 快速出一版，順便把語音快取好）
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH" PYTHONUTF8=1 HF_HUB_DISABLE_TELEMETRY=1
PY=/home/9447zzz/miniconda3/envs/py312/bin/python
SKILL=/home/9447zzz/askback-skill
C=/livingrooms/9447zzz/askback-cmp
hostname; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
CH=$(ls $SKILL/remotion/node_modules/.remotion/chrome-headless-shell/linux64/*/chrome-headless-shell 2>/dev/null | head -1)
[ -n "$CH" ] && echo "chrome 缺的函式庫：$(ldd $CH | grep 'not found' | awk '{print $1}' | tr '\n' ' ')"
declare -A SHOTS=([V01]="02:05 02:38" [V03]="" [V13]="01:28")
for ID in ${CASES:-V03 V13 V01}; do
  echo "################ $ID  $(date +%T)"
  cd "$C/$ID"
  [ -f work/voice/teacher.wav ] || $PY "$SKILL/scripts/look.py" 2>&1 | grep -v -i -E "warning|GLIBC"
  [ -n "${SHOTS[$ID]}" ] && $PY "$SKILL/scripts/look.py" --at ${SHOTS[$ID]} 2>&1 | grep -v GLIBC
  echo "--- 參考音檔逐字稿：$(cat work/voice/teacher.txt)"
  ASKBACK_TTS=clone $PY "$SKILL/scripts/render.py" --engine pillow --out output_pillow/askback.mp4 2>&1 | grep -v -i -E "warning|GLIBC|^  ✓"
done
echo "################ done $(date +%T)"
