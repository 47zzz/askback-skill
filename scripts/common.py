"""三支腳本共用的小工具：找 ffmpeg、看有沒有 GPU、找本機 TTS 環境。"""
import functools, os, pathlib, shutil, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TTS_LOCAL = ROOT / "tts_local"


def die(msg):
    sys.exit(f"\n✗ {msg}\n")


def _works(exe) -> bool:
    try:
        return subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


@functools.lru_cache(maxsize=None)
def ffmpeg() -> str:
    """系統的 ffmpeg 要真的跑得起來才用（有些容器裡的 /usr/bin/ffmpeg 會因 glibc 不合而壞掉），否則用 imageio-ffmpeg 內附的。"""
    found = shutil.which("ffmpeg")
    if found and _works(found):
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if _works(exe):
            return exe
    except ImportError:
        pass
    die("找不到能用的 ffmpeg。請先執行：python3 -m pip install imageio-ffmpeg")


def ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found and _works(found):
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
    """node 18+。系統沒有就看 ~/.local/bin（setup.sh 會裝在那裡，計算節點也找得到）。"""
    local = pathlib.Path.home() / ".local" / "bin"
    if not shutil.which("node") and (local / "node").exists():
        os.environ["PATH"] = f"{local}:{os.environ.get('PATH', '')}"
    return bool(shutil.which("node") and shutil.which("npx"))


def remotion_ready() -> bool:
    return have_node() and (ROOT / "remotion" / "node_modules" / "remotion").exists()


def stamp(s):
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def preload_cuda():
    """faster-whisper（CTranslate2）要 libcublas.so.12 與 cuDNN 9。pip 裝的 nvidia-* 套件不在預設搜尋路徑，
    這裡先用 RTLD_GLOBAL 載入，免 root、免設 LD_LIBRARY_PATH。找不到就算了（會退回 CPU）。"""
    import ctypes, glob, site
    roots = [p for p in site.getsitepackages() + [site.getusersitepackages()] if p]
    for pat in ("nvidia/cuda_runtime/lib/libcudart.so.12", "nvidia/cublas/lib/libcublasLt.so.12", "nvidia/cublas/lib/libcublas.so.12",
                "nvidia/cudnn/lib/libcudnn.so.9", "nvidia/cudnn/lib/libcudnn_*.so.9"):
        for r in roots:
            for p in sorted(glob.glob(f"{r}/{pat}")):
                try:
                    ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
