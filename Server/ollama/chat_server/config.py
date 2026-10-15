"""Settings, read once from the environment (or a .env file next to run.sh)."""

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class Config:
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemma4:31b")
    # Model used for the small "does this need a web search?" step. Empty means
    # use the chat model itself, which avoids loading a second model.
    PLANNER_MODEL = os.getenv("PLANNER_MODEL", "")

    HOST = os.getenv("API_HOST", "0.0.0.0")
    # 8000 is taken by the vLLM start script, so this server defaults to 8100.
    PORT = _int("API_PORT", 8100)
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

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

    # Optional extras (TTS/STT and the experimental tool-calling endpoints)
    ENABLE_EXTRAS = os.getenv("ENABLE_EXTRAS", "1") not in ("0", "false", "no")
