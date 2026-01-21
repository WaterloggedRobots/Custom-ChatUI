import json
import threading
import requests
import time
from PySide6.QtCore import Signal, QObject

# ======================
# CONFIG
# ======================
IP = "http://192.168.0.247"
PORT = "8000"
VLLM_URL = f"{IP}:{PORT}/v1/chat/completions"
MODELS_URL = f"{IP}:{PORT}/v1/models"
ADMIN_URL = f"{IP}:9000/admin/switch_model"

MAX_CONTEXT_MESSAGES = 16
SUMMARY_TRIGGER_COUNT = 24
SUMMARY_MODEL_MAX_TOKENS = 512


class LLMClient(QObject):
    token = Signal(str)
    done = Signal(str)
    error = Signal(str)
    model_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.model_name = None
        self.temperature = 0.7

        self.preset = ""                 # system role, immutable
        self.messages = []               # visible chat
        self.payload_messages = []       # full payload

        self.current_response = ""
        self.lock = threading.Lock()

        self._abort_flag = False

    # ----------------------
    # Configuration
    # ----------------------
    def set_model(self, model: str):
        self.model_name = model

    def set_preset(self, text: str):
        self.preset = text.strip()

    # ----------------------
    # Chat API
    # ----------------------
    def add_user_message(self, text: str):
        with self.lock:
            msg = {"role": "user", "content": text}
            self.messages.append(msg)
            self.payload_messages.append(msg)

    def generate(self):
        if not self.model_name:
            self.error.emit("No model selected")
            return

        self._abort_flag = False
        self.current_response = ""
        """
        if self.preset:
            if not self.payload_messages or self.payload_messages[0]["role"] != "system":
                self.payload_messages.insert(0, {
                    "role": "system",
                    "content": self.preset
                })
        """

        threading.Thread(
            target=self._stream_request,
            daemon=True
        ).start()

    # ----------------------
    # Context logic
    # ----------------------
    def _build_payload(self):
        payload = []

        # 1️⃣ Preset (never summarized)
        if self.preset:
            payload.append({
                "role": "system",
                "content": self.preset
            })

        # 2️⃣ Summarize ONLY payload_messages
        msgs = list(self.payload_messages)

        if len(msgs) > SUMMARY_TRIGGER_COUNT:
            summary = self._summarize(msgs[:-MAX_CONTEXT_MESSAGES])
            payload.append({
                "role": "system",
                "content": f"Conversation summary:\n{summary}"
            })
            payload.extend(msgs[-MAX_CONTEXT_MESSAGES:])
        else:
            payload.extend(msgs)

        return payload

    def _summarize(self, messages):
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarize the conversation. Preserve goals, constraints, "
                        "technical details. Be concise."
                    )
                },
                *messages
            ],
            "temperature": 0.3,
            "max_tokens": SUMMARY_MODEL_MAX_TOKENS,
            "stream": False
        }

        r = requests.post(VLLM_URL, json=payload, timeout=300)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ----------------------
    # Streaming
    # ----------------------
    def _stream_request(self):
        payload = {
            "model": self.model_name,
            "messages": self._build_payload(),
            "temperature": self.temperature,
            "stream": True
        }

        try:
            with requests.post(
                VLLM_URL,
                json=payload,
                stream=True,
                timeout=600
            ) as r:
                for line in r.iter_lines():
                    if self._abort_flag:
                        return

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
            self.error.emit(str(e))
            return

        with self.lock:
            msg = {"role": "assistant", "content": self.current_response}
            self.messages.append(msg)
            self.payload_messages.append(msg)

        self.done.emit(self.current_response)

    # ----------------------
    # Persistence helpers
    # ----------------------
    def export_payload(self):
        """Full payload sent to LLM (including preset)."""
        payload = []
        if self.preset:
            payload.append({
                "role": "system",
                "content": self.preset
            })
        payload.extend(self.payload_messages)
        return payload


    def import_payload(self, payload):
        """Restore payload from disk."""
        self.messages.clear()
        self.payload_messages.clear()
        #self.preset = ""

        for msg in payload:
            if msg["role"] == "system" and not self.preset:
                self.preset = msg["content"]
            else:
                self.payload_messages.append(msg)
                if msg["role"] in ("user", "assistant"):
                    self.messages.append(msg)

    # ----------------------
    # Switch Models
    # ----------------------
    def request_model_switch(self, model_name: str):
        r = requests.post(
            ADMIN_URL,
            params={"model": model_name},
            timeout=5
        )
        r.raise_for_status()

    def abort_generation(self):
        self._abort_flag = True

    def wait_for_server_ready(self, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(MODELS_URL, timeout=2)
                if r.ok:
                    return True
            except requests.RequestException:
                pass
            time.sleep(1)
        raise RuntimeError("LLM server did not come back online")

    def switch_model(self, model_name: str):
        self.abort_generation()
        self.request_model_switch(model_name)
        self.wait_for_server_ready()
        #reset server context
        self.model_name = model_name

    def get_model(self):
        r = requests.get(MODELS_URL, timeout=2)
        q = r.json()
        #print(q)
        model_name = q['data'][0]["id"]
        #print(model_name)
        return model_name