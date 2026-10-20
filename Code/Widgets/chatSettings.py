"""The settings window of one chat: title, character, model, options and voice.

It edits a copy of the chat's settings (chat_settings.py) and hands them back
to main.py on Save; ChatMain applies and saves them in the chat file. The
model list is the same one the main window's quick model picker shows. The
voice lists come from the server (GET /v1/audio/voices) the first time text
to speech is switched on here.
"""

import chat_settings
import paths
import voice
from Widgets.chatMain import LiveComboBox, WEB_LABELS, safe_file_name

from PySide6.QtWidgets import QMainWindow, QFileDialog, QComboBox, QMessageBox
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QSettings
from pathlib import Path


class ChatSettings(QMainWindow):
    def __init__(self, chat_main):
        super().__init__()
        self.chat_main = chat_main
        self.is_new = True             # True: Save creates a chat; False: it updates the open one
        self.model = ""
        self.tts_voice = ""            # the chosen voice id; kept even if the server doesn't list it
        self._tts_engine = chat_settings.DEFAULTS["TTS Engine"]
        self.engine_info = {}          # engine id -> {"name", "available", "install", "speed"}
        self.voice_list = None         # from the server: {"engines": [...], "voices": [...]}
        self.preview = voice.Speaker(lambda: chat_main.client.base_url, paths.AUDIO_DIR, self)
        self.preview.changed.connect(self._show_preview_state)
        self.preview.failed.connect(lambda err: self.ui.ttsStatusLabel.setText(err))
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
        self.setCentralWidget(self.ui)
        self.resize(self.ui.size())

        self.model_combo = LiveComboBox(self.refresh_models)
        self.model_combo.setToolTip(self.chat_main.model_combo.toolTip())
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.ui.modelLayout.addWidget(self.model_combo, 1)

        for mode in chat_settings.WEB_MODES:
            self.ui.webCombo.addItem(WEB_LABELS[mode], mode)

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

    def setup_connections(self):
        spin, slider = self.ui.temperatureSpin, self.ui.temperatureSlider
        spin.valueChanged.connect(lambda v: self._quietly(slider, slider.setValue, round(v * 100)))
        slider.valueChanged.connect(lambda v: self._quietly(spin, spin.setValue, v / 100))
        self.model_combo.activated.connect(self.on_model_chosen)
        self.ui.importButton.clicked.connect(self.import_settings)
        self.ui.exportButton.clicked.connect(self.export_settings)
        self.ui.ttsCheck.toggled.connect(self.on_tts_toggled)
        self.ui.ttsEngineCombo.activated.connect(lambda _: self._fill_languages())
        self.ui.ttsLanguageCombo.activated.connect(lambda _: self._fill_voices())
        self.ui.ttsVoiceCombo.activated.connect(self.on_voice_chosen)
        self.ui.ttsTestButton.clicked.connect(self.test_voice)

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
        self.is_new = is_new
        self.setWindowTitle("New Chat" if is_new else f"Chat Settings: {title}")
        self.ui.titleEdit.setText(title)
        self.show_settings(settings)
        self.show()
        self.raise_()
        self.activateWindow()

    def show_settings(self, settings: dict):
        s = chat_settings.normalize(settings)
        self.ui.characterEdit.setText(s["Character"])
        self.ui.pretextEdit.setPlainText(s["Pretext"])
        self.ui.temperatureSpin.setValue(s["Temperature"])
        self.ui.temperatureSlider.setValue(round(s["Temperature"] * 100))
        self.ui.webCombo.setCurrentIndex(max(0, self.ui.webCombo.findData(s["Web Search"])))
        self.ui.thinkCheck.setChecked(s["Think"])
        self.ui.toolsCheck.setChecked(s["Tools"])
        self.model = s["Model"]
        self.refresh_models()
        self.tts_voice = s["TTS Voice"]
        self.ui.ttsSpeedSpin.setValue(s["TTS Speed"])
        self.ui.ttsAutoCheck.setChecked(s["TTS Auto Play"])
        self.ui.ttsTranslateCheck.setChecked(s["TTS Translate"])
        self._tts_engine = s["TTS Engine"]
        self.ui.ttsCheck.blockSignals(True)
        self.ui.ttsCheck.setChecked(s["TTS"])
        self.ui.ttsCheck.blockSignals(False)
        self.on_tts_toggled(s["TTS"])

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
            "Tools": self.ui.toolsCheck.isChecked(),
            "TTS": self.ui.ttsCheck.isChecked(),
            "TTS Engine": self.tts_engine(),
            "TTS Voice": self.tts_voice,
            "TTS Speed": self.ui.ttsSpeedSpin.value(),
            "TTS Auto Play": self.ui.ttsAutoCheck.isChecked(),
            "TTS Translate": self.ui.ttsTranslateCheck.isChecked(),
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
        for mid, m in main.models.items():
            combo.addItem(main.model_label(m), mid)
        if self.model and self.model not in main.models:
            combo.addItem(f"   {self.model} (not on the server)", self.model)
        combo.setCurrentIndex(max(0, combo.findData(self.model)))
        combo.blockSignals(False)
        if not self.model and combo.count():
            self.model = combo.currentData()
        self._update_capabilities()

    def on_model_chosen(self, index):
        self.model = self.model_combo.itemData(index) or self.model
        self._update_capabilities()

    def on_main_model_changed(self, model):
        """The quick picker in the main window changed this chat's model."""
        if self.isVisible() and not self.is_new:
            self.model = model
            self.refresh_models()

    def _update_capabilities(self):
        caps = self.chat_main.models.get(self.model, {}).get("capabilities")
        can_think = not caps or "thinking" in caps
        can_call = not caps or "tools" in caps
        self.ui.thinkCheck.setEnabled(can_think)
        self.ui.thinkCheck.setToolTip("" if can_think else f"{self.model} can't think; this setting is ignored")
        self.ui.toolsCheck.setEnabled(can_call)
        self.ui.toolsCheck.setToolTip("" if can_call else f"{self.model} can't call tools; pick a model marked 🔧")

    # ------------------------------------------------------------------
    # Voice (text to speech)
    # ------------------------------------------------------------------

    def on_tts_toggled(self, on):
        """Off hides every voice setting; on fetches the voices from the server the first time."""
        self.ui.ttsBox.setVisible(on)
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
            combo.addItem(e["name"] + ("" if e.get("available") else "  (not installed on the server)"), e["id"])
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
        if info and not info.get("available", True):
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
        if self.tts_voice and not known:
            combo.addItem(f"{self.tts_voice} (not on the server)", self.tts_voice)
        index = combo.findData(self.tts_voice)
        if index < 0:
            index = 0
            self.tts_voice = combo.itemData(0) or ""
        combo.setCurrentIndex(index)

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
