#!/usr/bin/env bash
# 在 3090 計算節點上：每題 look.py → 補截圖 → render.py（clone 原片老師 + Remotion），另做一支「新畫面＋舊聲音(edge)」當中間對照
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH" PYTHONUTF8=1 HF_HUB_DISABLE_TELEMETRY=1
PY=/home/9447zzz/miniconda3/envs/py312/bin/python
SKILL=/home/9447zzz/askback-skill
C=/livingrooms/9447zzz/askback-cmp
hostname; nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
declare -A SHOTS=([V01]="02:05 02:38" [V03]="" [V13]="01:28")
for ID in ${CASES:-V03 V13 V01}; do
  echo "################ $ID  $(date +%T)"
  cd "$C/$ID"
  $PY "$SKILL/scripts/look.py" 2>&1 | grep -v -i warning
  [ -n "${SHOTS[$ID]}" ] && $PY "$SKILL/scripts/look.py" --at ${SHOTS[$ID]}
  echo "--- 參考音檔：$(cat work/voice/teacher.txt)"
  ASKBACK_TTS=clone $PY "$SKILL/scripts/render.py" --engine remotion --out output/askback.mp4 2>&1 | grep -v -i warning
  ASKBACK_TTS=edge  $PY "$SKILL/scripts/render.py" --engine remotion --out output_edge/askback.mp4 2>&1 | grep -E "✓|✗|⚠"
done
echo "################ done $(date +%T)"
