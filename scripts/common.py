"""三支腳本共用的小工具：找 ffmpeg、看有沒有 GPU、找本機 TTS 環境。"""
import functools, os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TTS_LOCAL = ROOT / "tts_local"


def die(msg):
    sys.exit(f"\n✗ {msg}\n")


@functools.lru_cache(maxsize=None)
def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        die("找不到 ffmpeg。請先執行：python3 -m pip install imageio-ffmpeg")


def ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    sib = pathlib.Path(ffmpeg()).with_name("ffprobe")
    return str(sib) if sib.exists() else ""


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], **kw)


@functools.lru_cache(maxsize=None)
def gpu() -> dict | None:
    """有 NVIDIA GPU 就回傳 {"name", "vram_mb"}，沒有就 None。預設環境是一張 RTX 3090（24 GB）。"""
    if os.environ.get("ASKBACK_NO_GPU"):
        return None
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        name, mem = out.stdout.strip().splitlines()[0].split(",")
        return {"name": name.strip(), "vram_mb": int(float(mem))}
    except Exception:
        return None


def tts_local_python() -> pathlib.Path | None:
    """本機 voice-clone TTS 的 venv（由 tts_local/setup_tts.sh 建立）。"""
    p = pathlib.Path(os.environ.get("ASKBACK_TTS_VENV", TTS_LOCAL / ".venv")) / "bin" / "python"
    return p if p.exists() else None


def clone_ready() -> bool:
    return bool(gpu()) and tts_local_python() is not None


def have_node() -> bool:
    return bool(shutil.which("node") and shutil.which("npx"))


def remotion_ready() -> bool:
    return have_node() and (ROOT / "remotion" / "node_modules" / "remotion").exists()


def stamp(s):
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"
