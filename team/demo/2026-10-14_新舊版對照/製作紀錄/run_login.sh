#!/usr/bin/env bash
# 登入節點：Remotion 畫面（語音直接用 GPU 階段的快取，不載入任何模型）＋ 中間對照（edge-tts + Remotion）
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH" PYTHONUTF8=1 ASKBACK_REMOTION_CONCURRENCY=3 ASKBACK_NO_GPU=1
PY=/home/9447zzz/miniconda3/envs/py312/bin/python
SKILL=/home/9447zzz/askback-skill
C=/livingrooms/9447zzz/askback-cmp
for ID in ${CASES:-V03 V13 V01}; do
  echo "################ $ID  $(date +%T)"
  cd "$C/$ID"
  ASKBACK_TTS=clone $PY "$SKILL/scripts/render.py" --engine remotion --out output/askback.mp4 2>&1 | grep -E "✓ output|✗|⚠|後端|旁白"
  ASKBACK_TTS=edge  $PY "$SKILL/scripts/render.py" --engine remotion --out output_edge/askback.mp4 2>&1 | grep -E "✓ output|✗|⚠"
done
