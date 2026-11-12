import avatar
import chat_links
import chat_sections
import chat_settings
import llmClient
import paths
import personalities
import voice
from local_tools import github_login, repo_url
from local_tools.linked import LinkedChat, LinkScope, shared_by
from local_tools.project import lead_note
from Widgets import projectWork
from Widgets.chatList import ChatList
from Widgets.serverFolders import ServerFolderPicker

from PySide6.QtWidgets import (QInputDialog, QListWidget, QListWidgetItem, QMainWindow, QSizePolicy, QFileDialog,
                               QComboBox, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QToolButton, QWidget,
                               QHBoxLayout, QVBoxLayout, QWidgetAction, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsOpacityEffect)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, QObject, QEvent, QUrl, QTimer, Signal, QSettings
from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor, QDesktopServices, QKeySequence, QShortcut, QPixmap
import html
import json
import threading
from pathlib import Path, PurePath
import os
import markdown
import time
from datetime import datetime

MUTED = "#8a8a8a"
IMAGE_HEIGHT = 240              # pictures in the chat; click one to open it full size
CAP_ICONS = (("vision", "👁"), ("thinking", "💭"), ("tools", "🔧"))
WEB_LABELS = {"auto": "Web: Auto", "on": "Web: Always", "off": "Web: Off"}
MAX_FOLLOW_UPS = 3                 # a project chat answers its sessions' reports this often per message of yours
TOOL_STATE_ICONS = {"ok": "✓", "error": "⚠", "declined": "✗ declined"}
APP_VERSION = "0.17.0_Ubuntools"
MENU_LIST_HEIGHT = 320          # a list in a menu scrolls beyond this
MENU_STYLE = "QMenu{background:rgb(45,45,45);color:white}"
REPO_LIST_STYLE = ("QListWidget{background:rgb(45,45,45);color:white;border:none}"
                   "QListWidget::item{padding:3px 8px}"
                   "QListWidget::item:hover{background:rgb(70,70,70)}"
                   "QListWidget::item:disabled{color:#8a8a8a}")
# Mic button languages: label, code sent to the server ("" = Whisper detects it)
VOICE_LANGUAGES = (("Detect automatically", ""), ("English", "en"), ("中文 Chinese", "zh"),
                   ("粵語 Cantonese", "yue"), ("日本語 Japanese", "ja"))
SPEAK_LABELS = {"stopped": "▶ Play", "loading": "⏳ Loading…", "playing": "⏸ Pause", "paused": "▶ Resume"}
MIC_STYLE = "border-color:rgb(255,255,255);background:rgb(57,57,57);color:white"
MIC_RECORDING_STYLE = "border-color:rgb(255,255,255);background:rgb(170,40,40);color:white"


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



def dim(widget, off: bool):
    """Turn a bar button off and fade it, emoji included (a stylesheet colour can't grey an emoji)."""
    widget.setEnabled(not off)
    if off:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.35)
        widget.setGraphicsEffect(effect)
    else:
        widget.setGraphicsEffect(None)

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


class ImagePanel(QObject):
    """The picture panel on the right (the .ui's graphicsView): shows one picture,
    scaled to fit, and keeps fitting it when the window is resized."""

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.scene = QGraphicsScene(view)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.item)
        view.setScene(self.scene)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.viewport().installEventFilter(self)
        self.path = None
        self.where = None            # (message index, picture index) of what is shown

    def show(self, path, where=None) -> bool:
        pix = QPixmap(str(path)) if path and Path(path).is_file() else QPixmap()
        if pix.isNull():
            self.clear()
            return False
        self.item.setPixmap(pix)
        self.scene.setSceneRect(self.item.boundingRect())
        self.path, self.where = str(path), where
        self.fit()
        return True

    def clear(self):
        self.item.setPixmap(QPixmap())
        self.path, self.where = None, None

    def fit(self):
        if self.path:
            self.view.fitInView(self.item, Qt.KeepAspectRatio)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            self.fit()
        elif event.type() == QEvent.MouseButtonDblClick and self.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))
            return True
        return False


class Redrawer(QObject):
    """Runs LLMClient.draw on a thread for Reload Image and reports back on the GUI thread."""
    finished = Signal(object, object, object)     # where, entry or None, error text or None

    def start(self, client, where, prompt, negative, workflow):
        def run():
            try:
                self.finished.emit(where, client.draw(prompt, negative, workflow), None)
            except Exception as e:
                self.finished.emit(where, None, str(e))
        threading.Thread(target=run, daemon=True).start()


def safe_file_name(name: str) -> str:
    """A chat title as a Windows-safe file name."""
    cleaned = "".join(c for c in name if c not in '<>:"/\\|?*' and ord(c) >= 32).strip().rstrip(".")
    return cleaned or "New Chat"


class ChatMain(QMainWindow):
    """Main chat window.

    `chat_markdown` is the conversation and the only source of truth: the LLM
    request is rebuilt from it every time (see llmClient.build_messages), so
    editing, deleting and regenerating can't leave a stale or duplicated
    payload behind. Each message is a dict:
      user:      role, content (plain text), ts, attachments [{name, path, kind}]
      assistant: role, content (markdown), ts, response_time,
                 thinking, thinking_time, sources [{n, title, url}], usage,
                 tools [{name, summary, kind, state, output}],
                 images [{path, prompt, negative_prompt, workflow} or {error, prompt, ...}]

    `settings` are this chat's character, model and options (chat_settings.py),
    saved in the chat file and edited in the Chat Settings window.
    """
    model_changed = Signal(str)      # the quick model picker changed this chat's model
    links_changed = Signal()           # links, personalities or the open chat changed
    _repos_fetched = Signal(object)  # your GitHub repositories, from the fetching thread

    def __init__(self):
        super().__init__()
        self.isShowImgWindow = True
        self.isShowImgWindowLock = False
        self.isShowChatList = False
        self.settings = chat_settings.defaults()      # what this chat runs with: own_settings under its personality
        self.own_settings = self.settings              # what's saved in the chat file
        self.personality = None                        # the settings of the personality it talks as, if any
        self.personality_name = None
        self.skipped = []                              # the groups of its settings this chat doesn't take
        self.links = chat_links.load(paths.LINKS_FILE, self.all_chats())
        self.botName = self.settings["Character"]
        self.status_tools_tip = ""
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
        self._streamed_images = []        # pictures of the reply being streamed

        self.client = llmClient.LLMClient()
        self.client.token.connect(self.on_token)
        self.client.thinking.connect(self.on_thinking)
        self.client.status.connect(self.on_status)
        self.client.done.connect(self.on_done)
        self.client.error.connect(self.on_error)
        self.client.tool_event.connect(self.on_tool_event)
        self.client.approval_needed.connect(self.on_approval_needed)
        self.client.image.connect(self.on_image)
        self.client.chat_summarized.connect(self.on_chat_summarized)
        # A project's own chat hands its sessions work, which they do in the background (projectWork.py)
        self.desk = None                 # the open chat's projectWork.ProjectDesk, if it's a project's chat
        self.runner = projectWork.SessionRunner(self)
        self.runner.changed.connect(self._show_runner_state)
        self._pending_messages = []      # for the open chat, waiting for its answer to finish
        self._follow_ups = {}            # project chat -> times it answered its reports since your last message
        self.handover = projectWork.Handover(self)        # writes context.md when a project chat starts fresh
        self.handover.done.connect(self._on_handover)
        self._handing_over = None        # the project whose context.md is being written
        self.redrawer = Redrawer(self)
        self.redrawer.finished.connect(self.on_redrawn)
        self.redrawing = False

        # Voice input (voice.py): the mic button records, the server's Whisper writes it down
        self.recorder = voice.Recorder(self)
        self.recorder.level.connect(self.on_voice_level)
        self.recorder.finished.connect(self.on_voice_recorded)
        self.recorder.failed.connect(self.on_voice_failed)
        self.transcriber = voice.Transcriber(lambda: self.client.base_url, self)
        self.transcriber.done.connect(self.on_voice_text)
        self.transcriber.failed.connect(self.on_voice_failed)
        # Text to speech: ▶ Play on each reply when the chat has TTS on; the server reads it out
        self.speaker = voice.Speaker(lambda: self.client.base_url, paths.AUDIO_DIR, self)
        self.speaker.changed.connect(self.on_speaker_changed)
        self.speaker.failed.connect(lambda err: self.status_label.setText("🔊 " + err))
        self.speaker.spoken.connect(self.on_spoken)
        self.expanded_spoken = set()     # indices of replies showing what they were spoken as
        self._speak_redraw_pending = False
        # The 3D model (avatar.py): in chats with "3D model" on she acts out and speaks each reply
        self.avatar_link = avatar.AvatarLink(self)
        self.avatar_link.failed.connect(lambda err: self.status_label.setText("🧍 " + err))
        self.avatar_link.status.connect(self.on_avatar_status)
        self.performer = avatar.Performer(self.speaker, self.avatar_link, self)
        self._tag_filter = None          # hides her stage directions while a reply streams in
        self._reacted = False            # the streaming reply's first act was played already

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
            loader.registerCustomWidget(ChatList)
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("ChatUI")
                self.chat_list = self.ui.chatList
                self.chat_list.sections_changed.connect(self.save_sections)
                self.chat_list.project_of = lambda chat: chat_links.led_by(self.links, chat)
                self.chat_list.fresh_requested.connect(self.ask_start_fresh)
                self.links_changed.connect(self.chat_list.redraw)
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
        """Status line in the bottom bar; quick model picker and the tools' folder buttons
        above the input, on the left.

        Web search, Think and the tools are set per chat in the Chat Settings window."""
        bottom = self.ui.horizontalLayout_4
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color:{MUTED}")
        bottom.insertWidget(0, self.status_label, 1)

        # The row above the input (Reload Text / Reload Image). Keep its image-panel spacer by
        # reference, since the controls below are inserted in front of it.
        bar = self.ui.horizontalLayout_3
        self.img_spacer = bar.itemAt(3)
        # Pictures ComfyUI drew for this chat: the newest is shown in the panel on the right.
        self.image_panel = ImagePanel(self.ui.graphicsView, self)
        self.ui.graphicsView.setToolTip("The newest picture of this chat. Double-click to open it full size.")
        self.ui.pushButton_7.setToolTip("Draw this picture again with the same prompt (a new seed)")

        # Models come straight from Ollama through the server, refreshed whenever the list is opened.
        # The same choice is the Model in Chat Settings.
        self.models = {}
        self.model_combo = LiveComboBox(self.refresh_models)
        self.model_combo.setToolTip("Models on the server's Ollama. ● = loaded in memory, 👁 vision, "
                                    "💭 thinking, 🔧 tools")
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.model_combo.setStyleSheet("background:rgb(57,57,57);color:white")
        self.model_combo.activated.connect(self.on_model_chosen)
        bar.insertWidget(0, self.model_combo)

        # Tools on this PC: file browsing, git and run (local_tools/), each turned on per chat. Changes and commands ask first.
        # 📁 links another folder; ▾ drops down the linked folders, each with an x to remove it.
        self.folder_button = QPushButton("📁")
        self.folder_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.folder_button.setFixedWidth(max(self.folder_button.sizeHint().width() // 2, 24))
        self.folder_button.clicked.connect(self.add_tools_folder)
        self.folder_list_button = QToolButton()
        self.folder_list_button.setText("▾")
        self.folder_list_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.folder_list_button.setToolTip("Linked folders")
        self.folder_menu = QMenu(self)
        self.folder_menu.setStyleSheet(MENU_STYLE)
        self.folder_list_button.clicked.connect(self.show_folder_menu)
        bar.insertWidget(1, self.folder_button)
        bar.insertWidget(2, self.folder_list_button)
        # 🐙 drops down your own GitHub repositories to link, with Other… for any other link;
        # its ▾ lists the linked ones, like the folders.
        self.repo_button = QPushButton("🐙")
        self.repo_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.repo_button.setFixedWidth(max(self.repo_button.sizeHint().width() // 2, 24))
        self.repo_button.clicked.connect(self.show_repo_picker)
        self._own_repos = []                # (owner/name) last fetched from GitHub
        self._repo_note = ""                # shown when there are none to list
        self._fetching_repos = False
        self._repos_fetched.connect(self._on_repos_fetched)
        self.repo_picker = QMenu(self)
        self.repo_picker.setStyleSheet(MENU_STYLE)
        self.repo_picker_list = QListWidget()
        self.repo_picker_list.setStyleSheet(REPO_LIST_STYLE)
        self.repo_picker_list.setMouseTracking(True)
        self.repo_picker_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.repo_picker_list.itemClicked.connect(self.pick_repo)
        self.repo_picker_action = QWidgetAction(self.repo_picker)
        self.repo_picker_action.setDefaultWidget(self.repo_picker_list)
        self.repo_picker.addAction(self.repo_picker_action)
        self.repo_picker.addSeparator()
        self.repo_picker.addAction("Other…", self.add_repo)
        self.repo_list_button = QToolButton()
        self.repo_list_button.setText("▾")
        self.repo_list_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.repo_list_button.setToolTip("Linked GitHub repositories")
        self.repo_menu = QMenu(self)
        self.repo_menu.setStyleSheet(MENU_STYLE)
        self.repo_list_button.clicked.connect(self.show_repo_menu)
        bar.insertWidget(3, self.repo_button)
        bar.insertWidget(4, self.repo_list_button)
        # 🖥 links a folder on the Ubuntu server for the ubuntu_* tools, picked from what the server allows
        # (nothing is mounted here); its ▾ lists them. Shown while the chat has Ubuntu Files or Ubuntu Run on.
        self.server_folder_button = QPushButton("🖥")
        self.server_folder_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.server_folder_button.setFixedWidth(max(self.server_folder_button.sizeHint().width() // 2, 24))
        self.server_folder_button.clicked.connect(self.add_server_folder)
        self.server_folder_list_button = QToolButton()
        self.server_folder_list_button.setText("▾")
        self.server_folder_list_button.setStyleSheet("background:rgb(57,57,57);color:white")
        self.server_folder_list_button.setToolTip("Linked server folders")
        self.server_folder_menu = QMenu(self)
        self.server_folder_menu.setStyleSheet(MENU_STYLE)
        self.server_folder_list_button.clicked.connect(self.show_server_folder_menu)
        bar.insertWidget(5, self.server_folder_button)
        bar.insertWidget(6, self.server_folder_list_button)
        self._show_tools_folder()

        # Mic button between the input and Send. Click to talk, click again to stop;
        # right-click for the language and auto-send.
        self.mic_button = QPushButton("🎤")
        self.mic_button.setMinimumHeight(40)
        self.mic_button.setFixedWidth(52)
        self.mic_button.setStyleSheet(MIC_STYLE)
        self.mic_button.setToolTip("Speak instead of typing (Ctrl+M). Click again to stop, Esc to cancel.\n"
                                   "Right-click for the language and auto-send.")
        self.mic_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mic_button.customContextMenuRequested.connect(self.show_voice_menu)
        row = self.ui.horizontalLayout
        row.insertWidget(row.indexOf(self.ui.pushButton_5), self.mic_button)
        self.ui.label.setText(APP_VERSION)

    def setup_connections(self):
        """Connect button signals to functions"""
        self.ui.pushButton.clicked.connect(self.toggle_graphics_view)
        self.ui.pushButton_2.clicked.connect(self.toggle_chat_list)
        self.ui.pushButton_5.clicked.connect(self.sendMessage)
        self.ui.pushButton_3.clicked.connect(self.on_file_button)
        self.mic_button.clicked.connect(self.toggle_voice)
        QShortcut(QKeySequence("Ctrl+M"), self, self.toggle_voice)
        self._voice_cancel = QShortcut(QKeySequence(Qt.Key_Escape), self, self.cancel_voice)
        self._voice_cancel.setEnabled(False)

        self.ui.actionNew_Chat.setShortcut("Ctrl+N")
        self.ui.actionLoad_Chat.setShortcut("Ctrl+O")
        self.ui.actionChat_Settings.setShortcut("Ctrl+E")
        self.ui.actionLink_Graph.setShortcut("Ctrl+G")
        self.fresh_action = self.ui.menuChat.addAction(
            "Start Project Chat Fresh…", lambda: self.ask_start_fresh(chat_links.led_by(self.links, self.chat_name())))
        self.fresh_action.setToolTip("A new chat for this project, with a context.md handover instead of the history")
        self.fresh_action.setEnabled(False)

        self.ui.pushButton_6.clicked.connect(self.regenerate_last_response) #regen
        self.ui.pushButton_7.clicked.connect(self.redraw_image)

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
        if self.models and not self.settings["Model"]:
            self.settings["Model"] = next(iter(self.models))     # a chat with no model yet takes the first
            self.client.set_model(self.settings["Model"])
        self._fill_model_combo()
        return list(self.models)

    def model_label(self, m):
        icons = "".join(icon for cap, icon in CAP_ICONS if cap in m.get("capabilities", []))
        size = m.get("parameter_size") or ""
        return f"{'●' if m.get('loaded') else '○'} {m['id']}  {size} {icons}".rstrip()

    def _fill_model_combo(self):
        combo, current = self.model_combo, self.client.model_name
        combo.blockSignals(True)
        combo.clear()
        for mid, m in self.models.items():
            combo.addItem(self.model_label(m), mid)
            ctx = m.get("context_length")
            combo.setItemData(combo.count() - 1, ", ".join(
                x for x in (m.get("parameter_size"), m.get("quantization"),
                            f"context {ctx:,}" if ctx else None,
                            "loaded" if m.get("loaded") else "not loaded") if x), Qt.ToolTipRole)
        if current and current not in self.models:
            combo.addItem(f"   {current} (not on the server)", current)
        if self.is_lead():
            for i, mid in enumerate(self.models):
                if not projectWork.fits_lead(self.models[mid]):
                    combo.model().item(i).setEnabled(False)
                    combo.setItemData(i, "A project's chat needs a model that thinks (💭) and calls tools (🔧)",
                                      Qt.ToolTipRole)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        combo.blockSignals(False)
        self._apply_options()

    def on_model_chosen(self, index):
        model = self.model_combo.itemData(index)
        if model and model != self.client.model_name:
            self.settings["Model"] = model
            self.client.set_model(model)
            self.client.summary = None      # the old summary was written by another model; harmless to redo
            self.save_chat()
            self.model_changed.emit(model)
        self._apply_options()

    def can(self, capability: str) -> bool:
        """Whether this chat's model has a capability (thinking, tools, vision). A server
        that doesn't report capabilities gets the benefit of the doubt."""
        caps = self.models.get(self.client.model_name, {}).get("capabilities")
        return not caps or capability in caps

    def _apply_options(self):
        """Send this chat's options to the client, leaving out what the model can't use:
        tools and memory need a model that calls tools, Think one that thinks, and
        pictures one that sees."""
        s = self.settings
        self.client.toolbox.link_scope = self._link_scope()
        project = chat_links.led_by(self.links, self.chat_name())
        if not project:
            self.desk = None
        elif not self.desk or (self.desk.project, self.desk.personality) != (project, self.personality_name):
            self.desk = projectWork.ProjectDesk(self, project, self.personality_name)
        self.client.toolbox.project = self.desk
        if hasattr(self, "fresh_action"):
            self.fresh_action.setEnabled(bool(project))
        self.client.role_note = lead_note(project, self.personality_name, s["Character"], self._model_lines(),
                                          self.client.model_name or "") if project else ""
        caps = self.models.get(self.client.model_name, {}).get("capabilities")
        can_call = self.client.apply_options(s, caps, self.memory_folder())
        groups = chat_settings.tool_groups(s)
        if not groups:
            tip = "Tools are off for this chat (Character > Settings)"
        elif not can_call:
            tip = f"{self.client.model_name} can't call tools; pick a model marked 🔧"
        else:
            tip = ""
        self.status_tools_tip = tip
        self._show_tools_folder()
        self._show_file_button()

    def _show_file_button(self):
        sees = self.can("vision")
        self.ui.pushButton_3.setToolTip("Attach images or files" if sees else
                                        f"Attach files ({self.client.model_name} can't see images, "
                                        "so pictures aren't sent)")

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

    def add_server_folder(self):
        box = self.client.toolbox
        url, token = box.server() if box.server else (self.client.base_url, "")
        picker = ServerFolderPicker(url, token, box.server_folders, self)
        if picker.exec() and picker.chosen:
            box.add_server_folder(picker.chosen)
            self._show_tools_folder()
            self.save_chat()

    def remove_server_folder(self, folder):
        self.client.toolbox.remove_server_folder(folder)
        self._show_tools_folder()
        self.save_chat()
        self._fill_server_folder_menu()
        self.server_folder_menu.adjustSize()

    def show_server_folder_menu(self):
        self._fill_server_folder_menu()
        btn = self.server_folder_list_button
        self.server_folder_menu.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _fill_server_folder_menu(self):
        menu = self.server_folder_menu
        menu.clear()
        folders = self.client.toolbox.server_folders
        if not folders:
            menu.addAction("None linked: the tools may use every folder the server allows").setEnabled(False)
            return
        rows = []
        for folder in folders:
            label = QLabel(folder)
            label.setToolTip(f"{folder} on the Ubuntu server")
            label.setStyleSheet("color:white")
            rows.append(self._menu_row(label, f"Unlink {folder}", lambda f=folder: self.remove_server_folder(f)))
        self._add_menu_rows(menu, rows)

    def show_folder_menu(self):
        self._fill_folder_menu()
        btn = self.folder_list_button
        self.folder_menu.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _fill_folder_menu(self):
        menu = self.folder_menu
        menu.clear()
        box = self.client.toolbox
        folders, shared = box.folders, box.shared_folders()
        if not folders and not shared:
            menu.addAction("No folders").setEnabled(False)
            return
        rows = []
        for folder in folders:
            label = QLabel(short_path(folder))
            label.setToolTip(str(folder))
            label.setStyleSheet("color:white")
            rows.append(self._menu_row(label, f"Unlink {folder}", lambda f=folder: self.remove_tools_folder(f)))
        for folder, chat in shared.items():
            rows.append(self._shared_row(html.escape(short_path(folder)), f"{folder}\nShared by the linked chat {chat}"))
        self._add_menu_rows(menu, rows)

    @staticmethod
    def _shared_row(text, tip):
        """A row for what a linked chat shares: there's no x, as it's unlinked in that chat or the link graph."""
        label = QLabel(f"🔗 {text}")
        label.setToolTip(tip)
        label.setOpenExternalLinks(True)
        label.setStyleSheet(f"color:{MUTED};padding:4px 8px")
        return label

    def _add_menu_rows(self, menu, rows):
        """Put rows in a menu, in a list that gets a scroll bar when it's too tall."""
        rows_widget = QWidget()
        lay = QVBoxLayout(rows_widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        for row in rows:
            lay.addWidget(row)
        scroll = QScrollArea()
        scroll.setWidget(rows_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        hint = rows_widget.sizeHint()
        tall = hint.height() > MENU_LIST_HEIGHT
        scroll.setFixedSize(hint.width() + (scroll.verticalScrollBar().sizeHint().width() if tall else 0),
                            min(hint.height(), MENU_LIST_HEIGHT))
        action = QWidgetAction(menu)
        action.setDefaultWidget(scroll)
        menu.addAction(action)

    def _menu_row(self, label, remove_tip, on_remove):
        """A row in a linked folders or repositories menu: the label and an x to unlink it."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 2, 4, 2)
        remove = QToolButton()
        remove.setText("x")
        remove.setToolTip(remove_tip)
        remove.setAutoRaise(True)
        remove.setStyleSheet("color:#ff8a8a")
        # Deferred: refilling the menu deletes this button, which must not happen inside its own click.
        remove.clicked.connect(lambda: QTimer.singleShot(0, on_remove))
        lay.addWidget(label, 1)
        lay.addWidget(remove)
        return row

    def show_repo_picker(self):
        """Drop down your repositories: the last list straight away, the fresh one when GitHub answers."""
        if not self._own_repos:
            self._repo_note = "Loading your repositories…"
        self._fill_repo_picker()
        self._popup_repo_picker()
        if self._fetching_repos:
            return
        self._fetching_repos = True

        def job():
            try:
                result = github_login.own_repos()
            except github_login.LoginError as e:
                result = str(e)
            try:
                self._repos_fetched.emit(result)
            except RuntimeError:               # the window is gone
                pass

        threading.Thread(target=job, daemon=True).start()

    def _popup_repo_picker(self):
        btn = self.repo_button
        self.repo_picker.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_repos_fetched(self, result):
        """`result`: the repository names, None if git isn't signed in, or an error's text."""
        self._fetching_repos = False
        if isinstance(result, list):
            self._own_repos = result
            self._repo_note = "You have no repositories"
        else:
            self._own_repos = []
            self._repo_note = (f"⚠ {result}" if result else
                               "Not signed in. Use Settings > General to list your repositories")
        self._fill_repo_picker()
        if self.repo_picker.isVisible():
            self._popup_repo_picker()          # it changed size: fit it on the screen again

    def _fill_repo_picker(self):
        rows = self.repo_picker_list
        rows.clear()
        linked = self.client.toolbox.repos
        for name in self._own_repos:
            is_linked = repo_url(name) in linked
            item = QListWidgetItem(f"✓ {name}" if is_linked else name)
            item.setData(Qt.UserRole, name)
            if is_linked:
                item.setFlags(Qt.NoItemFlags)   # the ▾ list unlinks it
                item.setToolTip("Already linked to this chat")
            rows.addItem(item)
        if not self._own_repos:
            item = QListWidgetItem(self._repo_note)
            item.setFlags(Qt.NoItemFlags)
            rows.addItem(item)
        height = rows.sizeHintForRow(0) * rows.count() + 2
        bar = rows.verticalScrollBar().sizeHint().width() if height > MENU_LIST_HEIGHT else 0
        rows.setFixedSize(min(max(rows.sizeHintForColumn(0) + bar + 8, 240), 520), min(height, MENU_LIST_HEIGHT))
        # A menu only measures its rows again when one changes, and resizing the list doesn't count.
        self.repo_picker_action.setDefaultWidget(rows)
        self.repo_picker.removeAction(self.repo_picker_action)
        self.repo_picker.insertAction(self.repo_picker.actions()[0], self.repo_picker_action)
        self.repo_picker.adjustSize()

    def pick_repo(self, item):
        name = item.data(Qt.UserRole)
        if not name:
            return
        self.repo_picker.close()
        self.client.toolbox.add_repo(name)
        self._show_tools_folder()
        self.save_chat()

    def add_repo(self):
        text, ok = QInputDialog.getText(self, "Link a GitHub repository",
                                        "Repository link, e.g. https://github.com/owner/repo")
        if not ok or not text.strip():
            return
        if not self.client.toolbox.add_repo(text):
            self.status_label.setText(f"That isn't a GitHub repository link: {text.strip()}")
            return
        self._show_tools_folder()
        self.save_chat()

    def remove_repo(self, url):
        self.client.toolbox.remove_repo(url)
        self._show_tools_folder()
        self.save_chat()
        self._fill_repo_menu()
        self.repo_menu.adjustSize()

    def show_repo_menu(self):
        self._fill_repo_menu()
        btn = self.repo_list_button
        self.repo_menu.popup(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _fill_repo_menu(self):
        menu = self.repo_menu
        menu.clear()
        box = self.client.toolbox
        repos, shared = box.repos, box.shared_repos()
        if not repos and not shared:
            menu.addAction("No repositories").setEnabled(False)
            return
        rows = []
        for url in repos:
            name = url.removeprefix("https://github.com/")
            label = QLabel(f'<a style="color:#7ab7ff" href="{html.escape(url)}">{html.escape(name)}</a>')
            label.setToolTip(url)
            label.setOpenExternalLinks(True)
            rows.append(self._menu_row(label, f"Unlink {url}", lambda u=url: self.remove_repo(u)))
        for url, chat in shared.items():
            name = html.escape(url.removeprefix("https://github.com/"))
            rows.append(self._shared_row(f'<a style="color:#7ab7ff" href="{html.escape(url)}">{name}</a>',
                                         f"{url}\nShared by the linked chat {chat}"))
        self._add_menu_rows(menu, rows)

    def _show_tools_folder(self):
        box = self.client.toolbox
        can_call = self.can("tools")
        for button in (self.folder_button, self.folder_list_button, self.repo_button, self.repo_list_button,
                       self.server_folder_button, self.server_folder_list_button):
            dim(button, not can_call)
        n, shared = len(box.folders), len(box.shared_folders())
        self.folder_list_button.setText(self._count_label(n, shared))
        tip = self.status_tools_tip
        self.folder_button.setToolTip(f"Link a folder for the tools ({n} linked)\n"
                                      f"Working folder now: {box.working_dir}" + (f"\n{tip}" if tip else ""))
        n, shared = len(box.repos), len(box.shared_repos())
        self.repo_list_button.setText(self._count_label(n, shared))
        self.repo_button.setToolTip(f"Link one of your GitHub repositories, or another by its link ({n} linked)"
                                    + (f"\n{tip}" if tip else ""))
        on = bool(box.groups & {"ubuntu_files", "ubuntu_run"})
        self.server_folder_button.setVisible(on)
        self.server_folder_list_button.setVisible(on)
        n = len(box.server_folders)
        self.server_folder_list_button.setText(self._count_label(n, 0))
        self.server_folder_button.setToolTip(
            "Link a folder on the Ubuntu server for its file and run tools "
            + (f"({n} linked; the tools stay inside them)" if n else "(none linked: every folder the server allows)")
            + (f"\n{tip}" if tip else ""))

    @staticmethod
    def _count_label(own, shared):
        """The ▾ button: how many are this chat's, and how many come from linked chats."""
        return "▾" + (f" {own}" if own else "") + (f" +{shared}" if shared else "")

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

    def on_approval_needed(self, req, client=None, who=None):
        """A change or command wants to run; the worker thread waits for the answer.
        `client` and `who` are for a session working in the background."""
        client = client or self.client
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if req["kind"] == "run" else QMessageBox.Question)
        box.setWindowTitle("Allow this command?" if req["kind"] == "run" else "Allow this change?")
        asker = html.escape(self.botName) if who is None else f"Session <b>{html.escape(who)}</b>, in the background,"
        box.setText(f"{asker} wants to:<br/><br/><b>"
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
        client.answer_approval("allow" if clicked is allow else "always" if clicked is always else "deny")

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
        refused = []
        for p in paths:
            path = Path(p)
            if llmClient.attachment_kind(path) == "image" and not self.can("vision"):
                refused.append(path.name)
                continue
            if path.stat().st_size > llmClient.MAX_ATTACHMENT_BYTES:
                self.on_status(f"{path.name} is over 25 MB and was not attached")
                continue
            if any(a["path"] == str(path) for a in self.pending_attachments):
                continue
            self.pending_attachments.append({"name": path.name, "path": str(path), "kind": llmClient.attachment_kind(path)})
        self.show_pending_attachments()
        if refused:
            self.on_status(f"{self.client.model_name} can't see images; not attached: {', '.join(refused)}")

    def clear_attachments(self):
        self.pending_attachments = []
        self.show_pending_attachments()

    def show_pending_attachments(self):
        n = len(self.pending_attachments)
        self.ui.pushButton_3.setText(f"File ({n})" if n else "File")
        self.status_label.setText("📎 " + ", ".join(a["name"] for a in self.pending_attachments) if n else "")

    # ------------------------------------------------------------------
    # Voice input
    # ------------------------------------------------------------------

    @staticmethod
    def _voice_setting(key, default):
        value = QSettings().value(f"voice/{key}", default)
        if isinstance(default, bool) and not isinstance(value, bool):
            return str(value).lower() in ("true", "1")
        return value

    def show_voice_menu(self, pos):
        menu = QMenu(self)
        auto = menu.addAction("Send automatically after speaking")
        auto.setCheckable(True)
        auto.setChecked(self._voice_setting("autoSend", False))
        auto.toggled.connect(lambda on: QSettings().setValue("voice/autoSend", on))
        langs = menu.addMenu("Language")
        current = self._voice_setting("language", "")
        for label, code in VOICE_LANGUAGES:
            act = langs.addAction(label)
            act.setCheckable(True)
            act.setChecked(code == current)
            act.triggered.connect(lambda _=False, c=code: QSettings().setValue("voice/language", c))
        menu.exec(self.mic_button.mapToGlobal(pos))

    def toggle_voice(self):
        if self.transcriber.busy:
            return
        if self.recorder.recording:
            self.recorder.stop()
            return
        self.recorder.start()
        if self.recorder.recording:
            self.mic_button.setText("■ 0:00")
            self.mic_button.setStyleSheet(MIC_RECORDING_STYLE)
            self._voice_cancel.setEnabled(True)
            self.status_label.setText("🎤 Listening… click ■ or press Ctrl+M to stop, Esc to cancel")

    def cancel_voice(self):
        if self.recorder.recording:
            self.recorder.cancel()
            self._voice_idle()
            self.on_status("")

    def on_voice_level(self, level):
        secs = int(self.recorder.seconds())
        self.mic_button.setText(f"■ {secs // 60}:{secs % 60:02d}")
        bars = round(min(1.0, level * 1.5) * 8)
        self.status_label.setText("🎤 Listening " + "▮" * bars + "▯" * (8 - bars)
                                  + "  click ■ or press Ctrl+M to stop, Esc to cancel")

    def on_voice_recorded(self, wav):
        self._voice_cancel.setEnabled(False)
        if self.recorder.seconds() < 0.3:
            self._voice_idle()
            self.on_status("")
            return
        self.mic_button.setText("…")
        self.mic_button.setStyleSheet(MIC_STYLE)
        self.mic_button.setEnabled(False)
        self.status_label.setText("🎤 Writing down what you said…")
        self.transcriber.transcribe(wav, self._voice_setting("language", ""))

    def on_voice_text(self, text):
        self._voice_idle()
        if not text:
            self.status_label.setText("🎤 Didn't catch anything; try again a little closer to the mic")
            return
        self.on_status("")
        current = self.input.toPlainText()
        cursor = self.input.textCursor()
        before = current[:cursor.position()]
        if before and not before[-1].isspace():
            text = " " + text
        cursor.insertText(text)
        self.input.setTextCursor(cursor)
        self.input.setFocus()
        if self._voice_setting("autoSend", False) and not self.client.busy:
            self.sendMessage()

    def on_voice_failed(self, err):
        print(f"Voice: {err}")
        self._voice_idle()
        self.status_label.setText("🎤 " + err)

    def _voice_idle(self):
        self._voice_cancel.setEnabled(False)
        self.mic_button.setEnabled(True)
        self.mic_button.setText("🎤")
        self.mic_button.setStyleSheet(MIC_STYLE)

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
        pictures = []
        for k, img in enumerate(msg.get("images") or []):
            if img.get("path") and Path(img["path"]).is_file():
                src = QUrl.fromLocalFile(img["path"]).toString()
                tip = html.escape(img.get("prompt", ""), quote=True)
                pictures.append(f"<a href='image:{index}:{k}' title='{tip}'><img src='{src}' height='{IMAGE_HEIGHT}'/></a>")
            elif img.get("path"):
                pictures.append(f"<span style='color:{MUTED}'>🖼 picture missing: {html.escape(img['path'])}</span>")
            else:
                pictures.append(f"<span style='color:#ff6b6b'>🖼 couldn't draw the picture: "
                                f"{html.escape(img.get('error', ''))}</span>")
        if pictures:
            parts.append("<div>" + " ".join(pictures) + "</div>")
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
        spoken = msg.get("spoken") if self.settings["TTS"] and self._translates(msg.get("content", "")) else None
        if self.settings["TTS"] and msg.get("content", "").strip():
            label = SPEAK_LABELS[self.speaker.state_of(index)]
            footer.append(f"<a href='speak:{index}' style='color:#7aa7ff;text-decoration:none;font-size:13px'>{label}</a>")
            if spoken:
                arrow = "▾" if index in self.expanded_spoken else "▸"
                footer.append(f"<a href='spoken:{index}' style='color:{MUTED};text-decoration:none'>"
                              f"🌐 Spoken in {html.escape(spoken.get('language', ''))} {arrow}</a>")
        if footer:
            parts.append(f"<div style='color:{MUTED};font-size:11px'>{' · '.join(footer)}</div>")
        if spoken and index in self.expanded_spoken:
            body = html.escape(spoken.get("text", "")).replace("\n", "<br/>")
            parts.append(f"<div style='color:{MUTED};font-size:12px;margin-left:12px'><i>{body}</i></div>")
        return "".join(parts)

    def _append_message(self, msg, index):
        ts = msg.get("ts")
        time_str = f" <span style='color:{MUTED}'>[{self.format_ts(ts)}]</span>" if ts else ""
        self.chat.moveCursor(QTextCursor.End)    # a click in the chat moves the cursor; insert at the end anyway
        if msg["role"] == "user":
            if msg.get("report"):
                who = f"📨 {html.escape(msg['report'])}"
            elif msg.get("from"):
                who = f"📋 Brief from project {html.escape(msg['from'])}"
            else:
                who = "You"
            self.chat.append(f"<b>{who}</b>{time_str}:")
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
        elif target.startswith("speak:"):
            self.speak(int(target.split(":", 1)[1]))
        elif target.startswith("image:"):
            i, k = (int(x) for x in target.split(":")[1:3])
            img = self._image_at(i, k)
            if img:
                self.image_panel.show(img["path"], (i, k))
                QDesktopServices.openUrl(QUrl.fromLocalFile(img["path"]))
        elif target.startswith("spoken:"):
            if self.client.busy:
                return
            self.expanded_spoken ^= {int(target.split(":", 1)[1])}
            self._rebuild_chat_ui(keep_scroll=True)
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    # ------------------------------------------------------------------
    # Text to speech
    # ------------------------------------------------------------------

    def speak(self, index, reacted=False):
        """Play, pause or resume reply `index` with this chat's voice. With the 3D
        model on she acts it out too (`reacted`: its first act was played already)."""
        if not self.settings["TTS"] or not 0 <= index < len(self.chat_markdown):
            return
        s = self.settings
        if s["Avatar"] and self.speaker.key != index:
            msg = self.chat_markdown[index]
            self.performer.prepare(index, msg.get("content", ""), msg.get("acts"), skip_first=reacted)
        text = self.chat_markdown[index].get("content", "")
        translate_model = (self.client.model_name or "") if self._translates(text) else None
        self.speaker.toggle(index, text,
                            s["TTS Engine"], s["TTS Voice"], s["TTS Speed"], translate_model)

    def _translates(self, text: str) -> bool:
        return self.settings["TTS Translate"] or voice.needs_translating(text, self.settings["TTS Voice"])

    def on_spoken(self, index, language, text):
        """A reply was translated for its voice: keep the translation with the message."""
        if 0 <= index < len(self.chat_markdown) and self.chat_markdown[index].get("role") == "assistant":
            msg = self.chat_markdown[index]
            if msg.get("spoken") != {"language": language, "text": text}:
                msg["spoken"] = {"language": language, "text": text}
                self.save_chat()
                self.on_speaker_changed()

    def on_speaker_changed(self):
        # Redrawing mid-answer would wipe the streamed text, so wait for the answer to finish.
        if self.response_start_pos is not None:
            self._speak_redraw_pending = True
        else:
            self._rebuild_chat_ui(keep_scroll=True)

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
        self._streamed_images = []
        self._tag_filter = avatar.TagFilter() if self.settings["Avatar"] else None
        self._reacted = False
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
        if self._tag_filter is not None:
            text, acts = self._tag_filter.feed(text)
            if acts and not self._reacted:          # she reacts as the answer appears
                self.performer.react(acts[0])
                self._reacted = True
            if not text:
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
            self._after_answer(None, "it was stopped")
            return

        end_time = time.time()
        content, acts = result.get("content", ""), []
        if self.settings["Avatar"]:
            content, acts = avatar.strip_tags(content)
        msg = llmClient.reply_message(
            result, content, self.response_start_time,
            self.thinking_time or round(end_time - (self.thinking_started_at or end_time), 1))
        if acts:
            msg["acts"] = acts            # her stage directions, with where they were in the text
        self.chat_markdown.append(msg)

        cursor = self._clear_streamed()
        cursor.insertHtml(self.render_assistant(msg, len(self.chat_markdown) - 1))
        cursor.insertBlock()
        self.chat.moveCursor(QTextCursor.End)
        self._end_response()
        self.save_chat()
        chat = self.chat_name()
        if not result.get("aborted") and chat_links.summary_due(self.links, chat, len(self.chat_markdown)):
            self.client.summarize_chat(chat, self.chat_markdown)
        if self._speak_redraw_pending:
            self._speak_redraw_pending = False
            self._rebuild_chat_ui()
        s = self.settings
        if s["TTS"] and (s["TTS Auto Play"] or s["Avatar"]) and msg["content"].strip() \
                and not result.get("aborted"):
            self.speak(len(self.chat_markdown) - 1, reacted=self._reacted)
        self._after_answer(msg["content"] if not result.get("aborted") else None, "it was stopped")

    # ------------------------------------------------------------------
    # Pictures (ComfyUI, through the server)
    # ------------------------------------------------------------------

    def _image_at(self, i, k):
        if 0 <= i < len(self.chat_markdown):
            images = self.chat_markdown[i].get("images") or []
            if 0 <= k < len(images) and images[k].get("path"):
                return images[k]
        return None

    def show_latest_image(self):
        for i in range(len(self.chat_markdown) - 1, -1, -1):
            images = self.chat_markdown[i].get("images") or []
            for k in range(len(images) - 1, -1, -1):
                if images[k].get("path") and self.image_panel.show(images[k]["path"], (i, k)):
                    return
        self.image_panel.clear()

    def on_image(self, entry):
        """A picture arrived while the reply streams; it joins the reply when the reply is done."""
        if entry.get("path"):
            self.image_panel.show(entry["path"], (len(self.chat_markdown), len(self._streamed_images)))
            self.status_label.setText("🖼 Picture ready")
        else:
            self.status_label.setText("🖼 " + entry.get("error", "couldn't draw the picture"))
        self._streamed_images.append(entry)

    def redraw_image(self):
        """Reload Image: draw the picture in the panel again, same prompt, new seed."""
        where = self.image_panel.where
        img = self._image_at(*where) if where else None
        if img is None:
            self.status_label.setText("🖼 No picture to draw again yet")
            return
        workflow = self.settings["Image Workflow"] or img.get("workflow", "")
        if self.redrawing or self.client.busy or not workflow:
            return
        self.redrawing = True
        self.ui.pushButton_7.setEnabled(False)
        self.status_label.setText("🖼 Drawing again…")
        self.redrawer.start(self.client, where, img.get("prompt", ""), img.get("negative_prompt", ""), workflow)

    def on_redrawn(self, where, entry, err):
        self.redrawing = False
        self.ui.pushButton_7.setEnabled(self.isShowImgWindow and not self.isShowImgWindowLock)
        if err:
            self.status_label.setText(f"🖼 {err}")
            return
        i, k = where
        if self._image_at(i, k) is None:          # the chat changed meanwhile
            return
        self.chat_markdown[i]["images"][k] = entry    # the old file stays in the Images folder
        self.save_chat()
        self.on_status("")
        if self.response_start_pos is None:
            self._rebuild_chat_ui(keep_scroll=True)
        self.image_panel.show(entry["path"], where)

    def _show_image_panel(self):
        """The panel and Reload Image are there when the chat has a workflow."""
        on = bool(self.settings["Image Workflow"])
        self.isShowImgWindowLock = not on
        self.ui.pushButton.setEnabled(on and not self.isShowChatList)
        if on and self.isShowImgWindow and not self.isShowChatList:
            self.showImgWindow()
        else:
            self.hideImgWindow()
        if on:
            self.show_latest_image()

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
        self._after_answer(None, err)

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

        self.img_spacer.changeSize(0, 20, QSizePolicy.Fixed, QSizePolicy.Fixed)
        layoutH3.invalidate()

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

        self.img_spacer.changeSize(40, 20, QSizePolicy.Expanding, QSizePolicy.Fixed)
        layoutH3.invalidate()
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
        self.chat_list.setVisible(False)
        self.isShowChatList = False
        if not self.isShowImgWindowLock and self.isShowImgWindow:
            self.showImgWindow()
            self.ui.pushButton.setEnabled(True)

    def showChatList(self):
        self.chat_list.setVisible(True)
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
        self._flush_pending()
        self.runner.give_back(self.chat_name())

        if path == "":
            path = self.get_first_json_file_os(str(paths.CHAT_DIR))
            if path is None:
                return

        chatHist = read_json_file(path)
        if chatHist is None:
            return
        self.speaker.stop()
        self.chat_path = path

        self.update_lastChat(path)
        self.ui.lineEdit.setText(chatHist.get("Name") or Path(path).stem)

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

        self.client.summary = chatHist.get("Summary")
        folders = chatHist.get("Tools Folders")
        if folders is None:          # 0.5 saved one working folder, which defaulted to home
            old = chatHist.get("Tools Folder")
            folders = [old] if old and Path(old) != Path.home() else []
        self.client.toolbox.set_folders(folders)
        self.client.toolbox.set_repos(chatHist.get("Tools Repos") or [])
        self.client.toolbox.set_server_folders(chatHist.get("Server Folders") or [])
        self.own_settings = chat_settings.from_chat(chatHist)
        self._load_personality()
        self.set_settings(self._running_settings())
        if not isinstance(chatHist.get("Settings"), dict):
            self.save_chat()         # move a pre-0.9 chat's settings into the chat file now

        self.chat_list.fill(logs["ChatList"], logs.get("Sections"))

        self.expanded_thinking = set()
        self.expanded_tools = set()
        self.expanded_spoken = set()
        self.clear_attachments()
        self._rebuild_chat_ui()
        self.show_latest_image()
        self.links_changed.emit()
        self._show_runner_state()
        self.runner.kick()
        self._follow_up()

    def save_chat(self):
        if not self.chat_path:
            return

        # A chat keeps its own settings in its file, and only what it changed from its personality
        # counts as changed, so edits to the personality reach it and unlinking gives back its own.
        overrides = []
        if self.personality:
            self.own_settings, overrides = personalities.split(self.settings, self.own_settings, self.personality,
                                                               self.skipped)
        else:
            self.own_settings = self.settings
        if chat_links.set_overrides(self.links, self.chat_name(), overrides):
            self.save_links()

        data = {
            "Name": self.ui.lineEdit.text(),
            "Settings": self.own_settings,
            "Tools Folders": [str(f) for f in self.client.toolbox.folders],
            "Tools Repos": self.client.toolbox.repos,
            "Server Folders": self.client.toolbox.server_folders,
            "Chat": self.chat_markdown,
            "Summary": self.client.summary,
        }

        try:
            with open(self.chat_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Save failed: {e}")

    # ------------------------------------------------------------------
    # Chat settings (character, model, options)
    # ------------------------------------------------------------------

    def set_settings(self, settings):
        """Use `settings` for this chat: character, model and options."""
        self.settings = chat_settings.normalize(settings)
        s = self.settings
        self.botName = s["Character"]
        self.client.set_preset(s["Pretext"])
        self.client.character = s["Character"]
        self.client.stage_note = self._stage_note if s["Avatar"] else None
        self.avatar_link.watch("chat", s["Avatar"])
        if s["Avatar"]:
            self.avatar_link.personality(s["Avatar Personality"])
        self.client.temperature = s["Temperature"]
        if not s["Model"] and self.models:
            s["Model"] = next(iter(self.models))
        if self.is_lead() and s["Model"] in self.models and not projectWork.fits_lead(self.models[s["Model"]]):
            s["Model"] = projectWork.lead_model(self.models) or s["Model"]
        self.client.set_model(s["Model"] or None)
        self._fill_model_combo()            # also applies Think, Web Search, Tools and Pictures
        self._show_image_panel()

    def on_avatar_status(self, info):
        """The 3D model was started or closed while this chat uses her."""
        if self.settings["Avatar"]:
            self.status_label.setText("🧍 " + ("The 3D model is connected." if info is not None
                                              else self.avatar_link.missing_text()))

    def _stage_note(self) -> str:
        """The 3D model's system note (on llmClient's worker thread); "" while she isn't running."""
        info = self.avatar_link.catalog()
        if info is None:
            return ""
        return avatar.system_note(info, self.settings["Avatar Personality"], self.settings["Avatar Notes"])

    def apply_settings(self, title, settings, personality=None, skipped=()):
        """Save from the Chat Settings window for the open chat; a new title renames its file."""
        if self.client.busy:
            self.client.abort_generation()
            self._end_response()
        old_model = self.client.model_name
        tts_keys = ("TTS", "TTS Engine", "TTS Voice", "TTS Speed", "TTS Translate")
        old_tts = [self.settings[k] for k in tts_keys]
        if personality != self.personality_name:
            chat_links.set_speaks_as(self.links, self.chat_name(), personality)
        chat_links.set_skipped(self.links, self.chat_name(), skipped if personality else ())
        self.save_links()
        self._load_personality()
        self.set_settings(settings)
        if [self.settings[k] for k in tts_keys] != old_tts:
            self.speaker.stop()                 # the voice changed or TTS is off: the next Play reads it anew
            self._rebuild_chat_ui(keep_scroll=True)
        if self.client.model_name != old_model:
            self.client.summary = None      # written by the old model; harmless to redo
        if title != self.ui.lineEdit.text():
            self.ui.lineEdit.setText(title)
            self._rename_chat_file(title)
        self.save_chat()
        self._rebuild_chat_ui()
        self.links_changed.emit()

    def create_chat(self, title, settings, personality=None, skipped=()):
        """A new, empty chat with these settings, opened straight away."""
        path = self._free_chat_path(title)
        saved = personalities.load(personality) if personality else None
        if saved:
            chat_links.set_speaks_as(self.links, path.stem, personality)
            chat_links.set_skipped(self.links, path.stem, skipped)
            chat_links.set_overrides(self.links, path.stem,
                                     personalities.split(settings, settings, saved, skipped)[1])
            self.save_links()
        data = {"Name": title, "Settings": chat_settings.normalize(settings), "Tools Folders": [], "Chat": []}
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        self.load_chat(str(path))

    def _free_chat_path(self, title, current=None):
        """CHAT_DIR/<title>.json, or <title> (1).json and so on if that's taken by another chat."""
        base = safe_file_name(title)
        path, i = paths.CHAT_DIR / f"{base}.json", 1
        # The chat's folder (its memory notes) goes by the same name, so that must be free too.
        while ((path.exists() or (paths.CHAT_DIR / path.stem).exists())
               and not (current and path.resolve() == Path(current).resolve())):
            path = paths.CHAT_DIR / f"{base} ({i}).json"
            i += 1
        return path

    def _rename_chat_file(self, title):
        self.rename_chat(self.chat_name(), title)

    def rename_chat(self, chat, title) -> str:
        """Rename a chat's file and memory folder to fit `title`, open or not. Returns its new name."""
        is_open = chat == self.chat_name()
        old = paths.CHAT_DIR / f"{chat}.json"
        new = self._free_chat_path(title, current=old)
        if new == old:
            return chat
        try:
            os.replace(old, new)
            if (paths.CHAT_DIR / old.stem).exists():
                os.replace(paths.CHAT_DIR / old.stem, paths.CHAT_DIR / new.stem)
        except OSError as e:
            print(f"Rename failed: {e}")
            return chat
        if is_open:
            self.chat_path = str(new)
        else:
            data = projectWork._read(new)
            if data:
                data["Name"] = title
                projectWork._write(new, data)
        self.runner.rename(old.stem, new.stem)
        chat_links.rename_node(self.links, chat_links.CHAT, old.stem, new.stem)
        self.save_links()
        if is_open:
            self._apply_options()           # the memory folder moved with it
        logs = read_json_file(paths.CHAT_INDEX_FILE) or {"LastOpened": "", "ChatList": []}
        logs["ChatList"] = [new.stem if c == old.stem else c for c in logs.get("ChatList", [])]
        if new.stem not in logs["ChatList"]:
            logs["ChatList"].append(new.stem)
        sections = chat_sections.normalize(logs.get("Sections"), [old.stem, *logs["ChatList"]])
        chat_sections.rename_chat(sections, old.stem, new.stem)
        logs["Sections"] = sections
        if is_open:
            logs["LastChat"] = str(new)
        with open(paths.CHAT_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=4)
        self.chat_list.fill(logs["ChatList"], sections)
        return new.stem

    def save_sections(self, sections):
        logs = read_json_file(paths.CHAT_INDEX_FILE) or {"LastOpened": "", "ChatList": []}
        logs["Sections"] = sections
        with open(paths.CHAT_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=4)

    # ------------------------------------------------------------------
    # Links to other chats, projects and personalities (chat_links.py)
    # ------------------------------------------------------------------

    @staticmethod
    def all_chats():
        return sorted(p.stem for p in paths.CHAT_DIR.glob("*.json"))

    def chat_name(self):
        return Path(self.chat_path).stem if self.chat_path else ""

    def save_links(self):
        try:
            chat_links.save(self.links, paths.LINKS_FILE)
        except OSError as e:
            print(f"Saving links failed: {e}")

    def _load_personality(self):
        name = chat_links.speaks_as(self.links, self.chat_name())
        self.personality = personalities.load(name) if name else None
        self.personality_name = name if self.personality else None
        self.skipped = list(chat_links.skipped_of(self.links, self.chat_name()))

    def _running_settings(self):
        return self._resolve(self.chat_name(), self.own_settings, self.personality, self.skipped)

    def _model_lines(self) -> list:
        """The server's models a project chat may give a session: "qwen3.8:27b (27B; thinking, tools)".
        Only ones that call tools, since a session needs them to touch files, commands and the web."""
        lines = []
        for mid, m in self.models.items():
            if m.get("capabilities") and "tools" not in m["capabilities"]:
                continue
            caps = [c for c in m.get("capabilities") or [] if c != "completion"]
            about = "; ".join(x for x in (m.get("parameter_size"), ", ".join(caps)) if x)
            lines.append(f"{mid} ({about})" if about else mid)
        return lines

    def _resolve(self, chat, own, personality, skipped):
        """A chat's settings under its personality. A project's chat always thinks, and takes its
        personality's model only if that one thinks and calls tools; otherwise it keeps its own."""
        lead = bool(chat_links.led_by(self.links, chat))
        if lead and personality:
            model = self.models.get(personality.get("Model") or "")
            if not model or not projectWork.fits_lead(model):
                skipped = [*skipped, "LLM model"]
        s = personalities.resolve(own, personality, chat_links.overrides_of(self.links, chat), skipped)
        if lead:
            s["Think"] = True
        return s

    def settings_of(self, chat, data):
        """What another chat runs with, from its file's `data`."""
        name = chat_links.speaks_as(self.links, chat)
        personality = personalities.load(name) if name else None
        skipped = chat_links.skipped_of(self.links, chat) if personality else []
        return self._resolve(chat, chat_settings.from_chat(data), personality, skipped)

    def is_lead(self) -> bool:
        """Whether the open chat is a project's own chat."""
        return bool(self.chat_path and chat_links.led_by(self.links, self.chat_name()))

    def _link_scope(self):
        """What the open chat's links reach, for the model; replaced whole, as a reply may be reading it."""
        return self.link_scope_of(self.chat_name())

    def link_scope_of(self, chat):
        personality = chat_links.speaks_as(self.links, chat)
        places = {f"project:{p}": paths.project_memory_dir(p) for p in chat_links.projects_of(self.links, chat)}
        led = chat_links.led_by(self.links, chat)
        if led:
            places[f"project:{led}"] = paths.project_memory_dir(led)
        if personality:
            places[f"personality:{personality}"] = paths.personality_memory_dir(personality)
        chats = {}
        for name, why in chat_links.readable_chats(self.links, chat).items():
            summary = self.links["Summaries"].get(name, {}).get("Text", "")
            chats[name] = LinkedChat(paths.CHAT_DIR / f"{name}.json", why, summary)
        folders, repos = {}, {}
        for name, chat in chats.items():
            shared_folders, shared_repos = shared_by(chat.path)
            for folder in shared_folders:
                folders.setdefault(folder, name)
            for url in shared_repos:
                repos.setdefault(url, name)
        return LinkScope(chats=chats, places=places, folders=folders, repos=repos)

    def save_personality(self, name, settings, skipped=()):
        """Save a personality. What changed reaches every chat speaking as it, even one that had changed it."""
        before = personalities.load(name)
        personalities.save(name, settings, skipped)
        after = personalities.load(name)
        if before and after:
            changed = [k for k in personalities.PERSONALITY_KEYS if before[k] != after[k]]
            chat_links.drop_overrides(self.links, name, changed)
        self.links_updated()

    def links_updated(self):
        """The links or a personality changed: save, and let the open chat follow."""
        self.save_links()
        before = (self.personality_name, self.personality, self.skipped)
        self._load_personality()
        if (self.personality_name, self.personality, self.skipped) != before:
            if self.client.busy:
                self.client.abort_generation()
                self._end_response()
            self.set_settings(self._running_settings())
            self._rebuild_chat_ui(keep_scroll=True)
        else:
            self._apply_options()
        self.links_changed.emit()

    def on_chat_summarized(self, chat, upto, text):
        if chat in self.all_chats():
            self.links["Summaries"][chat] = {"Upto": upto, "Text": text}
            self.save_links()
            self._apply_options()

    def memory_folder(self):
        """Where the open chat's memory notes go, or None with no chat open."""
        return paths.chat_memory_dir(Path(self.chat_path).stem) if self.chat_path else None

    # ------------------------------------------------------------------
    # A project's own chat and its sessions (projectWork.py)
    # ------------------------------------------------------------------

    def is_answering(self) -> bool:
        return self.client.busy or self.response_start_pos is not None

    def answer_open_chat(self) -> bool:
        """Answer the open chat's last message now, for the session runner. False if there's nothing to answer."""
        if self.is_answering() or not self.chat_markdown or self.chat_markdown[-1].get("role") != "user":
            return False
        self._rebuild_chat_ui()
        self._start_response()
        return True

    def _after_answer(self, content, why):
        """An answer in the main window ended: deliver what waited for it, and let the sessions go on."""
        chat = self.chat_name()
        self._flush_pending()
        if self.runner.in_place and self.runner.current == chat:
            self.runner.finished_in_place(chat, content, why)
        else:
            self.runner.kick()
            self._follow_up()

    def _follow_up(self):
        """All sessions are done and the open project chat has new reports: it answers them,
        MAX_FOLLOW_UPS times at most for each message you send, so it can't go round forever."""
        chat = self.chat_name()
        if (self.runner.current is not None or self.runner.queue or not self.is_lead() or self.is_answering()
                or not self.chat_markdown or not self.chat_markdown[-1].get("report")):
            return
        done = self._follow_ups.get(chat, 0)
        if done < MAX_FOLLOW_UPS:
            self._follow_ups[chat] = done + 1
            self.answer_open_chat()

    def _flush_pending(self):
        if not self._pending_messages:
            return
        self.chat_markdown += self._pending_messages
        self._pending_messages = []
        self.save_chat()
        self._rebuild_chat_ui(keep_scroll=True)

    def append_message(self, chat, msg):
        """Add a message to a chat, open or not. The open chat takes it once it's done answering."""
        if chat == self.chat_name():
            if self.is_answering():
                self._pending_messages.append(msg)
            else:
                self.chat_markdown.append(msg)
                self.save_chat()
                self._rebuild_chat_ui(keep_scroll=True)
            return
        path = paths.CHAT_DIR / f"{chat}.json"
        data = projectWork._read(path)
        if data:
            data.setdefault("Chat", []).append(msg)
            projectWork._write(path, data)

    def session_finished(self, chat, content, why):
        """A session is done with its brief: show its answer if it's open, and report to its project's chat."""
        if chat == self.chat_name() and not self.is_answering():
            saved = projectWork._read(paths.CHAT_DIR / f"{chat}.json").get("Chat") or []
            if len(saved) > len(self.chat_markdown):
                self.chat_markdown = saved
                self.client.summary = projectWork._read(paths.CHAT_DIR / f"{chat}.json").get("Summary")
                self._rebuild_chat_ui()
        project = self.runner.origin.pop(chat, None)
        lead = chat_links.lead_of(self.links, project) if project else None
        if lead:
            self.append_message(lead, projectWork.report_message(chat, content, why))
        self.links_changed.emit()
        if not self.runner.queue:
            self._follow_up()

    def _show_runner_state(self):
        """A session working in the background can't be written to until it's done."""
        busy = self.runner.busy_with(self.chat_name())
        self.input.setReadOnly(busy)
        self.ui.pushButton_5.setEnabled(not busy)
        if busy:
            self.status_label.setText("⏳ Working on its task in the background…")
        elif self.status_label.text().startswith("⏳"):
            self.status_label.setText("")

    def _brief(self, project, brief) -> dict:
        return {"role": "user", "content": brief, "ts": self.now_ts(), "from": project}

    def start_session(self, project, title, brief, model=None) -> str:
        """A new chat in the project, set up like the project's chat, with the brief as its first message.
        It can run on another model, unless the project speaks as a personality: then every session
        uses the project chat's model, so she behaves the same in each. Returns what happened, for the model."""
        lead = chat_links.lead_of(self.links, project)
        lead_data = projectWork._read(paths.CHAT_DIR / f"{lead}.json") if lead else {}
        personality = chat_links.speaks_as(self.links, lead) if lead else None
        settings = chat_settings.normalize(chat_settings.from_chat(lead_data))
        lead_model = self.settings_of(lead, lead_data)["Model"] if lead else settings["Model"]
        note = ""
        if personality:
            if model and model != lead_model:
                note = f" It runs on {lead_model}, like you, not {model}, so {personality} stays herself."
            model = lead_model
        elif model and (model not in self.models or "tools" not in (self.models[model].get("capabilities")
                                                                     or ["tools"])):
            usable = ", ".join(line.split(" ")[0] for line in self._model_lines())
            return f"Error: {model} isn't a model on the server that can call tools. Pick one of: {usable}"
        settings["Model"] = model or lead_model
        path = self._free_chat_path(title)
        name = path.stem
        data = {"Name": title, "Settings": settings,
                "Tools Folders": lead_data.get("Tools Folders") or [], "Tools Repos": lead_data.get("Tools Repos") or [],
                "Server Folders": lead_data.get("Server Folders") or [], "Chat": [self._brief(project, brief)]}
        path.parent.mkdir(parents=True, exist_ok=True)
        projectWork._write(path, data)
        if personality:
            chat_links.set_speaks_as(self.links, name, personality)
            skipped = [g for g in chat_links.skipped_of(self.links, lead) if g != "LLM model"]
            chat_links.set_skipped(self.links, name, [*skipped, "LLM model"])   # the model set above
        chat_links.add_link(self.links, chat_links.node(chat_links.CHAT, name),
                            chat_links.node(chat_links.PROJECT, project), chat_links.MEMBER)
        logs = read_json_file(paths.CHAT_INDEX_FILE) or {"LastOpened": "", "ChatList": []}
        logs.setdefault("ChatList", [])
        if name not in logs["ChatList"]:
            logs["ChatList"].insert(max(len(logs["ChatList"]) - 1, 0), name)   # under the open chat
            with open(paths.CHAT_INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump(logs, f, indent=4)
        self.chat_list.fill(logs["ChatList"], logs.get("Sections"))
        self.links_updated()
        self.runner.add(name, project)
        return (f"Started session {name} on {settings['Model']} in project {project} with your brief. It works on "
                f"it in the background; its answer comes back here as a report.{note}")

    def assign_session(self, project, chat, brief) -> str:
        """Another task for a chat that exists; it joins the project if it isn't in it."""
        if chat not in self.all_chats():
            names = ", ".join(chat_links.sessions_of(self.links, project)) or "none yet"
            return f"Error: there's no chat called {chat}. The project's sessions: {names}"
        if chat_links.led_by(self.links, chat):
            return f"Error: {chat} is a project's own chat, not a session"
        note = ""
        if chat not in chat_links.sessions_of(self.links, project):
            chat_links.add_link(self.links, chat_links.node(chat_links.CHAT, chat),
                                chat_links.node(chat_links.PROJECT, project), chat_links.MEMBER)
            self.links_updated()
            note = f"Linked {chat} to project {project}. "
        self.append_message(chat, self._brief(project, brief))
        state = self.runner.state_of(chat)
        self.runner.add(chat, project)
        after = " after its current task" if state == "working" else ""
        return f"{note}Gave {chat} the task; it works on it in the background{after} and reports back here."

    def project_chat(self, project) -> str:
        """The project's own chat, made the first time: Gemma 4 if the server has it, thinking on."""
        lead = chat_links.lead_of(self.links, project)
        if lead and (paths.CHAT_DIR / f"{lead}.json").exists():
            return lead
        settings = chat_settings.defaults()
        settings["Model"] = projectWork.lead_model(self.models) or self.settings["Model"]
        path = self._free_chat_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        projectWork._write(path, {"Name": project, "Settings": settings, "Tools Folders": [], "Chat": []})
        chat_links.set_lead(self.links, project, path.stem)
        self.save_links()
        return path.stem

    def open_project_chat(self, project):
        self.qs_chat(self.project_chat(project))

    def ask_start_fresh(self, project):
        lead = chat_links.lead_of(self.links, project)
        if not lead:
            self.open_project_chat(project)
            return
        if QMessageBox.question(
                self, "Start fresh",
                f"Start {project}'s chat fresh?\n\nIts model writes a context.md handover first, and the new chat "
                f"starts with only that, so its context is short again. The current chat is kept as "
                f"\"{project} (old)\", and the new one can still read it and every session.") == QMessageBox.Yes:
            self.start_fresh(project)

    def start_fresh(self, project):
        """Write the project chat's context.md, then swap in a new chat that starts from it (_on_handover)."""
        lead = chat_links.lead_of(self.links, project)
        if not lead or self._handing_over:
            return
        if lead == self.chat_name():
            if self.is_answering():
                self.status_label.setText("Wait for the project chat to finish answering, then start it fresh")
                return
            history = self.chat_markdown
        else:
            history = projectWork._read(paths.CHAT_DIR / f"{lead}.json").get("Chat") or []
        if not [m for m in history if m.get("role") in ("user", "assistant")]:
            self.status_label.setText(f"{project}'s chat is empty, so it's fresh already")
            return
        model = self.settings_of(lead, projectWork._read(paths.CHAT_DIR / f"{lead}.json"))["Model"]
        self._handing_over = project
        self.status_label.setText(f"📝 Writing context.md for {project}'s fresh start…")
        sessions = projectWork.ProjectDesk(self, project, None)._sessions()
        self.handover.start(self.client.base_url, model, project, sessions, history,
                            self.client.contexts.get(model, llmClient.DEFAULT_CONTEXT))

    def _on_handover(self, project, text, err):
        self._handing_over = None
        if err:
            self.status_label.setText(f"Couldn't write the handover, so {project}'s chat stays as it was: {err}")
            return
        new = self.fresh_lead(project, text)
        if new:
            self.qs_chat(new)
            self.status_label.setText(f"{project} started fresh. Its handover is in context.md "
                                      "(Chat Settings > Memory > Open folder)")

    def fresh_lead(self, project, context) -> str:
        """Keep the project chat as "<project> (old)" and make a new one like it, with `context` as its
        context.md memory note. Returns the new chat's name, or "" if it has no project chat."""
        old = chat_links.lead_of(self.links, project)
        if not old:
            return ""
        if old == self.chat_name():
            self._flush_pending()
            self.save_chat()
        old = self.rename_chat(old, f"{project} (old)")
        data = projectWork._read(paths.CHAT_DIR / f"{old}.json")
        personality = chat_links.speaks_as(self.links, old)
        skipped, overrides = chat_links.skipped_of(self.links, old), chat_links.overrides_of(self.links, old)
        chat_links.retire_lead(self.links, project)
        settings = chat_settings.normalize(chat_settings.from_chat(data))
        settings["Memory"] = True                       # context.md is a memory note
        path = self._free_chat_path(project)
        projectWork._write(path, {"Name": project, "Settings": settings,
                                  "Tools Folders": data.get("Tools Folders") or [],
                                  "Tools Repos": data.get("Tools Repos") or [],
                                  "Server Folders": data.get("Server Folders") or [], "Chat": []})
        new = path.stem
        chat_links.set_lead(self.links, project, new)
        if personality:
            chat_links.set_speaks_as(self.links, new, personality)
            chat_links.set_skipped(self.links, new, skipped)
            chat_links.set_overrides(self.links, new, overrides)
        notes, old_notes = paths.chat_memory_dir(new), paths.chat_memory_dir(old)
        notes.mkdir(parents=True, exist_ok=True)
        if old_notes.is_dir():                          # its rules and notes carry over; context.md is new
            for note in old_notes.glob("*.md"):
                if note.name.lower() != "context.md":
                    (notes / note.name).write_text(note.read_text(encoding="utf-8"), encoding="utf-8")
        (notes / "context.md").write_text(context.strip() + "\n", encoding="utf-8")
        self.save_links()
        self.links_changed.emit()
        return new

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
        self._follow_ups.pop(self.chat_name(), None)
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
        self.speaker.stop()
        del self.chat_markdown[i + 1:]
        self.chat_markdown[i]["content"] = new_text
        self.chat_markdown[i]["ts"] = self.now_ts()
        self._rebuild_chat_ui()
        self._start_response()

    def delete_last_user_exchange(self):
        i = self._last_user_index()
        if i is None or self.client.busy:
            return
        self.speaker.stop()
        del self.chat_markdown[i:]
        self._rebuild_chat_ui()
        self.save_chat()

    def regenerate_last_response(self):
        if self.client.busy:
            return
        i = self._last_user_index()
        if i is None:
            return
        self.speaker.stop()
        del self.chat_markdown[i + 1:]
        self.expanded_thinking = {x for x in self.expanded_thinking if x <= i}
        self.expanded_tools = {x for x in self.expanded_tools if x <= i}
        self.expanded_spoken = {x for x in self.expanded_spoken if x <= i}
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