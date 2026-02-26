import json
import threading
import requests
import time
from PySide6.QtCore import Signal, QObject
from pathlib import Path

from enum import Enum

# ======================
# CONFIG
# ======================
#IP = "192.168.0.247"
PORT = "8000"


MAX_CONTEXT_MESSAGES = 16
SUMMARY_TRIGGER_COUNT = 24
SUMMARY_MODEL_MAX_TOKENS = 512

class LLMArch(str, Enum):
    VLLM="VLLM"
    OLLAMA="Ollama"
    FAIL="not connected"

class LLMStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    FREE = "free"
    THINKING = "thinking"
    RESPONDING = "responding"
    ERROR = "error"
    SWITCHING_MODEL = "switching_model"


class LLMClient(QObject):
    token = Signal(str)
    done = Signal(str)
    error = Signal(str)
    status_changed=Signal(str)
    model_changed = Signal(str)


    def __init__(self,fd):
        super().__init__()
        self.model_name = None
        self.temperature = 0.7
        self.max_tokens = 512

        self.directoryDefault = fd
        IP = self.read_json_file(str(self.directoryDefault) + r"\UI\config.json")
        self.ip = IP["ip"]
        self.llm = self.get_llm()
        self.port = PORT
        self.SERVER_URL = f"http://{self.ip}:{self.port}"
        #self.VLLM_URL = f"http://{self.ip}:{self.port}/v1/chat/completions"
        #self.MODELS_URL = f"http://{self.ip}:{self.port}/v1/models"
        #self.ADMIN_URL = f"http://{self.ip}:9000"
        #self.ADMIN_URL = f"http://{self.ip}:9000/api"  #vllm api server url

        self.preset = ""                 # system role, immutable
        self.messages = []               # visible chat
        self.payload_messages = []       # full payload

        self.current_response = ""
        self.lock = threading.Lock()

        self._abort_flag = False
        self.status = LLMStatus.NOT_CONNECTED

    def _set_status(self, status: LLMStatus):
        self.status = status
        self.status_changed.emit(status.value)

    def read_json_file(self, file_path):
        """Read a JSON file and return its contents"""
        try:
            # Convert to Path object for better handling
            json_path = Path(file_path)

            if not json_path.exists():
                print(f"❌ File not found in ChatUI: {file_path}")
                return None

            # Read the file
            with open(json_path, 'r', encoding='utf-8') as file:
                data = json.load(file)

            print(f"✅ JSON loaded successfully from: {json_path.name}")
            return data

        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON format: {e}")
            return None
        except Exception as e:
            print(f"❌ Error reading file: {e}")
            return None

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

        self._set_status(LLMStatus.THINKING)
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
            to_summarize = self._trim_for_summary(msgs[:-MAX_CONTEXT_MESSAGES])
            summary = self._summarize(to_summarize)
            payload.append({
                "role": "system",
                "content": f"Conversation summary:\n{summary}"
            })
            payload.extend(msgs[-MAX_CONTEXT_MESSAGES:])
        else:
            payload.extend(msgs)

        return payload

    def _trim_for_summary(self, messages, max_chars=12000):
        total = 0
        trimmed = []
        for msg in reversed(messages):
            total += len(msg["content"])
            if total > max_chars:
                break
            trimmed.insert(0, msg)
        return trimmed

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
            "stream": False,
            "temperature": 0.3,
            "num_predict": SUMMARY_MODEL_MAX_TOKENS
        }

        print("Summarizing", len(messages), "messages")
        r = requests.post(self.SERVER_URL+f"/v1/chat/completions", json=payload, timeout=300)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ----------------------
    # Streaming
    # ----------------------
    def _stream_request(self):
        payload = {
            "model": self.model_name,
            "messages": self._build_payload(),
            "stream": True,
            "temperature": self.temperature,
            "num_predict": self.max_tokens
        }

        try:
            with requests.post(
                self.SERVER_URL+f"/v1/chat/completions",
                json=payload,
                stream=True,
                timeout=600
            ) as r:
                for line in r.iter_lines(decode_unicode=True):
                    if self._abort_flag:
                        return

                    if not line:
                        continue

                    if line.strip() == "data: [DONE]":
                        break

                    if not line.startswith("data:"):
                        continue

                    data = line[len("data:"):].strip()

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choice = chunk.get("choices", [{}])[0]
                    delta = choice.get("delta", {})

                    content = delta.get("content")
                    if content:
                        if self.status != LLMStatus.RESPONDING:
                            self._set_status(LLMStatus.RESPONDING)
                        self.current_response += content
                        self.token.emit(content)


        except Exception as e:
            self.error.emit(str(e))
            self._set_status(LLMStatus.ERROR)
            return

        with self.lock:
            msg = {"role": "assistant", "content": self.current_response}
            self.messages.append(msg)
            self.payload_messages.append(msg)

        self.done.emit(self.current_response)
        self._set_status(LLMStatus.FREE)
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
        payload = {"model": model_name,
                   "parameters": {
                       "temperature": self.temperature,
                       "num_ctx": 16384,
                       "top_p": 0.95,
                   },
                   "system_prompt": "",
                   "keep_alive": -1
                   }

        r = requests.post(
            self.SERVER_URL +f"/v1/models/switch",
            json=payload,
            timeout=5
        )
        r.raise_for_status()
    """
    #vllm api
        def request_model_switch(self, model_name: str):
        r = requests.post(
            self.ADMIN_URL,
            params={"model": model_name},
            timeout=5
        )
        r.raise_for_status()
    """
    def abort_generation(self):
        self._abort_flag = True

    def wait_for_server_ready(self, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(self.SERVER_URL + f"/v1/models", timeout=2)
                if r.ok:
                    return True
            except requests.RequestException:
                self._set_status(LLMStatus.NOT_CONNECTED)
            time.sleep(1)
        raise RuntimeError("LLM server did not come back online")

    def switch_model(self, model_name: str):
        self._set_status(LLMStatus.SWITCHING_MODEL)
        self.abort_generation()
        self.request_model_switch(model_name)
        self.wait_for_server_ready()
        #reset server context
        self.model_name = model_name

    def get_model(self):
        r = requests.get(self.SERVER_URL+f"/v1/models/active", timeout=2)
        q = r.json()
        print(q)
        model_name = q['active_model']
        #print(model_name)
        return model_name

    def get_model_list(self):
        r = requests.get(self.SERVER_URL+f"/v1/models", timeout=2)
        q = r.json()
        print(q)
        model_name_list = []
        for i in range(len(q['data'])):
            model_name_list.append(q['data'][i]["id"])
        #print(model_name_list)
        return model_name_list

    def get_llm(self):
        try:
            r= requests.get(self.SERVER_URL+f"/type", timeout=2)
            q=r.json()
        except Exception as e:
            try:
                r = requests.get(self.ADMIN_URL + f"/type", timeout=2)
                q = r.json()
            except Exception as e:
                return LLMArch.FAIL

        response = LLMArch.OLLAMA if q['type']=='ollama' else LLMArch.VLLM
        return response