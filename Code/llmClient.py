"""Client for the ChatUI server (Server/ollama/ollama_server.py).

The chat history in ChatMain is the single source of truth; every request
builds its message list from it here:

- the bot preset goes first as the system message;
- long chats keep the newest messages verbatim and fold older ones into a
  rolling summary, which is only refreshed after enough new messages have
  dropped out of the window (not on every request);
- images and files attached to recent user messages are sent inline
  (OpenAI content parts); older ones are replaced by a short note;
- the model's reasoning is requested with `think` and streamed separately,
  and is never sent back as context.
"""

import base64
import json
import mimetypes
import threading
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

import paths

DEFAULT_PORT = 8100

MAX_CONTEXT_MESSAGES = 20     # newest messages sent verbatim
SUMMARY_REFRESH_STEP = 10     # re-summarise after this many more messages fall out of the window
SUMMARY_MAX_TOKENS = 600
ATTACHMENT_TURNS = 2          # user turns whose attachments are re-sent in full
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
WEB_MODES = ("auto", "on", "off")


def attachment_kind(path) -> str:
    return "image" if Path(path).suffix.lower() in IMAGE_EXT else "file"


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class LLMClient(QObject):
    token = Signal(str)        # answer text, as it streams
    thinking = Signal(str)     # reasoning text, as it streams
    status = Signal(str)       # "Searching: …" and similar; "" clears it
    sources = Signal(list)     # [{"n", "title", "url"}]
    done = Signal(dict)        # {"content", "thinking", "sources", "usage", "timings", "aborted"}
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.model_name = None
        self.temperature = 0.7
        self.preset = ""
        self.think = True
        self.web_mode = "auto"
        self.summary = None            # {"upto": n, "text": "..."} covering history[:n]

        # LLM server address: Code/UI/network.json, or your saved copy (see paths.py)
        server = paths.load_network().get("llm_server", {})
        self.server_ip = str(server.get("ip") or "127.0.0.1")
        self.server_port = int(server.get("port") or DEFAULT_PORT)

        self._worker = None
        self._abort = threading.Event()
        self._response = None

    # ------------------------------------------------------------- settings

    @property
    def server_address(self) -> str:
        return f"{self.server_ip}:{self.server_port}"

    def set_server_address(self, text: str, save: bool = True):
        """Accepts '192.168.0.192' or '192.168.0.192:8100'."""
        self.server_ip, self.server_port = paths.split_address(text, self.server_port)
        if save:
            paths.save_server_address(self.server_ip, self.server_port)

    @property
    def base_url(self) -> str:
        return f"http://{self.server_address}"

    def set_model(self, model: str):
        self.model_name = model

    def set_preset(self, text: str):
        self.preset = (text or "").strip()

    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # --------------------------------------------------------------- server

    def list_models(self):
        """Models on the server, with capabilities when it reports them."""
        try:
            r = requests.get(f"{self.base_url}/v1/models", params={"detailed": "true"}, timeout=5)
            r.raise_for_status()
            return r.json().get("data", [])
        except requests.RequestException as e:
            print(f"❌ Could not list models: {e}")
            return []

    # ------------------------------------------------------------ messages

    def _content(self, msg, full_attachments: bool):
        text = msg.get("content", "")
        atts = msg.get("attachments") or []
        if not atts:
            return text
        if not full_attachments:
            names = ", ".join(a["name"] for a in atts)
            return f"{text}\n\n[Earlier attachments, no longer shown: {names}]".strip()
        parts = [{"type": "text", "text": text}]
        for a in atts:
            path = Path(a["path"])
            if not path.is_file():
                parts[0]["text"] += f"\n\n[Attachment {a['name']} is missing from {path}]"
                continue
            if a.get("kind") == "image":
                parts.append({"type": "image_url", "name": a["name"], "image_url": {"url": _data_url(path)}})
            else:
                parts.append({"type": "file", "file": {"filename": a["name"], "file_data": _data_url(path)}})
        return parts

    def _split_history(self, history):
        """(older, recent): recent is the verbatim window and starts with a user turn."""
        cut = max(0, len(history) - MAX_CONTEXT_MESSAGES)
        while cut < len(history) - 1 and history[cut]["role"] != "user":
            cut += 1
        return history[:cut], history[cut:]

    def build_messages(self, history, summary_text=None):
        _, recent = self._split_history(history)
        messages = []
        if self.preset:
            messages.append({"role": "system", "content": self.preset})
        if summary_text:
            messages.append({"role": "system", "content": f"Summary of the earlier conversation:\n{summary_text}"})
        user_idx = [i for i, m in enumerate(recent) if m["role"] == "user"]
        full = set(user_idx[-ATTACHMENT_TURNS:])
        for i, m in enumerate(recent):
            if m["role"] not in ("user", "assistant"):
                continue
            messages.append({"role": m["role"], "content": self._content(m, i in full)})
        return messages

    def export_payload(self, history):
        """Plain-text copy of what the model sees, saved in the chat file for reference."""
        payload = [{"role": "system", "content": self.preset}] if self.preset else []
        for m in history:
            payload.append({"role": m["role"], "content": self._content(m, False)})
        return payload

    # ------------------------------------------------------------- summary

    def _summary_for(self, older):
        """Summary text covering `older`, refreshing it only when it lags by SUMMARY_REFRESH_STEP."""
        if not older:
            return None
        s = self.summary
        if s and s.get("upto", 0) > len(older):
            s = None                                  # history was edited underneath it
        if s and len(older) - s["upto"] < SUMMARY_REFRESH_STEP:
            return s["text"]
        start = s["upto"] if s else 0
        new_msgs = [{"role": m["role"], "content": self._content(m, False)} for m in older[start:]]
        self.status.emit("Summarising earlier messages…")
        prompt = [
            {"role": "system", "content": "Update the running summary of a conversation. Keep goals, decisions, "
                                          "constraints, names, numbers and technical details. Be concise; "
                                          "write it as notes, not prose."},
            {"role": "user", "content": (f"Current summary:\n{s['text']}\n\n" if s else "")
                                        + "New messages:\n" + json.dumps(new_msgs, ensure_ascii=False)},
        ]
        r = requests.post(f"{self.base_url}/v1/chat/completions", json={
            "model": self.model_name, "messages": prompt, "think": False, "temperature": 0.2,
            "max_tokens": SUMMARY_MAX_TOKENS, "stream": False}, timeout=300)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        self.summary = {"upto": len(older), "text": text}
        return text

    # ------------------------------------------------------------- request

    def generate(self, history):
        """Answer the last user message in `history` (a list of chat messages)."""
        if not self.model_name:
            self.error.emit("No model selected")
            return
        if self.busy:
            self.error.emit("Still answering the previous message")
            return
        self._abort.clear()
        self._worker = threading.Thread(target=self._run, args=([dict(m) for m in history],), daemon=True)
        self._worker.start()

    def abort_generation(self):
        self._abort.set()
        r = self._response
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

    def _run(self, history):
        result = {"content": "", "thinking": "", "sources": [], "usage": {}, "timings": {}, "aborted": False}
        try:
            older, _ = self._split_history(history)
            summary = self._summary_for(older)
            payload = {
                "model": self.model_name,
                "messages": self.build_messages(history, summary),
                "temperature": self.temperature,
                "think": self.think,
                "web_search": self.web_mode,
                "stream": True,
            }
            with requests.post(f"{self.base_url}/v1/chat/completions", json=payload, stream=True,
                               timeout=(10, 600)) as r:
                self._response = r
                if r.status_code != 200:
                    try:
                        msg = r.json()["error"]["message"]
                    except Exception:
                        msg = r.text
                    raise RuntimeError(f"Server error {r.status_code}: {msg}")
                for line in r.iter_lines(decode_unicode=True):
                    if self._abort.is_set():
                        result["aborted"] = True
                        break
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if "error" in chunk:
                        raise RuntimeError(chunk["error"].get("message", str(chunk["error"])))
                    if "status" in chunk:
                        self.status.emit(chunk["status"])
                    if "sources" in chunk:
                        result["sources"] = chunk["sources"]
                        self.sources.emit(chunk["sources"])
                    if chunk.get("usage"):
                        result["usage"] = chunk["usage"]
                        result["timings"] = chunk.get("timings", {})
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            result["thinking"] += delta["reasoning_content"]
                            self.thinking.emit(delta["reasoning_content"])
                        if delta.get("content"):
                            result["content"] += delta["content"]
                            self.token.emit(delta["content"])
        except Exception as e:
            if self._abort.is_set():
                result["aborted"] = True
            else:
                if isinstance(e, requests.ConnectionError):
                    e = f"Can't reach the chat server at {self.base_url}. Is it running? (Server > Set IP)"
                self.status.emit("")
                self.error.emit(str(e))
                return
        finally:
            self._response = None
        self.status.emit("")
        self.done.emit(result)
