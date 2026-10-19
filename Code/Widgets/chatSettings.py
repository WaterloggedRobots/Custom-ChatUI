"""The settings window of one chat: title, character, model and options.

It edits a copy of the chat's settings (chat_settings.py) and hands them back
to main.py on Save; ChatMain applies and saves them in the chat file. The
model list is the same one the main window's quick model picker shows.
"""

import chat_settings
import paths
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

    def setup_connections(self):
        spin, slider = self.ui.temperatureSpin, self.ui.temperatureSlider
        spin.valueChanged.connect(lambda v: self._quietly(slider, slider.setValue, round(v * 100)))
        slider.valueChanged.connect(lambda v: self._quietly(spin, spin.setValue, v / 100))
        self.model_combo.activated.connect(self.on_model_chosen)
        self.ui.importButton.clicked.connect(self.import_settings)
        self.ui.exportButton.clicked.connect(self.export_settings)

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
