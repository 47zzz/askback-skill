#!/bin/bash
# 把剛跑完的一題存進 team/outputs/，方便組員互相看。
#   用法（在有 input/ 和 output/ 的那一層執行）：
#     bash <repo>/team/save_output.sh
#   題號會從 input/case_input.json 讀出來；同一題再存一次會覆蓋舊的。
set -e
here="$(cd "$(dirname "$0")" && pwd)"
[ -f output/askback.mp4 ] || { echo "找不到 output/askback.mp4：請在有 input/ 和 output/ 的那一層執行"; exit 1; }
id=$(python3 -c "import json;print(json.load(open('input/case_input.json'))['case_id'])")
dst="$here/outputs/$id"
mkdir -p "$dst"
cp output/askback.mp4 output/preview.jpg "$dst/"
cp work/lesson.json "$dst/lesson.json" 2>/dev/null || cp output/lesson.json "$dst/lesson.json"
echo "已存到 $dst"
echo "接著：cd $here/.. && git add team/outputs/$id && git commit -m \"output: $id\" && git push"
