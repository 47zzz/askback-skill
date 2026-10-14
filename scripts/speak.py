#!/usr/bin/env python3
"""語音：把每一句旁白變成 wav。render.py 會呼叫；也可以單獨跑來試聲音。

    python3 scripts/speak.py "一千五百九十八減二百六十，剩下一千三百三十八。" --voice teacher_f --out work/try.wav

後端（環境變數 ASKBACK_TTS 可指定）：
  auto   預設：有 GPU 而且 tts_local/ 裝好了就用 clone，否則用 edge
  clone  本機 voice-clone TTS（tts_local/engine.py，跑在 GPU 上）：聲音來自 tts_local/voices/<名字>.wav
  edge   edge-tts 的 zh-TW 聲音（免費、免金鑰、需要網路）
  openai / gtts / system   只當備援

同一句、同一個聲音、同一個後端只合成一次（work/audio/ 快取）。
clone 後端會一次把所有要合成的句子交給 engine.py（模型只載入一次），
再用 Whisper 把每句聽回來比對（CER），不像就重合成，挑最像的一次。
"""
import argparse, asyncio, hashlib, json, os, pathlib, shutil, sys, wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import TTS_LOCAL, die, ffmpeg, gpu, tts_local_python, run

EDGE_VOICES = ["zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural", "zh-TW-HsiaoYuNeural"]
BANK = TTS_LOCAL / "voices"


# ───────────────────────── 後端選擇 ─────────────────────────
def backend() -> str:
    want = os.environ.get("ASKBACK_TTS", "auto")
    if want != "auto":
        return want
    if gpu() and tts_local_python():
        return "clone"
    return "edge"


def voice_wav(voice: str) -> pathlib.Path:
    """聲音可以是 tts_local/voices 的名字，或任何 wav 的路徑（旁邊要有同名 .txt 逐字稿）。"""
    p = pathlib.Path(voice)
    return p if p.suffix == ".wav" else BANK / f"{voice}.wav"


def bank_voices() -> dict:
    """tts_local/voices/bank.json：每個參考聲音的名字、性別、音高、語速、授權來源。"""
    p = BANK / "bank.json"
    if not p.exists():
        return {}
    try:
        return {v["name"]: v for v in json.loads(p.read_text(encoding="utf-8")).get("voices", [])}
    except Exception as e:
        die(f"讀不了 {p}：{e}")


def default_clone_voice(work: pathlib.Path) -> str:
    """預設 clone 的聲音：原片老師（work/voice/teacher.wav，ASKBACK_CLONE_SOURCE=video，預設）；
    或聲音庫裡和原片最像的那個（ASKBACK_CLONE_SOURCE=bank，或原片沒抽到參考音檔時）。"""
    teacher = work / "voice" / "teacher.wav"
    if os.environ.get("ASKBACK_CLONE_SOURCE", "video") != "bank" and teacher.exists() and teacher.stat().st_size > 50000:
        return str(teacher.resolve())
    prof = work / "voice" / "profile.json"
    if prof.exists():
        try:
            m = json.loads(prof.read_text(encoding="utf-8")).get("match")
            if m and (BANK / f"{m}.wav").exists():
                return m
        except Exception:
            pass
    names = [n for n in bank_voices() if (BANK / f"{n}.wav").exists()]
    names += sorted(p.stem for p in BANK.glob("*.wav") if p.with_suffix(".txt").exists() and p.stem not in names)
    return names[0] if names else "demo_f"


# ───────────────────────── 共用：轉 wav、裁靜音、響度 ─────────────────────────
def to_wav(src, dst, loudnorm=False):
    af = ("silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
          "silenceremove=start_periods=1:start_threshold=-50dB,areverse")
    if loudnorm:
        af = "loudnorm=I=-16:TP=-1.5:LRA=11," + af
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-ac", "1", "-ar", "44100",
         "-af", af, "-c:a", "pcm_s16le", dst], check=True)


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


# ───────────────────────── 雲端／系統後端（逐句） ─────────────────────────
EDGE_FALLBACK = ["zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-CN-YunYangNeural"]   # 某個聲音暫時壞掉時依序換


def _edge(text, voice, rate, tmp):
    import edge_tts

    async def go(v):
        await edge_tts.Communicate(text, v, rate=rate).save(str(tmp))
    errors = []
    for v in [voice] + [f for f in EDGE_FALLBACK if f != voice]:
        for attempt in range(2):
            try:
                asyncio.run(asyncio.wait_for(go(v), 40))
                if tmp.exists() and tmp.stat().st_size > 500:
                    if v != voice:
                        print(f"  ⚠ edge-tts 的 {voice} 現在沒有回應，這句改用 {v}")
                    return
            except Exception as e:
                errors.append(f"{v}: {e}")
    raise RuntimeError("edge-tts 沒有回傳音訊（" + "; ".join(errors[-3:]) + "）")


def _openai(text, voice, rate, tmp):
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("沒有 OPENAI_API_KEY")
    from openai import OpenAI
    v = "onyx" if "Yun" in voice else "nova"
    with OpenAI().audio.speech.with_streaming_response.create(
            model=os.environ.get("ASKBACK_OPENAI_TTS", "gpt-4o-mini-tts"), voice=v, input=text,
            instructions="用臺灣華語、親切清楚的老師語氣朗讀。") as r:
        r.stream_to_file(str(tmp))


def _gtts(text, voice, rate, tmp):
    from gtts import gTTS
    gTTS(text, lang="zh-TW").save(str(tmp))


def _say(text, voice, rate, tmp):
    aiff = tmp.with_suffix(".aiff")
    if shutil.which("say"):
        run(["say", "-v", "Meijia", "-o", aiff, text], check=True)
    elif shutil.which("espeak-ng"):
        aiff = tmp.with_suffix(".wav")
        run(["espeak-ng", "-v", "cmn", "-s", "150", "-w", aiff, text], check=True)
    else:
        raise RuntimeError("沒有系統語音")
    shutil.move(str(aiff), str(tmp))


CLOUD = [("edge-tts", _edge, ".mp3"), ("openai", _openai, ".mp3"), ("gtts", _gtts, ".mp3"), ("system", _say, ".snd")]


def edge_voice_for(voice: str) -> str:
    """clone 的聲音名字 → 最接近的 edge-tts 聲音（備援用）。"""
    if voice.startswith("zh-"):
        return voice
    if voice.endswith(".wav"):                 # 原片老師：看 profile.json 的性別
        prof = pathlib.Path(voice).with_name("profile.json")
        try:
            g = json.loads(prof.read_text(encoding="utf-8")).get("gender")
        except Exception:
            g = None
        return "zh-TW-YunJheNeural" if g == "male" else "zh-TW-HsiaoChenNeural"
    v = bank_voices().get(voice, {})
    return "zh-TW-YunJheNeural" if v.get("gender") == "male" else "zh-TW-HsiaoChenNeural"


def speak_cloud(text, voice, rate, wav, cache, key):
    errors = []
    for name, fn, ext in CLOUD:
        tmp = cache / f"{key}_raw{ext}"
        try:
            fn(text, edge_voice_for(voice), rate, tmp)
            to_wav(tmp, wav)
            tmp.unlink(missing_ok=True)
            return wav
        except Exception as e:
            errors.append(f"{name}: {e}")
    die("所有語音服務都失敗。可改用環境裡任何 TTS 產生這一句的 wav，存成\n  "
        f"{wav}\n再重跑（render.py 會直接採用）。\n句子：{text}\n" + "\n".join(errors))


# ───────────────────────── 本機 clone 後端（整批） ─────────────────────────
def speak_clone(jobs: list, cache: pathlib.Path):
    """jobs: [{"text","voice","rate","wav","key"}]。一次交給 engine.py，模型只載入一次。"""
    py = tts_local_python()
    if not py:
        raise RuntimeError("tts_local/.venv 不存在（先跑 tts_local/setup_tts.sh）")
    for j in jobs:
        if not voice_wav(j["voice"]).exists():
            raise RuntimeError(f"找不到聲音「{j['voice']}」。tts_local/voices/ 裡可用：" + "、".join(sorted(p.stem for p in BANK.glob('*.wav'))))
    jf = cache / "clone_jobs.json"
    raw = [{"text": j["text"], "voice": j["voice"], "rate": j["rate"], "out": str(cache / f"{j['key']}_raw.wav")} for j in jobs]
    jf.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", TOKENIZERS_PARALLELISM="false")
    r = run([py, TTS_LOCAL / "engine.py", jf, "--best-of", os.environ.get("ASKBACK_BEST_OF", "3")], env=env)
    if r.returncode != 0:
        raise RuntimeError(f"engine.py 失敗（exit {r.returncode}）")
    for j, rj in zip(jobs, raw):
        if not pathlib.Path(rj["out"]).exists():
            raise RuntimeError(f"engine.py 沒有產出：{rj['out']}")
        to_wav(rj["out"], j["wav"], loudnorm=True)
        pathlib.Path(rj["out"]).unlink(missing_ok=True)
    jf.unlink(missing_ok=True)


# ───────────────────────── 對外：一次處理整支影片的所有句子 ─────────────────────────
def cache_key(be, voice, rate, text):
    return hashlib.sha1(f"{be}|{voice}|{rate}|{text}".encode()).hexdigest()[:16]


def speak_all(beats: list, cache: pathlib.Path, work: pathlib.Path) -> str:
    """每個 beat 要有 say、voice、rate；回填 b["wav"]。回傳實際用到的後端名稱。"""
    cache.mkdir(parents=True, exist_ok=True)
    be = backend()
    if be == "clone":
        for b in beats:
            if b["voice"].startswith("zh-"):          # lesson 裡寫的是 edge 聲音名：換成 bank 裡最接近的
                b["voice"] = clone_voice_for(b["voice"], work)
        todo = []
        for b in beats:
            b["key"] = cache_key("clone", b["voice"], b["rate"], b["say"])
            b["wav"] = cache / f"{b['key']}.wav"
            if not b["wav"].exists():
                todo.append(dict(text=b["say"], voice=b["voice"], rate=b["rate"], wav=b["wav"], key=b["key"]))
        if todo and not gpu() and not os.environ.get("ASKBACK_CLONE_ON_CPU"):
            print(f"  ⚠ 沒有 GPU，{len(todo)} 句沒有 clone 快取：不在 CPU 上跑 clone 模型（太慢、太吃記憶體），這次改用 edge-tts。")
            be = "edge"
        elif todo:
            try:
                g = gpu()
                print(f"  本機 clone TTS（{g['name'] if g else 'CPU，會很慢'}）：{len(todo)} 句要合成，聲音：" + "、".join(sorted({pathlib.Path(j['voice']).stem for j in todo})))
                speak_clone(todo, cache)
            except Exception as e:
                print(f"  ⚠ 本機 clone TTS 失敗（{e}），這次改用 edge-tts。")
                be = "edge"
    if be != "clone":
        for b in beats:
            b["key"] = cache_key("cloud", edge_voice_for(b["voice"]), b["rate"], b["say"])
            b["wav"] = cache / f"{b['key']}.wav"
            if not b["wav"].exists():
                speak_cloud(b["say"], b["voice"], b["rate"], b["wav"], cache, b["key"])
    for b in beats:
        b["dur"] = wav_len(b["wav"])
    return be


def clone_voice_for(edge_name: str, work: pathlib.Path) -> str:
    """lesson 裡寫了 edge 聲音名（通常是角色劇的第二個角色）：預設聲音性別相同就用預設，否則找庫裡同性別的。"""
    d = default_clone_voice(work)
    want = "male" if "Yun" in edge_name else "female"
    if edge_voice_for(d).startswith("zh-TW-Yun") == (want == "male"):
        return d
    same = [n for n, v in bank_voices().items() if v.get("gender") == want and (BANK / f"{n}.wav").exists()]
    return same[0] if same else d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text")
    ap.add_argument("--voice", default=None, help="clone：tts_local/voices 的名字；edge：zh-TW-… 名字")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--out", default="work/try.wav")
    ap.add_argument("--work", default="work")
    a = ap.parse_args()
    work = pathlib.Path(a.work)
    voice = a.voice or (default_clone_voice(work) if backend() == "clone" else EDGE_VOICES[0])
    beats = [dict(say=a.text, voice=voice, rate=a.rate)]
    be = speak_all(beats, work / "audio", work)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(beats[0]["wav"], out)
    print(f"{out}　{beats[0]['dur']:.2f} 秒　後端：{be}　聲音：{beats[0]['voice']}")


if __name__ == "__main__":
    main()
