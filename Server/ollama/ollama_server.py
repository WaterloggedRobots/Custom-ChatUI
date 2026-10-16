#!/usr/bin/env python3
"""ChatUI server entry point.

Wraps Ollama with an OpenAI-style /v1/chat/completions that adds native
reasoning (think), image and document input, and web search.
The code lives in chat_server/; settings come from environment variables
(see chat_server/config.py and README.md).
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

from chat_server.app import create_app
from chat_server.config import Config

logging.basicConfig(level=Config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

if __name__ == "__main__":
    print(f"ChatUI server on http://{Config.SERVER_BIND_IP}:{Config.SERVER_PORT}  (Ollama: {Config.OLLAMA_URL}, "
          f"default model: {Config.DEFAULT_MODEL})")
    uvicorn.run(app, host=Config.SERVER_BIND_IP, port=Config.SERVER_PORT, log_level=Config.LOG_LEVEL.lower())
