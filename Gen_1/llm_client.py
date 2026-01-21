import sys
import json
import threading
import requests
import time

from PySide6.QtCore import Qt, Signal, QObject

# ======================
# CONFIG
# ======================
ip = "http://192.168.0.247:8000"
VLLM_URL = ip+"/v1/chat/completions"  # CHANGE THIS
ADMIN_URL = ip-"8000"+"9000"+"/admin/switch_model"
MODEL_NAME = "openai/gpt-oss-20b"
MAX_CONTEXT_MESSAGES = 16      # keep last N messages verbatim
SUMMARY_TRIGGER_COUNT = 24     # when total messages exceed this
SUMMARY_MODEL_MAX_TOKENS = 512

# ======================
# Streaming signal bridge
# ======================
class StreamEmitter(QObject):
    token = Signal(str)
    done = Signal()


class LLMClient(QObject):
    token = Signal(str)
    done = Signal(str)

    def __init__(self):
        super().__init__()
        self.messages = []
        self.temperature = 0.7
        self.current_response = ""
        self.lock = threading.Lock()

    # ----------------------
    # Public API
    # ----------------------
    def add_user_message(self, text: str):
        with self.lock:
            self.messages.append({"role": "user", "content": text})

    def generate(self):
        self.current_response = ""
        threading.Thread(
            target=self._stream_request,
            daemon=True
        ).start()

    # ----------------------
    # Context management
    # ----------------------
    def get_context_messages(self):
        if len(self.messages) < SUMMARY_TRIGGER_COUNT:
            return list(self.messages)

        old = self.messages[:-MAX_CONTEXT_MESSAGES]
        recent = self.messages[-MAX_CONTEXT_MESSAGES:]

        summary = self._summarize(old)
        return [{"role": "system", "content": summary}] + recent

    def _summarize(self, messages):
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarize the conversation. Preserve goals, constraints, "
                        "technical decisions. Be concise."
                    )
                },
                *messages
            ],
            "temperature": 0.3,
            "max_tokens": SUMMARY_MODEL_MAX_TOKENS,
            "stream": False,
        }

        r = requests.post(VLLM_URL, json=payload, timeout=300)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ----------------------
    # Streaming logic
    # ----------------------
    def _stream_request(self):
        payload = {
            "model": MODEL_NAME,
            "messages": self.get_context_messages(),
            "temperature": self.temperature,
            "stream": True,
        }

        try:
            with requests.post(
                VLLM_URL,
                json=payload,
                stream=True,
                timeout=600
            ) as r:
                for line in r.iter_lines():
                    if not line or not line.startswith(b"data: "):
                        continue

                    data = line[6:]
                    if data == b"[DONE]":
                        break

                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]

                    if "content" in delta:
                        self.current_response += delta["content"]
                        self.token.emit(delta["content"])

        except Exception as e:
            self.current_response += f"\n[ERROR] {e}\n"
            self.token.emit(f"\n[ERROR] {e}\n")

        with self.lock:
            self.messages.append({
                "role": "assistant",
                "content": self.current_response
            })

        self.done.emit(self.current_response)

    def switch_model(self, model_name: str):
        self.abort_generation()
        self.messages.clear()
        self.current_response = ""

        try:
            r = requests.post(
                ADMIN_URL,
                params={"model": model_name},
                timeout=5
            )
            r.raise_for_status()
        except Exception as e:
            self.error.emit(f"Switch failed: {e}")
            return

        self.wait_for_server()
        self.model_name = model_name
        self.model_changed.emit(model_name)

    def wait_for_server(self, timeout=120):
        for _ in range(timeout):
            try:
                r = requests.get(
                    ip+"/v1/models",
                    timeout=2
                )
                if r.status_code == 200:
                    return
            except:
                pass
            time.sleep(1)

        raise RuntimeError("vLLM server did not restart")
