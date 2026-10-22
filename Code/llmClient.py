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
  and is never sent back as context;
- with tools on, the model can use this PC's files, git and terminal
  (local_tools/). The server streams the model's tool calls back; they run
  here (changes and commands only after you allow them in the GUI), and the
  results go back to the model in the next request, for up to
  MAX_TOOL_ROUNDS rounds per answer;
- with a ComfyUI workflow picked for the chat, the model can ask for
  pictures in its reply; the server draws them and streams them back, and
  they are saved in CLIENT_DATA_DIR/Images and kept with the reply.
"""

import base64
import json
import mimetypes
import threading
import time
import uuid
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

import paths
from local_tools import ToolBox

DEFAULT_PORT = 8100

MAX_CONTEXT_MESSAGES = 20     # at least this many newest messages are sent verbatim
SUMMARY_REFRESH_STEP = 10     # re-summarise after this many more messages fall out of the window
SUMMARY_MAX_TOKENS = 600
ATTACHMENT_TURNS = 2          # user turns whose attachments are re-sent in full
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_TOOL_ROUNDS = 12          # tool calls the model may make in a row before it must answer
TOOL_LOG_CHARS = 3000         # how much of each tool result is kept in the saved chat
IMAGE_EXT_FOR = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


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
    done = Signal(dict)        # {"content", "thinking", "sources", "tools", "images", "usage", "timings", "aborted"}
    error = Signal(str)
    tool_event = Signal(dict)  # {"name", "summary", "kind", "state": running|ok|error|declined, "output"}
    # A change or command wants to run: {"name", "summary", "kind", "details"}. Answer with answer_approval().
    approval_needed = Signal(dict)
    # A picture is ready: {"path", "prompt", "negative_prompt", "workflow"}, or {"error", "prompt", ...}
    image = Signal(dict)

    def __init__(self):
        super().__init__()
        self.model_name = None
        self.temperature = 0.7
        self.preset = ""
        self.think = True
        self.web_mode = "auto"
        self.image_workflow = ""       # ComfyUI workflow id on the server; "" = no pictures
        self.image_mode = "auto"
        self.summary = None            # {"upto": n, "text": "..."} covering history[:n]

        # LLM server address: Code/UI/network.json, or your saved copy (see paths.py)
        server = paths.load_network().get("llm_server", {})
        self.server_ip = str(server.get("ip") or "127.0.0.1")
        self.server_port = int(server.get("port") or DEFAULT_PORT)

        self._worker = None
        self._abort = threading.Event()
        self._response = None

        self.tools_enabled = True
        self.toolbox = ToolBox(extract_text=self._extract_text)
        self._approval = threading.Event()
        self._approval_answer = "deny"

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
            r = requests.get(f"{self.base_url}/v1/models", params={"detailed": "true"}, timeout=4)
            r.raise_for_status()
            return r.json().get("data", [])
        except requests.RequestException as e:
            print(f"❌ Could not list models: {e}")
            return []

    def list_workflows(self) -> dict:
        """The server's ComfyUI workflows: {"data": [{"id", "name", "usable", "error"}], "comfyui", "error"}."""
        try:
            r = requests.get(f"{self.base_url}/v1/images/workflows", timeout=6)
        except requests.RequestException as e:
            raise RuntimeError(f"Can't reach the server at {self.base_url}: {e}") from e
        if r.status_code == 404:
            raise RuntimeError("The server is older than this app and can't draw pictures; update it")
        if r.status_code != 200:
            raise RuntimeError(f"Server error {r.status_code}: {r.text[:300]}")
        return r.json()

    def draw(self, prompt: str, negative: str, workflow: str) -> dict:
        """Draw one picture now (blocking; call it from a thread). Returns the saved image entry."""
        try:
            r = requests.post(f"{self.base_url}/v1/images/generations", timeout=(10, 900),
                              json={"prompt": prompt, "negative_prompt": negative, "workflow": workflow})
        except requests.RequestException as e:
            raise RuntimeError(f"Can't reach the server at {self.base_url}: {e}") from e
        if r.status_code != 200:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:300]
            raise RuntimeError(msg)
        return keep_image(r.json()["data"][0])

    def _extract_text(self, path: Path) -> str:
        """PDF/Word text for read_file, from the server's extractor."""
        with open(path, "rb") as f:
            r = requests.post(f"{self.base_url}/v1/files/extract", files={"file": (path.name, f)}, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"the server could not read {path.name}: {r.text[:300]}")
        return r.json().get("text", "")

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
        """(older, recent): recent is the verbatim window and starts with a user turn.

        The window moves in steps of SUMMARY_REFRESH_STEP rather than one message per
        turn (it holds MAX_CONTEXT_MESSAGES to MAX_CONTEXT_MESSAGES + step - 1 messages).
        Between steps the start of the prompt doesn't change, so Ollama reuses its
        cached prompt and only reads the new messages; the summary refreshes at the
        same steps."""
        cut = max(0, len(history) - MAX_CONTEXT_MESSAGES)
        cut -= cut % SUMMARY_REFRESH_STEP
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
        if self.tools_enabled:
            messages.append({"role": "system", "content": self.toolbox.system_note()})
        for i, m in enumerate(recent):
            if m["role"] not in ("user", "assistant"):
                continue
            content = self._content(m, i in full)
            if m.get("tools"):
                # Earlier tool use, in one line each, so follow-up questions have the context.
                used = "\n".join(f"- {t['summary'].splitlines()[0]} ({t['state']})" for t in m["tools"])
                content = f"[Tools used for this answer:\n{used}]\n\n{content}"
            drawn = [i["prompt"] for i in m.get("images") or [] if i.get("path") and i.get("prompt")]
            if drawn and isinstance(content, str):
                # So the model knows what it already showed, and can keep the looks the same.
                lines = "\n".join(f"- {p}" for p in drawn)
                content = f"{content}\n\n[Pictures you drew with this reply:\n{lines}]"
            messages.append({"role": m["role"], "content": content})
        return messages

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
        self._approval.set()
        r = self._response
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

    def answer_approval(self, answer: str):
        """From the GUI: "allow", "always" (this tool, until the app closes) or "deny"."""
        self._approval_answer = answer
        self._approval.set()

    def _ask_approval(self, name, summary, kind, args) -> bool:
        if not self.toolbox.needs_approval(name):
            return True
        self._approval.clear()
        self._approval_answer = "deny"
        details = json.dumps(args, indent=2, ensure_ascii=False)
        self.approval_needed.emit({"name": name, "summary": summary, "kind": kind,
                                   "details": details[:8000] + ("\n…" if len(details) > 8000 else "")})
        self._approval.wait()
        if self._abort.is_set():
            return False
        if self._approval_answer == "always":
            self.toolbox.always_allow.add(name)
        return self._approval_answer in ("allow", "always")

    def _run_tools(self, calls, result):
        """Run the model's tool calls in order; returns the role=tool messages."""
        replies = []
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
            except ValueError as e:
                args, output, state = {}, f"Error: arguments were not valid JSON ({e})", "error"
            else:
                state = None
            summary = self.toolbox.describe(name, args)
            kind = self.toolbox.kind(name)
            if state is None:
                if self._abort.is_set():
                    break
                if self._ask_approval(name, summary, kind, args):
                    self.tool_event.emit({"name": name, "summary": summary, "kind": kind, "state": "running"})
                    output = self.toolbox.run(name, args)
                    state = "error" if output.startswith("Error:") else "ok"
                elif self._abort.is_set():
                    break
                else:
                    output, state = "The user declined this action. Don't try it again; ask them or carry on without it.", "declined"
            entry = {"name": name, "summary": summary, "kind": kind, "state": state,
                     "output": output[:TOOL_LOG_CHARS] + ("…" if len(output) > TOOL_LOG_CHARS else "")}
            result["tools"].append(entry)
            self.tool_event.emit(entry)
            replies.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": output})
        return replies

    def _stream(self, payload, result):
        """One streamed request. Adds text to `result`; returns (this round's text, tool calls)."""
        text, calls = "", {}
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
                for img in chunk.get("images") or []:
                    entry = keep_image(img)
                    result["images"].append(entry)
                    self.image.emit(entry)
                if chunk.get("usage"):
                    for k, v in chunk["usage"].items():
                        result["usage"][k] = result["usage"].get(k, 0) + v
                    result["timings"] = chunk.get("timings", {})
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        result["thinking"] += delta["reasoning_content"]
                        self.thinking.emit(delta["reasoning_content"])
                    if delta.get("content"):
                        text += delta["content"]
                        result["content"] += delta["content"]
                        self.token.emit(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        # OpenAI streams may split one call over several chunks, keyed by index.
                        slot = calls.setdefault(tc.get("index", len(calls)),
                                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
        return text, [calls[k] for k in sorted(calls)]

    def _run(self, history):
        result = {"content": "", "thinking": "", "sources": [], "tools": [], "images": [], "usage": {},
                  "timings": {}, "aborted": False}
        try:
            older, _ = self._split_history(history)
            summary = self._summary_for(older)
            messages = self.build_messages(history, summary)
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                payload = {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": self.temperature,
                    "think": self.think,
                    # "Always" searches once per question, not again after every tool result.
                    "web_search": "off" if round_no and self.web_mode == "on" else self.web_mode,
                    "stream": True,
                }
                if self.image_workflow:
                    payload["image_workflow"] = self.image_workflow
                    payload["image_mode"] = self.image_mode
                if self.tools_enabled and round_no < MAX_TOOL_ROUNDS:
                    payload["tools"] = self.toolbox.schemas()
                text, calls = self._stream(payload, result)
                if not calls or result["aborted"]:
                    break
                if text and not text.endswith("\n"):
                    result["content"] += "\n\n"
                    self.token.emit("\n\n")
                messages = messages + [{"role": "assistant", "content": text, "tool_calls": calls}]
                messages += self._run_tools(calls, result)
                if self._abort.is_set():
                    result["aborted"] = True
                    break
                self.status.emit("Reading the results…")
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


def keep_image(img: dict) -> dict:
    """Save a picture from the server ({"b64_json", "mime", "prompt", ...}) in paths.IMAGE_DIR.
    Returns what the chat keeps: {"path", "prompt", "negative_prompt", "workflow"} or, if
    it failed, {"error", "prompt", ...}."""
    entry = {k: img.get(k, "") for k in ("prompt", "negative_prompt", "workflow")}
    if img.get("error") or not img.get("b64_json"):
        entry["error"] = img.get("error") or "the server sent no picture"
        return entry
    try:
        data = base64.b64decode(img["b64_json"])
        paths.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        ext = IMAGE_EXT_FOR.get(img.get("mime", ""), Path(img.get("filename", "")).suffix or ".png")
        path = paths.IMAGE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}{ext}"
        path.write_bytes(data)
    except (OSError, ValueError) as e:
        entry["error"] = f"couldn't save the picture: {e}"
        return entry
    entry["path"] = str(path)
    return entry
