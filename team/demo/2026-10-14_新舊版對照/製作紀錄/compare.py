#!/usr/bin/env python3
"""把舊版（repo 裡 team/outputs 的成品）與新版並排成一頁 compare/index.html，供人工對照。
   舊版：edge-tts 固定聲音 + Pillow 畫面（Mac 上做的）
   中間：同一份 lesson.json，Remotion 畫面 + edge-tts（只換畫面引擎）
   新版：同一份 lesson.json，Remotion 畫面 + clone 原片老師聲音（BreezyVoice，3090）
"""
import html, json, pathlib, re, shutil, subprocess, sys

C = pathlib.Path(__file__).resolve().parent
SKILL = pathlib.Path("/home/9447zzz/askback-skill")
OUT = C / "compare"
OUT.mkdir(exist_ok=True)
try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FF = "ffmpeg"


def dur(p):
    r = subprocess.run([FF, "-i", str(p)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def cers(log, cid):
    """從 log_<ID>.txt 抓 engine 每句的 cer。"""
    p = C / "log_all.txt"
    if not p.exists():
        return []
    txt = p.read_text(encoding="utf-8", errors="ignore")
    seg = txt.split(f"################ {cid}")[-1].split("################")[0]
    return [float(x) for x in re.findall(r"\[engine\] ok .*? cer=([\d.]+)", seg)]


rows = []
for cid in ("V01", "V03", "V13"):
    d = OUT / cid
    d.mkdir(exist_ok=True)
    items = {}
    for key, src in (("old", SKILL / "team/outputs" / cid / "askback.mp4"),
                     ("mid", C / cid / "output_edge/askback.mp4"),
                     ("new", C / cid / "output/askback.mp4"),
                     ("ref", C / cid / "work/voice/teacher.wav"),
                     ("prev_old", SKILL / "team/outputs" / cid / "preview.jpg"),
                     ("prev_new", C / cid / "output/preview.jpg")):
        if src.exists():
            dst = d / (key + src.suffix)
            shutil.copy(src, dst)
            items[key] = dst.relative_to(OUT).as_posix()
    case = json.loads((C / cid / "input/case_input.json").read_text(encoding="utf-8"))
    prof = {}
    pp = C / cid / "work/voice/profile.json"
    if pp.exists():
        prof = json.loads(pp.read_text(encoding="utf-8"))
    cs = cers(None, cid)
    rows.append(dict(cid=cid, case=case, items=items, prof=prof, cers=cs,
                     durs={k: dur(OUT / v) for k, v in items.items() if v.endswith(".mp4")}))


def video(r, k, label):
    if k not in r["items"]:
        return f'<div class="cell missing"><h4>{label}</h4><p>（還沒有產出）</p></div>'
    d = r["durs"].get(k)
    return (f'<div class="cell"><h4>{label}</h4><video controls preload="metadata" src="{r["items"][k]}"></video>'
            f'<p class="meta">{d:.1f} 秒</p></div>' if d else "")


parts = []
for r in rows:
    c, p = r["case"], r["prof"]
    ref = p.get("teacher_ref") or {}
    cs = r["cers"]
    cer_txt = (f"每句 CER：{', '.join(f'{x:.2f}' for x in cs)}（平均 {sum(cs)/len(cs):.3f}；0 = Whisper 聽回來一字不差）" if cs else "")
    parts.append(f"""
<section>
  <h2>{r['cid']}　{html.escape(c.get('title',''))}　<small>{html.escape(c.get('grade',''))}・{html.escape(c.get('subject',''))}・原片形式：{html.escape(c.get('style',''))}</small></h2>
  <p class="q">學生的問題（第 {c.get('timestamp_seconds')} 秒）：{html.escape(c.get('question',''))}</p>
  <div class="grid">
    {video(r, 'old', '舊版：edge-tts + Pillow')}
    {video(r, 'mid', '中間：edge-tts + Remotion（只換畫面）')}
    {video(r, 'new', '新版：clone 原片老師 + Remotion')}
  </div>
  <div class="ref">
    <h4>clone 用的參考音檔（從原片 {html.escape(str(ref.get('from_','?')))} 自動挑出，{ref.get('seconds','?')} 秒，SNR {ref.get('snr_db','?')} dB）</h4>
    {f'<audio controls src="{r["items"]["ref"]}"></audio>' if 'ref' in r['items'] else '<p>（沒有）</p>'}
    <p class="meta">逐字稿：{html.escape(str(ref.get('text') or ''))}</p>
    <p class="meta">原片老師：{'女聲' if p.get('gender')=='female' else '男聲' if p.get('gender') else '?'}，F0 {p.get('f0_hz','?')} Hz，{p.get('chars_per_sec','?')} 字/秒 → 語速 {p.get('rate','?')}。{cer_txt}</p>
  </div>
  <details><summary>每句講完時的畫面（舊 / 新）</summary>
    <div class="grid2">
      {f'<img src="{r["items"]["prev_old"]}">' if 'prev_old' in r['items'] else ''}
      {f'<img src="{r["items"]["prev_new"]}">' if 'prev_new' in r['items'] else ''}
    </div>
  </details>
</section>""")

page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AskBack 新舊版對照</title>
<style>
body{{font-family:"Noto Sans TC",system-ui,sans-serif;margin:0;padding:24px;background:#f6f5f0;color:#222}}
h1{{margin:0 0 4px}} .lead{{color:#555;margin:0 0 24px;max-width:70em}}
section{{background:#fff;border-radius:12px;padding:16px 20px;margin-bottom:28px;box-shadow:0 1px 3px #0002}}
h2 small{{font-weight:400;color:#666;font-size:.7em}} .q{{background:#fff8e1;padding:8px 12px;border-radius:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:14px}}
.cell video,.grid2 img{{width:100%;border-radius:8px;background:#000}} .cell h4,.ref h4{{margin:8px 0 6px}}
.meta{{color:#666;font-size:.9em;margin:4px 0}} .missing{{color:#999}} audio{{width:100%;max-width:520px}}
</style></head><body>
<h1>AskBack 新舊版對照</h1>
<p class="lead">三題都用<strong>同一份 lesson.json</strong>（舊版成品裡的腳本），所以內容一樣，差別只在流程：
舊版＝edge-tts 固定聲音＋Pillow 逐格畫；中間＝只把畫面換成 Remotion；新版＝Remotion＋用 BreezyVoice 在 3090 上 clone 原片老師的聲音（每句合成 3 次、Whisper 聽回來挑最準的）。
每支影片都標示「AI 生成教學示範，非原講者本人」。本頁含原講者聲線的 clone 結果，請勿公開散布。</p>
{''.join(parts)}
</body></html>"""
(OUT / "index.html").write_text(page, encoding="utf-8")
print(OUT / "index.html")
for r in rows:
    print(r["cid"], {k: round(v, 1) for k, v in r["durs"].items() if v}, "cer:", r["cers"])
