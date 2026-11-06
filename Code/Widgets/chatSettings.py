"""The settings window of one chat: title, character, model, options and voice.

It edits a copy of the chat's settings (chat_settings.py) and hands them back
to main.py on Save; ChatMain applies and saves them in the chat file. The
model list is the same one the main window's quick model picker shows. The
voice lists come from the server (GET /v1/audio/voices) the first time text
to speech is switched on here, then GPT-SoVITS's is checked every few seconds while
it's on and the window is open, so it and its new voices appear once they're there.
The ComfyUI workflows (GET
/v1/images/workflows) each time their list is opened. "3D model" turns the
voice on with it and keeps looking for the 3D model (avatar.py). Memory lists
the chat's memory notes and opens their folder. Personality picks a saved
personality for the chat to talk as (personalities.py) and saves new ones.

The same window edits a saved personality (open_for_personality), showing only what
a personality holds; saving it reaches every chat speaking as it.
"""

import chat_links
import threading

import chat_settings
import paths
import personalities
import voice
from local_tools import memory
from Widgets.chatMain import LiveComboBox, WEB_LABELS, safe_file_name

from PySide6.QtWidgets import QMainWindow, QFileDialog, QComboBox, QInputDialog, QMessageBox, QScrollArea
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QSettings, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from pathlib import Path

IMAGE_MODE_LABELS = {"auto": "Auto: when a picture fits", "on": "With every reply"}


class ChatSettings(QMainWindow):
    voices_checked = Signal(object, str)    # from the worker thread: the list (None if it failed), its engine
    VOICE_CHECK_MS = 5000

    def __init__(self, chat_main):
        super().__init__()
        self.chat_main = chat_main
        self.is_new = True             # True: Save creates a chat; False: it updates the open one
        self.model = ""
        self.tts_voice = ""            # the chosen voice id; kept even if the server doesn't list it
        self._tts_engine = chat_settings.DEFAULTS["TTS Engine"]
        self.engine_info = {}          # engine id -> {"name", "available", "install", "speed"}
        self.voice_list = None         # from the server: {"engines": [...], "voices": [...]}
        self.image_workflow = ""       # the chosen workflow id; kept even if the server doesn't list it
        self.workflow_list = None      # from the server: [{"id", "name", "usable", "error"}]
        self.editing = None            # the personality being edited; None = a chat
        self.preview = voice.Speaker(lambda: chat_main.client.base_url, paths.AUDIO_DIR, self)
        self.preview.changed.connect(self._show_preview_state)
        self.preview.failed.connect(lambda err: self.ui.ttsStatusLabel.setText(err))
        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(self.VOICE_CHECK_MS)
        self._voice_timer.timeout.connect(self._check_voices)
        self._voices_checking = False
        self.voices_checked.connect(self._on_voices_checked)
        chat_main.avatar_link.status.connect(self._show_avatar_status)
        self.load_ui()
        self.setup_connections()
        chat_main.model_changed.connect(self.on_main_model_changed)

    def load_ui(self):
        ui_file = QFile(paths.ui_file("Chat Settings.ui"))
        if not ui_file.open(QFile.ReadOnly):
            print(f"Cannot open UI file: {ui_file.errorString()}")
            return
        self.ui = QUiLoader().load(ui_file)
        ui_file.close()
        # It scrolls: with the voice and 3D model sections open it's taller than most screens.
        scroll = QScrollArea()
        scroll.setWidget(self.ui)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet(self.ui.styleSheet())
        self.setCentralWidget(scroll)
        self.resize(self.ui.size())

        self.model_combo = LiveComboBox(self.refresh_models)
        self.model_combo.setToolTip(self.chat_main.model_combo.toolTip())
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.ui.modelLayout.addWidget(self.model_combo, 1)

        for mode in chat_settings.WEB_MODES:
            self.ui.webCombo.addItem(WEB_LABELS[mode], mode)

        self.workflow_combo = LiveComboBox(self.refresh_workflows)
        self.workflow_combo.setToolTip("ComfyUI workflows on the server (Server/ollama/workflows). "
                                       "Off: this chat doesn't draw.")
        self.ui.imageWorkflowLayout.addWidget(self.workflow_combo, 1)
        for mode in chat_settings.IMAGE_MODES:
            self.ui.imageModeCombo.addItem(IMAGE_MODE_LABELS[mode], mode)

        lo, hi = chat_settings.TEMPERATURE_RANGE
        spin, slider = self.ui.temperatureSpin, self.ui.temperatureSlider
        spin.setRange(lo, hi)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        slider.setRange(int(lo * 100), int(hi * 100))
        slider.setPageStep(10)

        lo, hi = chat_settings.TTS_SPEED_RANGE
        self.ui.ttsSpeedSpin.setRange(lo, hi)
        self.ui.ttsSpeedSpin.setSingleStep(0.1)
        self.ui.ttsSpeedSpin.setDecimals(2)
        self.ui.ttsBox.setVisible(False)

        for name in chat_settings.AVATAR_PERSONALITIES:
            self.ui.avatarPersonalityCombo.addItem(name, name)
        self.ui.avatarBox.setVisible(False)

    def setup_connections(self):
        spin, slider = self.ui.temperatureSpin, self.ui.temperatureSlider
        spin.valueChanged.connect(lambda v: self._quietly(slider, slider.setValue, round(v * 100)))
        slider.valueChanged.connect(lambda v: self._quietly(spin, spin.setValue, v / 100))
        self.model_combo.activated.connect(self.on_model_chosen)
        self.ui.importButton.clicked.connect(self.import_settings)
        self.ui.exportButton.clicked.connect(self.export_settings)
        self.ui.ttsCheck.toggled.connect(self.on_tts_toggled)
        self.ui.ttsEngineCombo.activated.connect(self.on_engine_chosen)
        self.ui.ttsLanguageCombo.activated.connect(lambda _: self._fill_voices())
        self.ui.ttsVoiceCombo.activated.connect(self.on_voice_chosen)
        self.ui.ttsTestButton.clicked.connect(self.test_voice)
        self.ui.memoryButton.clicked.connect(self.open_memory_folder)
        self.ui.personalityCombo.activated.connect(self.on_personality_chosen)
        for check, group in self._use_checks():
            check.toggled.connect(lambda on, g=group: self.on_use_toggled(g, on))
        self.ui.savePersonalityButton.clicked.connect(self.save_personality)
        self.ui.updatePersonalityButton.clicked.connect(self.update_personality)
        self.workflow_combo.activated.connect(self.on_workflow_chosen)
        self.ui.avatarCheck.toggled.connect(self.on_avatar_toggled)
        for check in (self.ui.ttsCheck, self.ui.avatarCheck):      # they open sections of the window
            check.toggled.connect(lambda _: QTimer.singleShot(0, self._fit_to_screen))
        self.ui.avatarCheckButton.clicked.connect(self.check_avatar)

    @staticmethod
    def _quietly(widget, setter, value):
        widget.blockSignals(True)
        setter(value)
        widget.blockSignals(False)

    # ------------------------------------------------------------------
    # Showing and reading the settings
    # ------------------------------------------------------------------

    def open_for(self, title: str, settings: dict, is_new: bool):
        """Fill the window with `settings` and show it."""
        self._set_editing(None)
        self.is_new = is_new
        self.setWindowTitle("New Chat" if is_new else f"Chat Settings: {title}")
        self.ui.titleEdit.setText(title)
        self.show_settings(settings)
        self._fill_personalities(None if is_new else self.chat_main.personality_name,
                                 () if is_new else self.chat_main.skipped)
        self._show_memory()
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._fit_to_screen)     # sizes are only known once it's shown

    def open_for_personality(self, name: str):
        """Edit a saved personality; Save goes to ChatMain.save_personality."""
        saved = personalities.load(name)
        if saved is None:
            return
        self._set_editing(name)
        self.is_new = False
        self.setWindowTitle(f"Personality: {name}")
        self.ui.titleEdit.setText(name)
        self.show_settings(saved)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._fit_to_screen)

    def _set_editing(self, name):
        """Show everything for a chat, or only what a personality holds."""
        self.editing = name
        ui = self.ui
        for widget in (ui.groupPersonality, ui.groupMemory, ui.groupPictures, ui.label_web, ui.webCombo, ui.label_think,
                       ui.thinkCheck, ui.label_fileTools, ui.fileToolsCheck, ui.label_runTools, ui.runToolsCheck,
                       ui.label_reaTools, ui.reaToolsCheck, ui.label_ubuntuFilesTools, ui.ubuntuFilesToolsCheck,
                       ui.label_ubuntuRunTools, ui.ubuntuRunToolsCheck):
            widget.setVisible(name is None)
        ui.label_title.setText("Chat Title:" if name is None else "Personality:")
        ui.titleEdit.setReadOnly(name is not None)
        ui.titleEdit.setToolTip("" if name is None else "Rename it in the link graph")
        ui.label_name.setVisible(name is None)          # its name is the character's name
        ui.characterEdit.setVisible(name is None)

    def _fit_to_screen(self):
        """Tall enough to show everything, if the screen has the room."""
        self.ui.layout().activate()         # sections may have just been hidden or shown
        need = self.ui.minimumSizeHint().expandedTo(self.ui.sizeHint())
        room = self.screen().availableGeometry().height() - 60
        bar = self.centralWidget().verticalScrollBar().sizeHint().width()
        self.resize(max(self.width(), need.width() + bar), min(need.height(), room))

    def show_settings(self, settings: dict):
        s = chat_settings.normalize(settings)
        self.ui.characterEdit.setText(s["Character"])
        self.ui.pretextEdit.setPlainText(s["Pretext"])
        self.ui.temperatureSpin.setValue(s["Temperature"])
        self.ui.temperatureSlider.setValue(round(s["Temperature"] * 100))
        self.ui.webCombo.setCurrentIndex(max(0, self.ui.webCombo.findData(s["Web Search"])))
        self.ui.thinkCheck.setChecked(s["Think"])
        for check, key in self._tool_checks():
            check.setChecked(s[key])
        self.ui.memoryCheck.setChecked(s["Memory"])
        self.model = s["Model"]
        self.refresh_models()
        self.image_workflow = s["Image Workflow"]
        self.ui.imageModeCombo.setCurrentIndex(max(0, self.ui.imageModeCombo.findData(s["Image Mode"])))
        self._fill_workflows()
        self.tts_voice = s["TTS Voice"]
        self.ui.ttsSpeedSpin.setValue(s["TTS Speed"])
        self.ui.ttsAutoCheck.setChecked(s["TTS Auto Play"])
        self.ui.ttsTranslateCheck.setChecked(s["TTS Translate"])
        self._tts_engine = s["TTS Engine"]
        self.ui.ttsCheck.blockSignals(True)
        self.ui.ttsCheck.setChecked(s["TTS"])
        self.ui.ttsCheck.blockSignals(False)
        self.on_tts_toggled(s["TTS"])
        self.ui.avatarPersonalityCombo.setCurrentIndex(
            max(0, self.ui.avatarPersonalityCombo.findData(s["Avatar Personality"])))
        self.ui.avatarNotesEdit.setPlainText(s["Avatar Notes"])
        self.ui.avatarStatusLabel.setText("")
        self.ui.avatarCheck.blockSignals(True)
        self.ui.avatarCheck.setChecked(s["Avatar"])
        self.ui.avatarCheck.blockSignals(False)
        self._show_avatar(s["Avatar"])

    def title(self) -> str:
        return self.ui.titleEdit.text().strip() or "New Chat"

    def settings(self) -> dict:
        return chat_settings.normalize({
            "Character": self.ui.characterEdit.text().strip(),
            "Pretext": self.ui.pretextEdit.toPlainText().strip(),
            "Model": self.model,
            "Temperature": self.ui.temperatureSpin.value(),
            "Think": self.ui.thinkCheck.isChecked(),
            "Web Search": self.ui.webCombo.currentData(),
            "Image Workflow": self.image_workflow,
            "Image Mode": self.ui.imageModeCombo.currentData(),
            **{key: check.isChecked() for check, key in self._tool_checks()},
            "Memory": self.ui.memoryCheck.isChecked(),
            "TTS": self.ui.ttsCheck.isChecked(),
            "TTS Engine": self.tts_engine(),
            "TTS Voice": self.tts_voice,
            "TTS Speed": self.ui.ttsSpeedSpin.value(),
            "TTS Auto Play": self.ui.ttsAutoCheck.isChecked(),
            "TTS Translate": self.ui.ttsTranslateCheck.isChecked(),
            "Avatar": self.ui.avatarCheck.isChecked(),
            "Avatar Personality": self.ui.avatarPersonalityCombo.currentData(),
            "Avatar Notes": self.ui.avatarNotesEdit.toPlainText(),
        })

    # ------------------------------------------------------------------
    # Model list (shared with the main window's quick picker)
    # ------------------------------------------------------------------

    def refresh_models(self):
        main = self.chat_main
        main.refresh_models()
        combo = self.model_combo
        combo.blockSignals(True)
        combo.clear()
        if self.editing:
            combo.addItem("Each chat picks its own", "")
        for mid, m in main.models.items():
            combo.addItem(main.model_label(m), mid)
        if self.model and self.model not in main.models:
            combo.addItem(f"   {self.model} (not on the server)", self.model)
        combo.setCurrentIndex(max(0, combo.findData(self.model)))
        combo.blockSignals(False)
        if not self.model and combo.count() and not self.editing:
            self.model = combo.currentData()
        self._update_capabilities()

    def on_model_chosen(self, index):
        chosen = self.model_combo.itemData(index)
        self.model = chosen if self.editing else chosen or self.model
        self._update_capabilities()

    def on_main_model_changed(self, model):
        """The quick picker in the main window changed this chat's model."""
        if self.isVisible() and not self.is_new and not self.editing:
            self.model = model
            self.refresh_models()

    def _update_capabilities(self):
        caps = self.chat_main.models.get(self.model, {}).get("capabilities")
        can_think = not caps or "thinking" in caps
        can_call = not caps or "tools" in caps
        self.ui.thinkCheck.setEnabled(can_think)
        self.ui.thinkCheck.setToolTip("" if can_think else f"{self.model} can't think; this setting is ignored")
        for check, _ in self._tool_checks():
            if not hasattr(check, "default_tip"):
                check.default_tip = check.toolTip()
            check.setEnabled(can_call)
            check.setToolTip(check.default_tip if can_call else f"{self.model} can't call tools; pick a model marked 🔧")

    def _tool_checks(self):
        ui = self.ui
        return [(ui.fileToolsCheck, chat_settings.TOOL_KEYS["files"]),
                (ui.gitToolsCheck, chat_settings.TOOL_KEYS["git"]),
                (ui.runToolsCheck, chat_settings.TOOL_KEYS["run"]),
                (ui.reaToolsCheck, chat_settings.SERVER_TOOL_KEYS["rea"]),
                (ui.ubuntuFilesToolsCheck, chat_settings.SERVER_TOOL_KEYS["ubuntu_files"]),
                (ui.ubuntuRunToolsCheck, chat_settings.SERVER_TOOL_KEYS["ubuntu_run"])]

    # ------------------------------------------------------------------
    # Personality
    # ------------------------------------------------------------------

    def personality(self):
        """The saved personality this chat talks as, or None for its own settings."""
        return self.ui.personalityCombo.currentData()

    def skipped(self) -> list:
        """The groups of the personality's settings this chat keeps to itself."""
        return [group for check, group in self._use_checks() if not check.isChecked()]

    def _use_checks(self):
        return ((self.ui.useVoiceCheck, "Voice"), (self.ui.useAvatarCheck, "3D model"),
                (self.ui.useModelCheck, "LLM model"))

    def _own_values(self):
        return chat_settings.defaults() if self.is_new else self.chat_main.own_settings

    def _fill_personalities(self, chosen, skipped=()):
        for check, group in self._use_checks():
            self._quietly(check, check.setChecked, group not in skipped)
        combo = self.ui.personalityCombo
        combo.clear()
        combo.addItem("This chat's own settings", None)
        for name in self.chat_main.links["Personalities"]:
            combo.addItem(name, name)
        combo.setCurrentIndex(max(0, combo.findData(chosen)))
        self._show_personality_state()

    def _show_personality_state(self):
        name = self.personality()
        self.ui.updatePersonalityButton.setEnabled(name is not None)
        self.ui.personalityNote.setVisible(name is not None)
        self.ui.characterEdit.setEnabled(name is None)
        self.ui.characterEdit.setToolTip("" if name is None else "The personality's name; rename it in the link graph")
        for check, _group in self._use_checks():
            check.setVisible(name is not None)

    def on_use_toggled(self, group, on):
        """Show the personality's values for this group, or this chat's own."""
        name = self.personality()
        saved = personalities.load(name) if name else None
        if saved:
            source = saved if on else self._own_values()
            keys = [k for k in personalities.OPTIONAL[group] if k != "Model" or source[k]]
            self.show_settings({**self.settings(), **{k: source[k] for k in keys}})

    def on_personality_chosen(self, _index):
        name = self.personality()
        self._show_personality_state()
        current = self.settings()
        if name:
            saved = personalities.load(name)
            if saved:
                self.show_settings(personalities.resolve(current, saved, skipped=self.skipped()))
        else:
            own = self._own_values()
            self.show_settings({**current, **{k: own[k] for k in personalities.PERSONALITY_KEYS}})

    def save_personality(self):
        name, ok = QInputDialog.getText(self, "Save as personality", "Name",
                                        text=self.ui.characterEdit.text().strip())
        name = chat_links.clean_name(name)
        if not ok or not name:
            return
        links = self.chat_main.links
        if name in links["Personalities"] and QMessageBox.question(
                self, "Save as personality", f"Replace the personality {name}?") != QMessageBox.Yes:
            return
        personalities.save(name, self.settings())
        chat_links.add_hub(links, chat_links.PERSONALITY, name)
        self.chat_main.links_updated()
        self._fill_personalities(name)

    def update_personality(self):
        name = self.personality()
        if name:
            self.chat_main.save_personality(name, self.settings(), self.skipped())   # not this chat's own voice, say

    def _memory_folder(self):
        """The memory folder of the chat being edited; a new chat doesn't have one until it's saved."""
        return None if self.is_new else self.chat_main.memory_folder()

    def _show_memory(self):
        folder = self._memory_folder()
        self.ui.memoryButton.setEnabled(folder is not None)
        if folder is None:
            self.ui.memoryLabel.setText("A new chat gets its memory folder when you save it.")
            return
        names = [p.name for p in memory.memory_files(folder)]
        self.ui.memoryLabel.setText(", ".join(names) if names else "No notes yet")
        self.ui.memoryLabel.setToolTip(str(folder))

    def open_memory_folder(self):
        folder = self._memory_folder()
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            self._show_memory()

    # ------------------------------------------------------------------
    # Pictures (ComfyUI workflows on the server)
    # ------------------------------------------------------------------

    def refresh_workflows(self):
        """Ask the server for its workflows (each time the list is opened, so new files show up)."""
        try:
            info = self.chat_main.client.list_workflows()
        except RuntimeError as e:
            self.ui.imageStatusLabel.setText(f"{e}. The workflow you pick is kept and used once the server has it.")
            self._fill_workflows()
            return
        self.workflow_list = info.get("data") or []
        notes = []
        if not info.get("comfyui", True):
            notes.append(info.get("error") or "ComfyUI isn't running on the server")
        if not self.workflow_list:
            notes.append(f"The server has no workflows yet; put ComfyUI 'Export (API)' files in {info.get('folder', 'Server/ollama/workflows')}")
        self.ui.imageStatusLabel.setText(". ".join(notes))
        self._fill_workflows()

    def _fill_workflows(self):
        combo = self.workflow_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Off", "")
        for w in self.workflow_list or []:
            combo.addItem(w["name"] + ("" if w.get("usable", True) else "  (can't be used yet)"), w["id"])
        if self.image_workflow and combo.findData(self.image_workflow) < 0:
            suffix = "" if self.workflow_list is None else " (not on the server)"
            combo.addItem(self.image_workflow + suffix, self.image_workflow)
        combo.setCurrentIndex(max(0, combo.findData(self.image_workflow)))
        combo.blockSignals(False)
        self._show_workflow_state()

    def on_workflow_chosen(self, index):
        self.image_workflow = self.workflow_combo.itemData(index) or ""
        self._show_workflow_state()

    def _show_workflow_state(self):
        self.ui.imageModeCombo.setEnabled(bool(self.image_workflow))
        info = next((w for w in self.workflow_list or [] if w["id"] == self.image_workflow), None)
        if info and not info.get("usable", True):
            self.ui.imageStatusLabel.setText(f"{info['id']}: {info.get('error', '')}")

    # ------------------------------------------------------------------
    # 3D model (avatar.py)
    # ------------------------------------------------------------------

    def on_avatar_toggled(self, on):
        """She speaks every reply, so turning her on turns the voice on (and auto play)."""
        if on:
            self.ui.ttsAutoCheck.setChecked(True)
            self.ui.ttsCheck.setChecked(True)
            self.check_avatar()
        self._show_avatar(on)

    def _show_avatar(self, on):
        self.ui.avatarBox.setVisible(on)
        self._update_watching()
        # The voice can't be turned off while she's on; it comes back with her off.
        self.ui.ttsCheck.setEnabled(not on)
        self.ui.ttsCheck.setToolTip("The 3D model speaks every reply, so the voice stays on while she's on"
                                    if on else "Off: no Play buttons in the chat and no voice settings here")
        self.adjustSize()

    def check_avatar(self):
        link = self.chat_main.avatar_link
        self.ui.avatarStatusLabel.setText(f"Looking for the 3D model at {link.url}…")
        self.ui.avatarStatusLabel.repaint()
        self._show_avatar_status(link.catalog(max_age=0))

    def _show_avatar_status(self, info):
        link = self.chat_main.avatar_link
        if info is None:
            self.ui.avatarStatusLabel.setText(
                f"The 3D model isn't running at {link.url}. Start ChatUIAvatar on this PC (or press Play in Unity); "
                "this setting is kept and she joins in once she's running.")
            return
        faces = len(info.get("expressions") or []) + len(info.get("specialExpressions") or [])
        self.ui.avatarStatusLabel.setText(
            f"✓ Connected: {faces} faces, {len(info.get('actions') or [])} moves, "
            f"{len(info.get('motionSets') or [])} presets; she's {str(info.get('posture', '')).lower() or 'here'}.")

    # ------------------------------------------------------------------
    # Voice (text to speech)
    # ------------------------------------------------------------------

    def _update_watching(self):
        """Keep an eye on the 3D model and the voices only while they're on screen."""
        shown = self.isVisible()
        self.chat_main.avatar_link.watch("settings", shown and self.ui.avatarCheck.isChecked())
        if shown and self.ui.ttsCheck.isChecked():
            self._voice_timer.start()
        else:
            self._voice_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_watching()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._update_watching()

    def _check_voices(self):
        """Ask the server again, off the GUI thread; only GPT-SoVITS can change once the list is in."""
        if self._voices_checking:
            return
        self._voices_checking = True
        engine = "gpt-sovits" if self.voice_list else ""
        base_url = self.chat_main.client.base_url

        def run():
            try:
                self.voices_checked.emit(voice.list_voices(base_url, engine), engine)
            except RuntimeError as e:    # the server's gone (this window's error is kept), or we're closing
                try:
                    self.voices_checked.emit(None, str(e))
                except RuntimeError:
                    pass

        threading.Thread(target=run, daemon=True).start()

    def _on_voices_checked(self, data, engine):
        self._voices_checking = False
        if data is None or not self.isVisible() or not self.ui.ttsCheck.isChecked():
            return
        if self.ui.ttsEngineCombo.view().isVisible() or self.ui.ttsLanguageCombo.view().isVisible() \
                or self.ui.ttsVoiceCombo.view().isVisible():
            return                       # they're choosing: the next check gets it
        if engine and self.voice_list:
            others = [v for v in self.voice_list.get("voices", []) if v.get("engine") != engine]
            data = {**self.voice_list, "engines": data.get("engines"), "voices": others + data.get("voices", [])}
        if self.voice_list and data.get("engines") == self.voice_list.get("engines") \
                and data.get("voices") == self.voice_list.get("voices"):
            return
        self.voice_list = data
        self._fill_engines()

    def on_tts_toggled(self, on):
        """Off hides every voice setting; on fetches the voices from the server the first time."""
        self.ui.ttsBox.setVisible(on)
        self._update_watching()
        if on:
            if self.voice_list is None:
                self.load_voices()
            else:
                self._fill_engines()
        else:
            self.preview.stop()
        self.adjustSize()

    def load_voices(self):
        self.ui.ttsStatusLabel.setText("Getting the voices from the server…")
        try:
            self.voice_list = voice.list_voices(self.chat_main.client.base_url)
            errors = self.voice_list.get("errors") or {}
            self.ui.ttsStatusLabel.setText("; ".join(errors.values()))
        except RuntimeError as e:
            self.voice_list = None
            self.ui.ttsStatusLabel.setText(f"{e}. The voice you pick is kept and used once the server has it.")
        self._fill_engines()

    def tts_engine(self) -> str:
        return self.ui.ttsEngineCombo.currentData() or self._tts_engine

    def _voices(self, engine=None):
        engine = engine or self.tts_engine()
        return [v for v in (self.voice_list or {}).get("voices", []) if v.get("engine") == engine]

    def _fill_engines(self):
        combo = self.ui.ttsEngineCombo
        engines = (self.voice_list or {}).get("engines") or [{"id": e, "name": e, "available": True}
                                                            for e in chat_settings.TTS_ENGINES]
        self.engine_info = {e["id"]: e for e in engines}
        combo.clear()
        for e in engines:
            combo.addItem(e["name"] + ("" if e.get("available") else "  (not available on the server)"), e["id"])
        if combo.findData(self._tts_engine) < 0:
            combo.addItem(f"{self._tts_engine} (not on the server)", self._tts_engine)
        combo.setCurrentIndex(max(0, combo.findData(self._tts_engine)))
        self._fill_languages()

    def _show_engine_info(self):
        """Say how to install an engine the server lacks; grey out Speed for engines without it."""
        info = self.engine_info.get(self.tts_engine(), {})
        speed = info.get("speed", True)
        self.ui.ttsSpeedSpin.setEnabled(speed)
        self.ui.ttsSpeedSpin.setToolTip("" if speed else "This engine always speaks at its own pace")
        if info and not info.get("available", True) and info.get("install", "").startswith("start "):
            self.ui.ttsStatusLabel.setText(f"Not running on the server yet: {info['install']}.")
        elif info and not info.get("available", True):
            self.ui.ttsStatusLabel.setText(f"Not installed on the server yet. On the server, in Server/ollama: "
                                           f".venv/bin/{info.get('install', 'pip install ...')}, then restart it.")
        elif self.voice_list is not None:
            self.ui.ttsStatusLabel.setText("; ".join((self.voice_list.get("errors") or {}).values()))

    def _fill_languages(self):
        self._tts_engine = self.tts_engine()
        self._show_engine_info()
        voices = self._voices()
        current = next((v["language"] for v in voices if v["id"] == self.tts_voice), None)
        if current is None and voices:
            # A new chat, or a voice from the other engine: start in the language of this PC.
            from PySide6.QtCore import QLocale
            mine = QLocale.system().name().replace("_", "-")
            langs = {v["language"] for v in voices}
            current = next((x for x in (mine, mine.split("-")[0], "en-US", "en") if x in langs), "")
        combo = self.ui.ttsLanguageCombo
        combo.clear()
        combo.addItem("All languages", "")
        for lang in sorted({v["language"] for v in voices}):
            combo.addItem(lang, lang)
        combo.setCurrentIndex(max(0, combo.findData(current or "")))
        self._fill_voices()

    def _fill_voices(self):
        """Voices of the chosen engine and language. "Server default" is only offered
        under All languages; picking a language takes its first voice unless the
        chosen voice is one of them."""
        lang = self.ui.ttsLanguageCombo.currentData() or ""
        voices = [v for v in self._voices() if not lang or v["language"] == lang]
        combo = self.ui.ttsVoiceCombo
        combo.clear()
        if not lang or not voices:
            combo.addItem("Server default", "")
        for v in voices:
            combo.addItem(v["name"], v["id"])
        known = any(v["id"] == self.tts_voice for v in self._voices())
        if self.tts_voice and not known and self._other_engine(self.tts_voice):
            self.tts_voice = ""          # left over from another engine: this one can't use it
        if self.tts_voice and not known:
            combo.addItem(f"{self.tts_voice} (not on the server)", self.tts_voice)
        index = combo.findData(self.tts_voice)
        if index < 0:
            index = 0
            self.tts_voice = combo.itemData(0) or ""
        combo.setCurrentIndex(index)

    def _other_engine(self, voice_id) -> bool:
        """True if the server lists `voice_id` under a different engine than the chosen one."""
        engine = self.tts_engine()
        return any(v["id"] == voice_id and v.get("engine") != engine
                   for v in (self.voice_list or {}).get("voices", []))

    def on_engine_chosen(self, _index):
        """A voice belongs to one engine, so switching engines drops it unless the new one has it."""
        if not any(v["id"] == self.tts_voice for v in self._voices()):
            self.tts_voice = ""
        self._fill_languages()

    def on_voice_chosen(self, index):
        self.tts_voice = self.ui.ttsVoiceCombo.itemData(index) or ""

    def test_voice(self):
        if self.preview.state_of("test") in ("playing", "loading"):
            self.preview.stop()
            return
        lang = next((v["language"] for v in self._voices() if v["id"] == self.tts_voice), "") or \
            (self.ui.ttsLanguageCombo.currentData() or "")
        name = self.ui.characterEdit.text().strip() or "Bot"
        self.ui.ttsStatusLabel.setText("")
        self.preview.toggle("test", voice_sample(lang, name), self.tts_engine(), self.tts_voice,
                            self.ui.ttsSpeedSpin.value())

    def _show_preview_state(self):
        state = self.preview.state_of("test")
        self.ui.ttsTestButton.setText({"loading": "⏳", "playing": "■ Stop"}.get(state, "▶ Test"))

    def closeEvent(self, event):
        self.preview.stop()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Import / export
    # ------------------------------------------------------------------

    def import_settings(self):
        path = ask_import_path(self)
        if path:
            settings = read_import(self, path)
            if settings is not None:
                self.show_settings(settings)

    def export_settings(self):
        export_to_file(self, self.settings(), self.title())


VOICE_SAMPLES = {
    "zh-HK": "你好，我係{name}。我把聲聽落係咁樣。",
    "zh": "你好，我是{name}。这就是我的声音。",
    "ja": "こんにちは、{name}です。これが私の声です。",
    "ko": "안녕하세요, 저는 {name}입니다. 제 목소리는 이렇습니다.",
    "fr": "Bonjour, je suis {name}. Voici ma voix.",
    "de": "Hallo, ich bin {name}. So klingt meine Stimme.",
    "es": "Hola, soy {name}. Así suena mi voz.",
}


def voice_sample(language: str, name: str) -> str:
    """A short line in the voice's language for the ▶ Test button."""
    text = VOICE_SAMPLES.get(language) or VOICE_SAMPLES.get(language.split("-")[0]) or \
        "Hello, I'm {name}. This is how I sound."
    return text.format(name=name)


def _last_folder() -> Path:
    """Where the last import or export went, so the next dialog opens there."""
    folder = Path(str(QSettings().value("settings/lastFolder", "") or Path.home()))
    return folder if folder.is_dir() else Path.home()


def _remember_folder(path):
    QSettings().setValue("settings/lastFolder", str(Path(path).parent))


def ask_import_path(parent) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, "Import chat settings", str(_last_folder()), "JSON Files (*.json)")
    if path:
        _remember_folder(path)
    return path


def read_import(parent, path):
    """Settings from `path`, or None after telling the user why not."""
    try:
        return chat_settings.import_file(path)
    except ValueError as e:
        QMessageBox.warning(parent, "Can't import settings", f"{Path(path).name}: {e}")
        return None


def export_to_file(parent, settings: dict, chat_name: str):
    """Save `settings` as {chat name}.json. The file dialog (Explorer on Windows)
    lets you rename the file and pick the folder; the next export opens in the
    folder you picked."""
    default = _last_folder() / f"{safe_file_name(chat_name)}.json"
    path, _ = QFileDialog.getSaveFileName(parent, "Export chat settings", str(default), "JSON Files (*.json)")
    if not path:
        return
    if Path(path).suffix.lower() != ".json":
        path += ".json"
    try:
        chat_settings.export_file(settings, path)
    except OSError as e:
        QMessageBox.warning(parent, "Can't export settings", str(e))
        return
    _remember_folder(path)
    QMessageBox.information(parent, "Settings exported", f"Saved to {path}")
