import llmClient
import paths

from PySide6.QtWidgets import (QMainWindow, QSizePolicy, QFileDialog, QComboBox, QCheckBox, QLabel, QMenu,
                               QMessageBox, QPushButton, QToolButton, QWidget, QHBoxLayout, QWidgetAction)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, QObject, QEvent, QUrl, QTimer
from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor, QDesktopServices
import html
import json
from pathlib import Path, PurePath
import os
import markdown
import time
from datetime import datetime

MUTED = "#8a8a8a"
CAP_ICONS = (("vision", "👁"), ("thinking", "💭"), ("tools", "🔧"))
WEB_LABELS = {"auto": "Web: Auto", "on": "Web: Always", "off": "Web: Off"}
TOOL_STATE_ICONS = {"ok": "✓", "error": "⚠", "declined": "✗ declined"}
APP_VERSION = "0.8.0_Folders"


def short_path(path) -> str:
    """C:\\Users\\me\\Projects\\ChatUI -> C:/.../Projects/ChatUI (drive, parent, folder)."""
    p = path if isinstance(path, PurePath) else Path(path)
    drive = (p.drive or "").replace("\\", "/").rstrip("/")
    names = list(p.parts[1:])
    if not names:
        return f"{drive}/"
    if len(names) <= 2:
        return "/".join([drive, *names])
    return "/".join([drive, "...", names[-2], names[-1]])


class LiveComboBox(QComboBox):
    """A combo box that refreshes its items each time it is opened."""

    def __init__(self, refresh, parent=None):
        super().__init__(parent)
        self._refresh = refresh

    def showPopup(self):
        self._refresh()
        super().showPopup()


class ShiftEnterFilter(QObject):
    """
    Intercepts Enter/Return on a QPlainTextEdit.
    - Shift+Enter → inserts a literal newline.
    - Enter        → calls ChatMain.sendMessage() and suppresses the
                      default newline insertion.
    """
    def __init__(self, chat_main, input_widget, parent=None):
        super().__init__(parent)      # parent is only for Qt's ownership
        self.chat_main = chat_main    # reference to ChatMain
        self.input_widget = input_widget

    def eventFilter(self, obj, event):
        # Only act on the input widget
        if obj is not self.input_widget:
            return False

        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                # Shift‑Enter → literal newline
                self.input_widget.insertPlainText("\n")
                return True            # stop further processing

            # Plain Enter → send the message
            self.chat_main.sendMessage()
            return True                # stop further processing

        return False                    # let Qt process other events


class ChatMain(QMainWindow):
    """Main chat window.

    `chat_markdown` is the conversation and the only source of truth: the LLM
    request is rebuilt from it every time (see llmClient.build_messages), so
    editing, deleting and regenerating can't leave a stale or duplicated
    payload behind. Each message is a dict:
      user:      role, content (plain text), ts, attachments [{name, path, kind}]
      assistant: role, content (markdown), ts, response_time,
                 thinking, thinking_time, sources [{n, title, url}], usage,
                 tools [{name, summary, kind, state, output}]
    """

    def __init__(self):
        super().__init__()
        self.isShowImgWindow = True
        self.isShowImgWindowLock = False
        self.isShowChatList = False
        self.bot_path = None
        self.bot_desc_path = None
        self.botName = ""
        self.chat_markdown = []
        self.chat_path = None  # active chat file path
        self.pending_attachments = []
        self.expanded_thinking = set()   # indices of assistant messages showing their reasoning
        self.expanded_tools = set()      # indices of assistant messages showing their tool output

        # Streaming state
        self.response_start_pos = None
        self.response_start_time = time.time()
        self.thinking_started_at = None
        self.thinking_time = None
        self.stream_phase = None          # None | "thinking" | "answer"

        self.client = llmClient.LLMClient()
        self.client.token.connect(self.on_token)
        self.client.thinking.connect(self.on_thinking)
        self.client.status.connect(self.on_status)
        self.client.done.connect(self.on_done)
        self.client.error.connect(self.on_error)
        self.client.tool_event.connect(self.on_tool_event)
        self.client.approval_needed.connect(self.on_approval_needed)

        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(paths.ui_file("Chat Window.ui"))
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("ChatUI")
                self.chat = self.ui.textBrowser
                self.input = self.ui.plainTextEdit
                self.chat.setOpenLinks(False)
                self.chat.setOpenExternalLinks(False)
                self.add_controls()
                self.hideChatList()
                lastChat = self.get_lastChat()
                self.load_chat(lastChat)
                QTimer.singleShot(0, self.refresh_models)

        except Exception as e:
            print(f"Error loading UI: {e}")

    def add_controls(self):
        """Web search mode, reasoning toggle and a status line, added to the bottom bar."""
        bar = self.ui.horizontalLayout_4
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{MUTED}")
        bar.insertWidget(0, self.status_label, 1)

        # Models come straight from Ollama through the server, refreshed whenever the list is opened.
        self.models = {}
        self.model_combo = LiveComboBox(self.refresh_models)
        self.model_combo.setToolTip("Models on the server's Ollama. ● = loaded in memory, 👁 vision, "
                                    "💭 thinking, 🔧 tools")
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.model_combo.setStyleSheet("background:rgb(57,57,57);color:white")
        self.model_combo.activated.connect(self.on_model_chosen)
        bar.insertWidget(bar.count() - 1, self.model_combo)

        self.web_combo = QComboBox()
        for mode in llmClient.WEB_MODES:
            self.web_combo.addItem(WEB_LABELS[mode], mode)
        self.web_combo.setToolTip("Auto: the model decides when a question needs a web search")
        self.web_combo.setStyleSheet("background:rgb(57,57,57);color:white")
        self.think_check = QCheckBox("Think")
        self.think_check.setLayoutDirection(Qt.RightToLeft)      # box to the right of the label
        self.think_check.setChecked(True)
        self.think_check.setToolTip("Let the model reason before answering (thinking models only)")
        bar.insertWidget(bar.count() - 1, self.web_combo)
        bar.insertWidget(bar.count() - 1, self.think_check)

        # Tools on this PC: files, git and terminal (local_tools/). Changes and commands ask first.
        self.tools_check = QCheckBox("Tools")
        self.tools_check.setLayoutDirection(Qt.RightToLeft)
        self.tools_check.setChecked(True)
        self.tools_check.setToolTip("Let the model browse and edit files, use git and run commands on this PC. "
                                    "It asks you before any change or command.")
        # 📁 links another folder; ▾ drops down the linked folders, each with an x to remove it.
        self.folder_button = QPushButton("📁")
        self.folder_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.folder_button.clicked.connect(self.add_tools_folder)
        self.folder_list_button = QToolButton()
        self.folder_list_button.setText("▾")
        self.folder_list_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.folder_list_button.setToolTip("Linked folders")
        self.folder_menu = QMenu(self)
        self.folder_menu.setStyleSheet("QMenu{background:rgb(45,45,45);color:white}")
        self.folder_list_button.clicked.connect(self.show_folder_menu)
        bar.insertWidget(bar.count() - 1, self.tools_check)
        bar.insertWidget(bar.count() - 1, self.folder_button)
        bar.insertWidget(bar.count() - 1, self.folder_list_button)
        self.tools_check.toggled.connect(self.on_options_changed)
        self._show_tools_folder()
        self.ui.label.setText(APP_VERSION)

        self.web_combo.currentIndexChanged.connect(self.on_options_changed)
        self.think_check.toggled.connect(self.on_options_changed)

    def setup_connections(self):
        """Connect button signals to functions"""
        self.ui.pushButton.clicked.connect(self.toggle_graphics_view)
        self.ui.pushButton_2.clicked.connect(self.toggle_chat_list)
        self.ui.pushButton_5.clicked.connect(self.sendMessage)
        self.ui.pushButton_3.clicked.connect(self.on_file_button)

        self.ui.actionNew_Chat.setShortcut("Ctrl+N")
        self.ui.actionLoad_Chat.setShortcut("Ctrl+O")
        self.ui.actionChat_Settings.setShortcut("Ctrl+E")

        self.ui.pushButton_6.clicked.connect(self.regenerate_last_response) #regen

        self.ui.actionLoad_Chat.triggered.connect(self.selectChat)

        self.ui.actionDelete_Message.triggered.connect(self.delete_last_user_exchange)

        self.chat.anchorClicked.connect(self.on_anchor)

        self.input = self.ui.plainTextEdit
        self._shift_filter = ShiftEnterFilter(self, self.input, parent=self)
        self.input.installEventFilter(self._shift_filter)

    # ------------------------------------------------------------------
    # Options and attachments
    # ------------------------------------------------------------------

    def refresh_models(self):
        """Ask the server which models Ollama has and fill the model list. Returns their names."""
        found = [m for m in self.client.list_models()
                 if "completion" in m.get("capabilities", ["completion"]) or not m.get("capabilities")]
        if found or not self.models:
            self.models = {m["id"]: m for m in found}
        if not found:
            self.status_label.setText(f"Can't get the model list from {self.client.base_url}")
        self._fill_model_combo()
        return list(self.models)

    def _model_label(self, m):
        icons = "".join(icon for cap, icon in CAP_ICONS if cap in m.get("capabilities", []))
        size = m.get("parameter_size") or ""
        return f"{'●' if m.get('loaded') else '○'} {m['id']}  {size} {icons}".rstrip()

    def _fill_model_combo(self):
        combo, current = self.model_combo, self.client.model_name
        combo.blockSignals(True)
        combo.clear()
        for mid, m in self.models.items():
            combo.addItem(self._model_label(m), mid)
            ctx = m.get("context_length")
            combo.setItemData(combo.count() - 1, ", ".join(
                x for x in (m.get("parameter_size"), m.get("quantization"),
                            f"context {ctx:,}" if ctx else None,
                            "loaded" if m.get("loaded") else "not loaded") if x), Qt.ToolTipRole)
        if current and current not in self.models:
            combo.addItem(f"   {current} (not on the server)", current)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.blockSignals(False)
        self._update_think_check()

    def on_model_chosen(self, index):
        model = self.model_combo.itemData(index)
        if model and model != self.client.model_name:
            self.client.set_model(model)
            self.client.summary = None      # the old summary was written by another model; harmless to redo
            self.save_chat()
        self._update_think_check()

    def _update_think_check(self):
        caps = self.models.get(self.client.model_name, {}).get("capabilities")
        can_think = not caps or "thinking" in caps
        self.think_check.setEnabled(can_think)
        self.think_check.setToolTip("Let the model reason before answering" if can_think
                                    else f"{self.client.model_name} can't think; this setting is ignored")
        can_call = not caps or "tools" in caps
        self.tools_check.setEnabled(can_call)
        if not can_call:
            self.tools_check.setToolTip(f"{self.client.model_name} can't call tools; pick a model marked 🔧")
        self.on_options_changed()

    def on_options_changed(self, *_):
        self.client.web_mode = self.web_combo.currentData() or "auto"
        self.client.think = self.think_check.isChecked()
        self.client.tools_enabled = self.tools_check.isChecked() and self.tools_check.isEnabled()

    def add_tools_folder(self):
        box = self.client.toolbox
        start = box.folders[-1].parent if box.folders else box.working_dir
        folder = QFileDialog.getExistingDirectory(self, "Link a folder for the tools", str(start))
        if folder:
            box.add_folder(folder)
            self._show_tools_folder()
            self.save_chat()

    def remove_tools_folder(self, folder):
        self.client.toolbox.remove_folder(folder)
        self._show_tools_folder()
        self.save_chat()
        self._fill_folder_menu()
        self.folder_menu.adjustSize()

    def show_folder_menu(self):
        self._fill_folder_menu()
        btn = self.folder_list_button
        self.folder_menu.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _fill_folder_menu(self):
        menu = self.folder_menu
        menu.clear()
        folders = self.client.toolbox.folders
        if not folders:
            menu.addAction("No folders").setEnabled(False)
            return
        for folder in folders:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(8, 2, 4, 2)
            label = QLabel(short_path(folder))
            label.setToolTip(str(folder))
            label.setStyleSheet("color:white")
            remove = QToolButton()
            remove.setText("x")
            remove.setToolTip(f"Unlink {folder}")
            remove.setAutoRaise(True)
            remove.setStyleSheet("color:#ff8a8a")
            # Deferred: refilling the menu deletes this button, which must not happen inside its own click.
            remove.clicked.connect(lambda _=False, f=folder: QTimer.singleShot(0, lambda: self.remove_tools_folder(f)))
            lay.addWidget(label, 1)
            lay.addWidget(remove)
            action = QWidgetAction(menu)
            action.setDefaultWidget(row)
            menu.addAction(action)

    def _show_tools_folder(self):
        box = self.client.toolbox
        n = len(box.folders)
        self.folder_list_button.setText(f"▾ {n}" if n else "▾")
        self.folder_button.setToolTip(f"Link a folder for the tools ({n} linked)\n"
                                      f"Working folder now: {box.working_dir}")

    def on_tool_event(self, ev):
        if ev["state"] == "running":
            self.status_label.setText("🔧 " + ev["summary"].splitlines()[0])
            return
        if self.response_start_pos is None:
            return
        if self.stream_phase == "thinking":
            if self.thinking_time is None:
                self.thinking_time = round(time.time() - self.thinking_started_at, 1)
            self._insert_stream("\n\n")
        self.stream_phase = "tools"
        self._insert_stream(f"🔧 {ev['summary'].splitlines()[0]}  {TOOL_STATE_ICONS.get(ev['state'], '')}\n", muted=True)
        self._show_tools_folder()

    def on_approval_needed(self, req):
        """A change or command wants to run; the worker thread waits for the answer."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if req["kind"] == "run" else QMessageBox.Question)
        box.setWindowTitle("Allow this command?" if req["kind"] == "run" else "Allow this change?")
        box.setText(f"{html.escape(self.botName)} wants to:<br/><br/><b>"
                    + html.escape(req["summary"]).replace("\n", "<br/>") + "</b>")
        box.setTextFormat(Qt.RichText)
        box.setDetailedText(req.get("details", ""))
        allow = box.addButton("Allow", QMessageBox.AcceptRole)
        always = box.addButton(f"Always allow {req['name']}", QMessageBox.AcceptRole)
        deny = box.addButton("Deny", QMessageBox.RejectRole)
        box.setDefaultButton(deny)
        box.setEscapeButton(deny)
        self.status_label.setText("Waiting for your OK…")
        box.exec()
        clicked = box.clickedButton()
        self.client.answer_approval("allow" if clicked is allow else "always" if clicked is always else "deny")

    def on_file_button(self):
        if not self.pending_attachments:
            self.add_attachments()
            return
        menu = QMenu(self)
        menu.addAction("Add files…", self.add_attachments)
        menu.addAction("Clear attachments", self.clear_attachments)
        menu.exec(self.ui.pushButton_3.mapToGlobal(self.ui.pushButton_3.rect().bottomLeft()))

    def add_attachments(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Attach images or files", "",
            "Images and documents (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.pdf *.docx *.txt *.md *.csv *.json "
            "*.py *.cpp *.h *.c *.js *.html *.xml *.yaml *.yml *.log);;All files (*)")
        for p in paths:
            path = Path(p)
            if path.stat().st_size > llmClient.MAX_ATTACHMENT_BYTES:
                self.on_status(f"{path.name} is over 25 MB and was not attached")
                continue
            if any(a["path"] == str(path) for a in self.pending_attachments):
                continue
            self.pending_attachments.append({"name": path.name, "path": str(path), "kind": llmClient.attachment_kind(path)})
        self.show_pending_attachments()

    def clear_attachments(self):
        self.pending_attachments = []
        self.show_pending_attachments()

    def show_pending_attachments(self):
        n = len(self.pending_attachments)
        self.ui.pushButton_3.setText(f"File ({n})" if n else "File")
        self.status_label.setText("📎 " + ", ".join(a["name"] for a in self.pending_attachments) if n else "")

    def on_status(self, text):
        if text:
            self.status_label.setText(text)
        else:
            self.show_pending_attachments()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_user(self, msg) -> str:
        text = html.escape(msg.get("content", "")).replace("\n", "<br/>")
        atts = ""
        for a in msg.get("attachments") or []:
            if a.get("kind") == "image" and Path(a["path"]).is_file():
                src = QUrl.fromLocalFile(a["path"]).toString()
                atts += f"<br/><img src='{src}' height='120'/>"
            else:
                atts += f"<br/><span style='color:{MUTED}'>📎 {html.escape(a['name'])}</span>"
        return f"<div style='color:#ffffff; margin-left:12px;'>{text}{atts}</div>"

    def render_assistant(self, msg, index) -> str:
        parts = []
        thinking = msg.get("thinking")
        if thinking:
            secs = msg.get("thinking_time")
            label = f"Thought for {secs}s" if secs is not None else "Thoughts"
            if index in self.expanded_thinking:
                body = html.escape(thinking).replace("\n", "<br/>")
                parts.append(f"<a href='think:{index}' style='color:{MUTED};text-decoration:none'>💭 {label} ▾</a>"
                             f"<div style='color:{MUTED};font-size:12px;margin-left:12px'><i>{body}</i></div>")
            else:
                parts.append(f"<a href='think:{index}' style='color:{MUTED};text-decoration:none'>💭 {label} ▸</a>")
        tools = msg.get("tools") or []
        if tools:
            open_ = index in self.expanded_tools
            lines = []
            for t in tools:
                line = f"🔧 {html.escape(t['summary'].splitlines()[0])}  {TOOL_STATE_ICONS.get(t['state'], '')}"
                if open_ and t.get("output"):
                    out = html.escape(t["output"]).replace("\n", "<br/>")
                    line += f"<div style='font-family:Consolas,monospace;font-size:11px;margin-left:16px'>{out}</div>"
                lines.append(line)
            parts.append(f"<a href='tools:{index}' style='color:{MUTED};text-decoration:none'>"
                         f"Used {len(tools)} tool{'s' if len(tools) != 1 else ''} {'▾' if open_ else '▸'}</a>"
                         f"<div style='color:{MUTED};font-size:12px;margin-left:12px'>{'<br/>'.join(lines)}</div>")
        parts.append(self.render_markdown(msg.get("content", "")))
        sources = msg.get("sources") or []
        if sources:
            links = "<br/>".join(
                f"<a href='{html.escape(s['url'])}' style='color:#7aa7ff'>[{s['n']}] {html.escape(s['title'] or s['url'])}</a>"
                for s in sources)
            parts.append(f"<div style='font-size:11px;color:{MUTED}'>Sources:<br/>{links}</div>")
        footer = []
        if "response_time" in msg:
            footer.append(f"⏱ {msg['response_time']}s")
        if msg.get("ts"):
            footer.append(self.format_ts(msg["ts"]))
        tokens = (msg.get("usage") or {}).get("completion_tokens")
        if tokens:
            footer.append(f"{tokens} tokens")
        if msg.get("stopped"):
            footer.append("stopped")
        if footer:
            parts.append(f"<div style='color:{MUTED};font-size:11px'>{' · '.join(footer)}</div>")
        return "".join(parts)

    def _append_message(self, msg, index):
        ts = msg.get("ts")
        time_str = f" <span style='color:{MUTED}'>[{self.format_ts(ts)}]</span>" if ts else ""
        if msg["role"] == "user":
            self.chat.append(f"<b>You</b>{time_str}:")
            self.chat.insertHtml(self.render_user(msg))
        else:
            self.chat.append(f"<b>{html.escape(self.botName)}</b>{time_str}:")
            self.chat.insertHtml(self.render_assistant(msg, index))
        self.chat.append("")

    def _rebuild_chat_ui(self, keep_scroll=False):
        bar = self.chat.verticalScrollBar()
        pos = bar.value()
        self.chat.clear()
        for i, msg in enumerate(self.chat_markdown):
            self._append_message(msg, i)
        if keep_scroll:
            bar.setValue(pos)
        else:
            self.chat.moveCursor(QTextCursor.End)

    def on_anchor(self, url: QUrl):
        target = url.toString()
        if target.startswith("think:"):
            if self.client.busy:
                return
            idx = int(target.split(":", 1)[1])
            self.expanded_thinking ^= {idx}
            self._rebuild_chat_ui(keep_scroll=True)
        elif target.startswith("tools:"):
            if self.client.busy:
                return
            self.expanded_tools ^= {int(target.split(":", 1)[1])}
            self._rebuild_chat_ui(keep_scroll=True)
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _start_response(self):
        self.chat.append(f"<b>{html.escape(self.botName)}:</b>")
        self.chat.moveCursor(QTextCursor.End)
        self.response_start_pos = self.chat.textCursor().position()
        self.response_start_time = time.time()
        self.thinking_started_at = None
        self.thinking_time = None
        self.stream_phase = None
        self.ui.pushButton_5.setText("Stop")
        self.client.generate(self.chat_markdown)

    def _insert_stream(self, text, muted=False):
        cursor = QTextCursor(self.chat.document())
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(MUTED if muted else "#ffffff"))
        fmt.setFontItalic(muted)
        cursor.insertText(text, fmt)
        self.chat.moveCursor(QTextCursor.End)
        self.chat.ensureCursorVisible()

    def on_thinking(self, text):
        if self.response_start_pos is None:
            return
        if self.stream_phase is None:
            self.thinking_started_at = time.time()
            self._insert_stream("💭 Thinking…\n", muted=True)
            self.stream_phase = "thinking"
        elif self.stream_phase != "thinking":       # thinking again after tool results
            self._insert_stream("\n" if self.stream_phase == "answer" else "", muted=True)
            self.stream_phase = "thinking"
        self._insert_stream(text, muted=True)

    def on_token(self, text):
        if self.response_start_pos is None:
            return
        if self.stream_phase != "answer":
            if self.stream_phase == "thinking":
                if self.thinking_time is None:
                    self.thinking_time = round(time.time() - self.thinking_started_at, 1)
                self._insert_stream("\n\n")
            self.stream_phase = "answer"
        self._insert_stream(text)

    def _clear_streamed(self):
        doc = self.chat.document()
        cursor = QTextCursor(doc)
        cursor.setPosition(self.response_start_pos)
        cursor.setPosition(doc.characterCount() - 1, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        return cursor

    def _end_response(self):
        self.response_start_pos = None
        self.stream_phase = None
        self.ui.pushButton_5.setText("Send")

    def on_done(self, result: dict):
        if self.response_start_pos is None:
            return
        if result.get("aborted") and not result.get("content"):
            self._clear_streamed()
            self._end_response()
            self._rebuild_chat_ui()
            self.on_status("Stopped")
            return

        end_time = time.time()
        msg = {
            "role": "assistant",
            "content": result.get("content", ""),
            "ts": end_time,
            "response_time": round(end_time - self.response_start_time, 2),
        }
        if result.get("thinking"):
            msg["thinking"] = result["thinking"]
            msg["thinking_time"] = self.thinking_time or round(end_time - (self.thinking_started_at or end_time), 1)
        if result.get("sources"):
            msg["sources"] = result["sources"]
        if result.get("tools"):
            msg["tools"] = result["tools"]
        if result.get("usage"):
            msg["usage"] = result["usage"]
        if result.get("aborted"):
            msg["stopped"] = True
        self.chat_markdown.append(msg)

        cursor = self._clear_streamed()
        cursor.insertHtml(self.render_assistant(msg, len(self.chat_markdown) - 1))
        cursor.insertBlock()
        self.chat.moveCursor(QTextCursor.End)
        self._end_response()
        self.save_chat()

    def on_error(self, err: str):
        print(f"LLM error: {err}")
        if self.response_start_pos is None:
            self.on_status(err)
            return
        cursor = self._clear_streamed()
        cursor.insertHtml(f"<div style='color:#ff6b6b'>⚠ {html.escape(err)}</div>"
                          f"<div style='color:{MUTED};font-size:11px'>Press Reload Text to try again.</div>")
        cursor.insertBlock()
        self._end_response()

    def keyPressEvent(self, event):
        if self.input.hasFocus():
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.input.insertPlainText("\n")
                else:
                    self.sendMessage()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Side panels
    # ------------------------------------------------------------------

    def toggle_graphics_view(self):
        """Toggle graphics view visibility by adjusting width constraints"""
        if self.isShowImgWindow:
            self.hideImgWindow()
            self.isShowImgWindow = False
        elif not self.isShowImgWindowLock:
            self.showImgWindow()
            self.isShowImgWindow = True

    def hideImgWindow(self):
        layoutH3 = self.ui.horizontalLayout_3
        # HIDE: Set max width to 0, disable the widget, and collapse layout space
        self.ui.graphicsView.setMaximumWidth(0)
        self.ui.graphicsView.setMinimumWidth(0)

        layoutH3.itemAt(3).changeSize(0, 20, QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.ui.pushButton_7.setMaximumWidth(0)
        self.ui.pushButton_7.setEnabled(False)
        self.ui.graphicsView.setEnabled(False)
        self.ui.graphicsView.setVisible(False)

        self.ui.pushButton.setText("<<")

        if hasattr(self.ui, 'gridLayout'):
            self.ui.gridLayout.setColumnStretch(1, 0)  # Column 1 is graphicsView

    def showImgWindow(self):
        layoutH3 = self.ui.horizontalLayout_3
        # SHOW: Restore original width constraints and enable
        self.ui.graphicsView.setMaximumWidth(16777215)
        self.ui.graphicsView.setMinimumWidth(0)

        layoutH3.itemAt(3).changeSize(40, 20, QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.ui.graphicsView.setEnabled(True)
        self.ui.graphicsView.setVisible(True)

        self.ui.pushButton.setText(">>")

        self.ui.pushButton_7.setMaximumWidth(16777215)
        self.ui.pushButton_7.setEnabled(True)

        self.is_graphics_view_visible = True
        self.ui.graphicsView.parentWidget().updateGeometry()

    def toggle_chat_list(self):
        if self.isShowChatList:
            self.hideChatList()
            self.ui.pushButton_2.setText(">>")
        else:
            self.showChatList()
            self.ui.pushButton_2.setText("<<")

    def hideChatList(self):
        self.ui.listWidget.setVisible(False)
        self.isShowChatList = False
        if not self.isShowImgWindowLock and self.isShowImgWindow:
            self.showImgWindow()
            self.ui.pushButton.setEnabled(True)

    def showChatList(self):
        self.ui.listWidget.setVisible(True)
        self.hideImgWindow()
        self.isShowChatList = True
        self.ui.pushButton.setEnabled(False)

    # ------------------------------------------------------------------
    # Chat files
    # ------------------------------------------------------------------

    def get_first_json_file_os(self, directory: str, recursive: bool = False):
        if not os.path.isdir(directory):
            print(f"Not a directory: {directory}")
            return None
        if recursive:
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file.lower().endswith('.json'):
                        return os.path.abspath(os.path.join(root, file))
        else:
            for file in os.listdir(directory):
                if file.lower().endswith('.json'):
                    return os.path.abspath(os.path.join(directory, file))
        print(f"No .json files found in: {directory}")
        return None

    def get_lastChat(self):
        log = read_json_file(paths.CHAT_INDEX_FILE)
        return log["LastChat"]

    def update_lastChat(self, path):
        logPath = paths.CHAT_INDEX_FILE
        log = read_json_file(logPath)
        log_new = log.copy()
        log_new["LastChat"] = path

        with open(logPath, "w", encoding="utf-8") as f:
            json.dump(log_new, f, indent=4)

    def load_chat(self, path):
        if self.client.busy:
            self.client.abort_generation()
            self._end_response()

        if path == "":
            path = self.get_first_json_file_os(str(paths.CHAT_DIR))
            if path is None:
                return

        chatHist = read_json_file(path)
        if chatHist is None:
            return
        self.chat_path = path

        self.update_lastChat(path)
        self.ui.lineEdit.setText(chatHist["Name"])

        self.chat_markdown = chatHist.get("Chat", [])
        for msg in self.chat_markdown:
            # Older versions stored user text with HTML line breaks.
            if msg.get("role") == "user":
                msg["content"] = msg.get("content", "").replace("<br/>", "\n")

        logsPath = paths.CHAT_INDEX_FILE
        logs = read_json_file(logsPath)
        chat_path_str = Path(self.chat_path).stem

        if chat_path_str in logs["ChatList"] and logs["ChatList"].index(chat_path_str) != len(logs["ChatList"])-1:
            logs["ChatList"].remove(chat_path_str)
        if chat_path_str not in logs["ChatList"]:
            logs["ChatList"].append(chat_path_str)

        with open(logsPath, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=4)

        self.load_bot(chatHist["Bot Path"])
        self.client.set_model(chatHist["Model"])
        self._fill_model_combo()
        self.client.temperature = chatHist["Temperature"]
        self.client.summary = chatHist.get("Summary")

        mode = chatHist.get("Web Search", "auto")
        self.web_combo.blockSignals(True)
        self.web_combo.setCurrentIndex(max(0, self.web_combo.findData(mode)))
        self.web_combo.blockSignals(False)
        self.think_check.blockSignals(True)
        self.think_check.setChecked(chatHist.get("Think", True))
        self.think_check.blockSignals(False)
        self.tools_check.blockSignals(True)
        self.tools_check.setChecked(chatHist.get("Tools", True))
        self.tools_check.blockSignals(False)
        folders = chatHist.get("Tools Folders")
        if folders is None:          # 0.5 saved one working folder, which defaulted to home
            old = chatHist.get("Tools Folder")
            folders = [old] if old and Path(old) != Path.home() else []
        self.client.toolbox.set_folders(folders)
        self._show_tools_folder()
        self.on_options_changed()

        self.ui.listWidget.clear()
        self.ui.listWidget.addItem("Create New Chat [+]")
        for chatFile in reversed(logs["ChatList"]):
            self.ui.listWidget.addItem(chatFile)

        self.expanded_thinking = set()
        self.expanded_tools = set()
        self.clear_attachments()
        self._rebuild_chat_ui()

    def save_chat(self):
        if not self.chat_path:
            return

        data = {
            "Name": self.ui.lineEdit.text(),
            "Bot Path": self.bot_path,
            "Temperature": self.client.temperature,
            "Model": self.client.model_name,
            "Web Search": self.client.web_mode,
            "Think": self.client.think,
            "Tools": self.tools_check.isChecked(),
            "Tools Folders": [str(f) for f in self.client.toolbox.folders],
            "Chat": self.chat_markdown,
            "Summary": self.client.summary,
            "Payload": self.client.export_payload(self.chat_markdown),
        }

        try:
            with open(self.chat_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Save failed: {e}")

    def load_bot(self, path):
        botJsonPath = path + "/Bot Description.json"
        botJson = read_json_file(botJsonPath)
        self.bot_path = path
        self.bot_desc_path = botJsonPath
        if botJson is None:
            self.client.set_preset("")
            self.botName = "Bot"
            return

        self.client.set_preset(botJson["Description"])
        self.botName = botJson["Name"]

        if botJson['WorkflowPath'] == "":
            self.isShowImgWindowLock = True
            self.hideImgWindow()
            self.ui.pushButton.setEnabled(False)
        else:
            self.isShowImgWindowLock = False
            self.showImgWindow()
            self.ui.pushButton.setEnabled(True)

    # ------------------------------------------------------------------
    # Sending, editing, regenerating
    # ------------------------------------------------------------------

    def sendMessage(self):
        if self.client.busy:
            self.client.abort_generation()       # the Send button is "Stop" while answering
            return

        text = self.input.toPlainText().strip()
        if not text and not self.pending_attachments:
            return
        self.input.clear()

        msg = {"role": "user", "content": text, "ts": self.now_ts()}
        if self.pending_attachments:
            msg["attachments"] = self.pending_attachments
        self.chat_markdown.append(msg)
        self.clear_attachments()

        self._append_message(msg, len(self.chat_markdown) - 1)
        self._start_response()

    def render_markdown(self, text: str) -> str:
        html_text = markdown.markdown(
            text,
            extensions=["fenced_code", "tables"]
        )

        return f"""
           <div style="
               font-family: Segoe UI, sans-serif;
               font-size: 14px;
               line-height: 1.5;
           ">
               <style>
                   pre {{
                       background: #202020;
                       color: #dcdcdc;
                       padding: 10px;
                       border-radius: 6px;
                       overflow-x: auto;
                   }}
                   code {{
                       background: #202020;
                       padding: 2px 4px;
                       border-radius: 4px;
                       font-family: Consolas, monospace;
                   }}
                   table {{
                       border-collapse: collapse;
                   }}
                   th, td {{
                       border: 1px solid #555;
                       padding: 4px 8px;
                   }}
                   h1, h2, h3 {{
                       color: #ffffff;
                   }}
               </style>
               {html_text}
           </div>
           """

    def _last_user_index(self):
        for i in range(len(self.chat_markdown) - 1, -1, -1):
            if self.chat_markdown[i]["role"] == "user":
                return i
        return None

    def get_last_user_message(self):
        i = self._last_user_index()
        return None if i is None else self.chat_markdown[i]["content"]

    def edit_last_user_message(self, new_text: str):
        new_text = new_text.strip()
        i = self._last_user_index()
        if not new_text or i is None or self.client.busy:
            return
        # Drop everything after the edited message, then answer it again.
        del self.chat_markdown[i + 1:]
        self.chat_markdown[i]["content"] = new_text
        self.chat_markdown[i]["ts"] = self.now_ts()
        self._rebuild_chat_ui()
        self._start_response()

    def delete_last_user_exchange(self):
        i = self._last_user_index()
        if i is None or self.client.busy:
            return
        del self.chat_markdown[i:]
        self._rebuild_chat_ui()
        self.save_chat()

    def regenerate_last_response(self):
        if self.client.busy:
            return
        i = self._last_user_index()
        if i is None:
            return
        del self.chat_markdown[i + 1:]
        self.expanded_thinking = {x for x in self.expanded_thinking if x <= i}
        self.expanded_tools = {x for x in self.expanded_tools if x <= i}
        self._rebuild_chat_ui()
        self._start_response()

    def now_ts(self):
        return time.time()

    def format_ts(self, ts: float):
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")

    def selectChat(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select chat JSON",
            str(paths.CHAT_DIR),
            "JSON Files (*.json)"
        )

        if file_path:
            self.load_chat(file_path)

    def qs_chat(self, chatName):
        chatPath = paths.CHAT_DIR / (chatName + ".json")
        self.load_chat(str(chatPath))

class EditMessage(QMainWindow):

    def __init__(self):
        super().__init__()
        self.load_ui()
        self.setup_connections()

    def load_ui(self):
        try:
            ui_file = QFile(paths.ui_file("EditMessage.ui"))
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Edit Message")


        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        #self.ui.pushButton.clicked.connect(self.load_bot_dir)
        pass

class ServerIP(QMainWindow):

    def __init__(self):
        super().__init__()
        self.load_ui()
        self.setup_connections()

    def load_ui(self):
        try:
            ui_file = QFile(paths.ui_file("server ip.ui"))
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Server IP Address")


        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        self.ui.pushButton_2.clicked.connect(self.reset_ip)

    def reset_ip(self):
        """Put back the default LLM server address from Code/UI/network.json."""
        server = paths.default_network().get("llm_server", {})
        self.ui.lineEdit.setText(f"{server.get('ip', '')}:{server.get('port', llmClient.DEFAULT_PORT)}")

def read_json_file(file_path):
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