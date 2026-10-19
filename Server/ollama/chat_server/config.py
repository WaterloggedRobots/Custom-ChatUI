"""Settings, read once from the environment (or a .env file next to run.sh;
copy .env.example to .env to change them).

Addresses at a glance:
  LLM server (this program)  listens on SERVER_BIND_IP:SERVER_PORT; clients use
                             the machine's LAN IP, e.g. 192.168.0.192:8100
  Ollama                     OLLAMA_URL, normally on this same machine
  NAS                        NAS_IP / NAS_MOUNT, recorded here; nothing uses them yet
"""

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env(name: str, default: str, *old_names: str) -> str:
    """Read a setting; older variable names are still honoured."""
    for n in (name, *old_names):
        if os.getenv(n):
            return os.environ[n]
    return default


class Config:
    # ---- Paths on the server -------------------------------------------
    SERVER_DIR = Path(__file__).resolve().parent.parent      # Server/ollama in the git clone
    ENV_FILE = SERVER_DIR / ".env"                           # loaded by run.sh

    # ---- Network ---------------------------------------------------------
    # This server. 0.0.0.0 = reachable from the LAN; 127.0.0.1 = this machine only.
    # 8000 is taken by the vLLM start script, so this server defaults to 8100.
    SERVER_BIND_IP = _env("SERVER_BIND_IP", "0.0.0.0", "API_HOST")
    SERVER_PORT = int(_env("SERVER_PORT", "8100", "API_PORT"))
    # Ollama, which runs the models.
    # (OLLAMA_HOST is only borrowed when it is a full URL: Ollama itself also
    # reads OLLAMA_HOST, often as a bare bind address like 0.0.0.0.)
    OLLAMA_URL = _env("OLLAMA_URL", os.getenv("OLLAMA_HOST", "") if os.getenv("OLLAMA_HOST", "").startswith("http")
                      else "http://localhost:11434").rstrip("/")
    # NAS on the LAN (reserved for later; not used yet).
    NAS_IP = os.getenv("NAS_IP", "")
    NAS_MOUNT = os.getenv("NAS_MOUNT", "")

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # ---- Models ------------------------------------------------------------
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemma4:31b")
    # Model used for the small "does this need a web search?" step. Empty means
    # use the chat model itself, which avoids loading a second model.
    PLANNER_MODEL = os.getenv("PLANNER_MODEL", "")

    # Context window asked of Ollama. Its own default (4096 on many builds) is
    # too small once search results or a document are added.
    NUM_CTX = _int("NUM_CTX", 16384)
    KEEP_ALIVE = os.getenv("KEEP_ALIVE", "30m")

    # Web search
    SEARCH_RESULTS = _int("SEARCH_RESULTS", 6)
    SEARCH_FETCH_PAGES = _int("SEARCH_FETCH_PAGES", 3)
    SEARCH_PAGE_CHARS = _int("SEARCH_PAGE_CHARS", 3000)
    SEARCH_MIN_INTERVAL = float(os.getenv("SEARCH_MIN_INTERVAL", "1.0"))
    FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "8"))
    USER_AGENT = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    )

    # Attachments
    MAX_ATTACHMENT_BYTES = _int("MAX_ATTACHMENT_BYTES", 25 * 1024 * 1024)
    FILE_TEXT_CHARS = _int("FILE_TEXT_CHARS", 40000)

    # Speech to text for the GUI's mic button (speech.py)
    STT_ENGINE = os.getenv("STT_ENGINE", "auto")            # auto | whisper | google | off
    STT_MODEL = os.getenv("STT_MODEL", "")                  # "" = large-v3-turbo on a GPU, small on the CPU
    STT_DEVICE = os.getenv("STT_DEVICE", "auto")            # auto (GPU, else CPU) | cuda | cpu
    STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "")    # "" = float16 on a GPU, int8 on the CPU
    STT_MODEL_DIR = os.getenv("STT_MODEL_DIR", "")          # "" = the Hugging Face cache (~/.cache/huggingface)
    STT_PRELOAD = os.getenv("STT_PRELOAD", "0") not in ("0", "false", "no")
    STT_MAX_BYTES = _int("STT_MAX_BYTES", 50 * 1024 * 1024)

    # Optional extras (TTS and the experimental tool-calling endpoints)
    ENABLE_EXTRAS = os.getenv("ENABLE_EXTRAS", "1") not in ("0", "false", "no")
