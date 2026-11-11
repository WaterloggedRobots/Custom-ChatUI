"""A project's own chat hands out work, and its sessions do it in the background.

ProjectDesk is what the project chat's tools (local_tools/project.py) call. The
model's tools run on the client's worker thread, so each call is carried over to
the GUI thread, where chats, links and files are changed, and waits for it there.

SessionRunner answers the sessions' briefs one at a time with a client of its
own, so you can keep talking in the project chat or open another. A session
that's open in the main window is answered there instead, where you watch it.
Each answer goes back to the project chat as a report.
"""

import json
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal

import chat_links
import llmClient
import paths

LEAD_MODEL = "gemma4"            # what a project's chat starts with, if the server has it
REPORT_CHARS = 6000              # of a session's answer, in its report


def fits_lead(model: dict) -> bool:
    """Whether a model can run a project's chat: it must think and call tools. Unknown capabilities pass."""
    caps = model.get("capabilities")
    return not caps or ("thinking" in caps and "tools" in caps)


def lead_model(models: dict):
    """The model a new project chat starts with: Gemma 4 (the largest), else the first that fits; None if none do."""
    fit = [mid for mid, m in models.items() if fits_lead(m)]
    gemma = [mid for mid in fit if mid.partition(":")[0] == LEAD_MODEL]

    def size(mid):
        tag = mid.partition(":")[2].lower().rstrip("b")
        try:
            return float(tag)
        except ValueError:
            return 0.0
    if gemma:
        return max(gemma, key=size)
    return fit[0] if fit else None


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


class ProjectDesk(QObject):
    """local_tools.project.Desk for one project's chat."""
    _call = Signal(object)

    def __init__(self, chat_main, project: str, personality):
        super().__init__()
        self.main = chat_main
        self.project = project
        self.personality = personality
        self._call.connect(self._run, Qt.BlockingQueuedConnection)

    def _run(self, job):
        try:
            job["result"] = job["fn"]()
        except Exception as e:           # a bug here must not leave the worker waiting
            job["result"] = f"Error: {type(e).__name__}: {e}"

    def _on_gui(self, fn):
        if QThread.currentThread() is self.thread():
            return fn()
        job = {"fn": fn}
        self._call.emit(job)
        return job.get("result")

    def sessions(self) -> list:
        return self._on_gui(self._sessions)

    def start(self, title, brief) -> str:
        return self._on_gui(lambda: self.main.start_session(self.project, title, brief))

    def assign(self, chat, brief) -> str:
        return self._on_gui(lambda: self.main.assign_session(self.project, chat, brief))

    def _sessions(self):
        main, links = self.main, self.main.links
        rows = []
        for chat in chat_links.sessions_of(links, self.project):
            summary = links["Summaries"].get(chat, {}).get("Text", "")
            if not summary:
                first = next((m.get("content", "") for m in _read(paths.CHAT_DIR / f"{chat}.json").get("Chat") or []
                              if m.get("role") == "user"), "")
                summary = " ".join(str(first).split())[:160]
            rows.append({"name": chat, "summary": summary, "state": main.runner.state_of(chat)})
        return rows


class SessionRunner(QObject):
    """Answers sessions' briefs in the background, one at a time."""
    changed = Signal()               # a session was queued, started or finished

    def __init__(self, chat_main):
        super().__init__(chat_main)
        self.main = chat_main
        self.queue = []              # chat names waiting
        self.origin = {}             # chat -> the project whose chat gets its report
        self.current = None          # the chat being answered
        self.in_place = False        # it's answered in the main window
        self.started = 0.0
        self.client = llmClient.LLMClient()
        self.client.done.connect(self._on_done)
        self.client.error.connect(self._on_error)
        self.client.approval_needed.connect(
            lambda req: self.main.on_approval_needed(req, client=self.client, who=self.current))
        self.client.chat_summarized.connect(self.main.on_chat_summarized)

    def state_of(self, chat) -> str:
        if chat == self.current:
            return "working"
        return "queued" if chat in self.queue else "idle"

    def busy_with(self, chat) -> bool:
        """It's being answered in the background, so the main window mustn't change it."""
        return chat == self.current and not self.in_place

    def add(self, chat, project=None):
        """Answer the chat's last message; with `project`, report back to that project's chat.
        A chat that's working already goes again afterwards, for the brief it just got."""
        if project:
            self.origin[chat] = project
        if chat not in self.queue:
            self.queue.append(chat)
            self.changed.emit()
        self.kick()

    def give_back(self, chat):
        """The main window stopped answering it (you opened another chat): do it in the background."""
        if chat == self.current and self.in_place:
            self.current, self.in_place = None, False
            self.queue.insert(0, chat)

    def rename(self, old, new):
        self.queue = [new if c == old else c for c in self.queue]
        if old in self.origin:
            self.origin[new] = self.origin.pop(old)
        if self.current == old:
            self.current = new

    def stop(self):
        self.queue = []
        if self.current and not self.in_place:
            self.client.abort_generation()

    def kick(self):
        """Start the next session, unless one is working. Call it again when the main window is free."""
        if self.current is not None:
            return
        main = self.main
        for chat in list(self.queue):
            path = paths.CHAT_DIR / f"{chat}.json"
            if not path.exists():
                self.queue.remove(chat)
                continue
            if chat == main.chat_name():
                if main.is_answering():
                    continue             # its own answer first; on_done kicks again
                self.queue.remove(chat)
                self.current, self.in_place = chat, True
                self.changed.emit()
                if not main.answer_open_chat():
                    self._finish(chat, None, "it had nothing to answer")
                return
            self.queue.remove(chat)
            data = _read(path)
            history = data.get("Chat") or []
            if not history or history[-1].get("role") != "user":
                continue
            self.current, self.in_place, self.started = chat, False, time.time()
            self._configure(chat, data)
            self.changed.emit()
            self.client.generate(history)
            return

    def _configure(self, chat, data):
        main, client = self.main, self.client
        settings = main.settings_of(chat, data)
        client.set_preset(settings["Pretext"])
        client.character = settings["Character"]
        client.role_note = ""
        client.temperature = settings["Temperature"]
        client.set_model(settings["Model"] or main.client.model_name)
        client.stage_note = None
        client.summary = data.get("Summary")
        client.contexts = main.client.contexts
        box = client.toolbox
        box.set_folders(data.get("Tools Folders") or [])
        box.set_repos(data.get("Tools Repos") or [])
        box.set_server_folders(data.get("Server Folders") or [])
        box.project = None
        box.link_scope = main.link_scope_of(chat)
        box.always_allow = main.client.toolbox.always_allow     # "Always allow" holds for the whole app
        caps = main.models.get(client.model_name, {}).get("capabilities")
        client.apply_options(settings, caps, paths.chat_memory_dir(chat))

    def _on_done(self, result):
        chat = self.current
        if chat is None or self.in_place:
            return
        msg = llmClient.reply_message(result, result.get("content", ""), self.started)
        path = paths.CHAT_DIR / f"{chat}.json"
        data = _read(path)
        if data:
            data.setdefault("Chat", []).append(msg)
            if self.client.summary:
                data["Summary"] = self.client.summary
            _write(path, data)
            if not result.get("aborted") and chat_links.summary_due(self.main.links, chat, len(data["Chat"])):
                self.client.summarize_chat(chat, data["Chat"])
        self._finish(chat, msg["content"] if not result.get("aborted") else None,
                     "it was stopped" if result.get("aborted") else None)

    def _on_error(self, err):
        if self.current is not None and not self.in_place:
            self._finish(self.current, None, err)

    def finished_in_place(self, chat, content, why=None):
        """The main window answered the session (or failed to)."""
        if chat == self.current and self.in_place:
            self._finish(chat, content, why)

    def _finish(self, chat, content, why):
        self.current, self.in_place = None, False
        self.main.session_finished(chat, content, why)
        self.changed.emit()
        self.kick()


def report_message(chat: str, content, why) -> dict:
    """What a project's chat is told when one of its sessions is done."""
    if content is None:
        text = f"Session {chat} couldn't finish its task: {why}"
    else:
        body = content.strip() or "(it answered with no text)"
        if len(body) > REPORT_CHARS:
            body = body[:REPORT_CHARS] + f"\n… [cut; read_linked_chat {chat} for the rest]"
        text = f"Report from session {chat}:\n\n{body}"
    return {"role": "user", "content": text, "ts": time.time(), "report": chat}
