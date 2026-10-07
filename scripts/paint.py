#!/usr/bin/env python3
"""（選用）畫一張「沒有字」的插圖，風格參考原片截圖。

    python3 scripts/paint.py "一隻螞蟻的側面圖，身體三段分明" --ref work/look/end_4.jpg --out work/ant.png

只有在原片截圖裡找不到可用的圖、而且用 shape 也畫不出來時才用。
圖裡不要放任何文字、數字、算式——那些一律交給 lesson.json，由程式寫，才不會寫錯。
需要 OPENAI_API_KEY；沒有金鑰或失敗就會結束並回報，請改用截圖或 shape，不要卡在這裡。
"""
import argparse, base64, os, pathlib, sys

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("prompt")
ap.add_argument("--ref", help="原片截圖，當作風格參考")
ap.add_argument("--out", default="work/paint.png")
a = ap.parse_args()

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("沒有 OPENAI_API_KEY：跳過生圖，改用原片截圖或 shape。")
try:
    from openai import OpenAI
except ImportError:
    sys.exit("沒有 openai 套件（python3 -m pip install openai）：跳過生圖，改用原片截圖或 shape。")

prompt = (a.prompt + "\n沿用參考圖的配色、筆觸與構圖風格。畫面乾淨、主體單一、大量留白。"
          "圖中不要出現任何文字、數字、符號、標籤、浮水印或人物肖像。")
client, err = OpenAI(), None
models = [os.environ["ASKBACK_IMAGE_MODEL"]] if os.environ.get("ASKBACK_IMAGE_MODEL") else ["gpt-image-2", "gpt-image-1"]
for model in models:
    try:
        if a.ref:
            r = client.images.edit(model=model, image=open(a.ref, "rb"), prompt=prompt, size="1536x1024")
        else:
            r = client.images.generate(model=model, prompt=prompt, size="1536x1024")
        out = pathlib.Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(r.data[0].b64_json))
        print(f"{out}（{model}）。請先打開看過：有沒有畫錯、有沒有偷放字，再放進 lesson.json。")
        sys.exit(0)
    except Exception as e:
        err = e
sys.exit(f"生圖失敗（{err}）：改用原片截圖或 shape。")
