from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QMainWindow, QFileDialog, QGraphicsScene, QGraphicsPixmapItem, QLineEdit
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
import json
from pathlib import Path

class ChatSettings(QMainWindow):
    def __init__(self, pd, fd):
        super().__init__()
        self.ui = None
        self.directoryParent = pd
        self.directoryDefault = fd
        self.chatPath = ""
        self.botSettingPath = ""
        self.scene = QGraphicsScene()
        self.chatHist =[]
        self.payload=[]
        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(self.directoryDefault+r"\UI\Chat Settings.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Chat Settings")

            self.setup_opacity_controls()

        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        self.ui.pushButton.clicked.connect(self.load_bot_dir)

    def load_bot_dir(self):
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Select Folder",  # Dialog title
            str(Path(self.directoryParent) / "Save" / "Bot"),  # Start directory (empty = current dir)
            QFileDialog.ShowDirsOnly  # Only show directories
        )
        self.botSettingPath = folder_path
        self.load_bot(folder_path)

    def load_bot(self,folder_path):
        # Select a folder (directory) only

        if folder_path:  # Check if user didn't cancel
            print(f"Selected folder: {folder_path}")

            self.ui.lineEdit_2.setText(folder_path)
            botJsonPath = folder_path + "/Bot Description.json"
            try:
                botJson = self.read_json_file(botJsonPath)
                self.ui.textBrowser_2.setText(botJson["Name"])
                self.ui.textBrowser.setText(botJson["Description"])
                img =QPixmap(str(folder_path + "/Portrait.png"))
                self.display_image(img)

            except Exception as e:
                print("Failed to load bot settings: ", e)

    def display_image(self, pixmap):
        """Simple approach using fitInView()"""
        if pixmap.isNull():
            return

        # Store the pixmap
        self.current_pixmap = pixmap

        # Clear the scene
        self.scene.clear()

        # Create and add pixmap item
        self.current_pixmap_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self.current_pixmap_item)

        # Store the item for later resizing
        self.current_pixmap_item = self.current_pixmap_item
        # Fit image to view (initial scaling)
        self.ui.graphicsView.fitInView(self.current_pixmap_item, Qt.KeepAspectRatio)
        self.ui.graphicsView.setScene(self.scene)
        self.ui.graphicsView.update()

    def resizeEvent(self, event):
        """Update image scaling on window resize"""
        super().resizeEvent(event)

        try:
            # Multiple safety checks
            if (hasattr(self, 'current_pixmap_item') and
                    self.current_pixmap_item is not None and
                    hasattr(self, 'scene') and
                    self.scene is not None and
                    self.current_pixmap_item in self.scene.items()):

                 self.ui.graphicsView.fitInView(self.current_pixmap_item, Qt.KeepAspectRatio)
        except Exception as e:
            pass

    def update_lastChat(self, path):
        logPath = Path(self.directoryParent) / "Save" / ".temp.json"
        log = self.read_json_file(logPath)
        log_new = log.copy()
        log_new["LastChat"] = path
        print("Updating Last Chat Logs")
        with open(logPath, "w", encoding="utf-8") as f:
            json.dump(log_new, f, indent=4)
        print(log_new)

    def read_json_file(self, file_path):
        """Read a JSON file and return its contents"""
        try:
            # Convert to Path object for better handling
            json_path = Path(file_path)

            if not json_path.exists():
                print(f"❌ File not found in Chat Settings: {file_path}")
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

    def del_json_file(self,file_path: str) -> bool:
        """Simple, safe deletion for your chat app"""
        try:
            path = Path(file_path)
            if path.exists() and path.suffix.lower() == '.json':
                path.unlink()
                return True
            return False
        except Exception as e:
            print(f"Error deleting {file_path}: {e}")
            return False

    def setup_opacity_controls(self):
        spin = self.ui.doubleSpinBox
        slider = self.ui.horizontalSlider

        # SpinBox setup
        spin.setRange(0.0, 1.0)
        spin.setSingleStep(0.01)
        spin.setDecimals(2)

        # Slider works in integers → map 0–100
        slider.setRange(0, 100)
        slider.setSingleStep(1)
        slider.setPageStep(10)

        # Initial sync
        slider.setValue(int(spin.value() * 100))

        # Connections (BLOCK SIGNALS to prevent loops)
        spin.valueChanged.connect(
            lambda v: self._sync_spin_to_slider(v)
        )
        slider.valueChanged.connect(
            lambda v: self._sync_slider_to_spin(v)
        )

    def _sync_spin_to_slider(self, value: float):
        slider = self.ui.horizontalSlider
        slider.blockSignals(True)
        slider.setValue(int(value * 100))
        slider.blockSignals(False)

    def _sync_slider_to_spin(self, value: int):
        spin = self.ui.doubleSpinBox
        spin.blockSignals(True)
        spin.setValue(value / 100.0)
        spin.blockSignals(False)

    def save_settings(self):
        logsPath =Path(self.directoryParent) / "Save" / ".temp.json"
        logs = self.read_json_file(logsPath)

        if True:
            pass
        else:
            return
        chat_name = self.ui.lineEdit.text().strip()
        if chat_name == "":
            chat_name = "New Chat"

        target_base = Path(self.directoryParent) / "Save" / "Chat"
        target_base.mkdir(parents=True, exist_ok=True)

        chat_dir = str(target_base) + '/' + chat_name

        if self.chatPath == chat_dir:
            chat_dir = Path(str(self.chatPath)+".json")

        else:
            if self.chatPath != "":
                self.del_json_file(str(self.chatPath))
                self.chatPath = chat_dir

            if Path(chat_dir).exists():
                chatName = Path(chat_dir).stem
                i=1
                while  Path(chat_dir).exists():
                    chatName_New =chatName + " ("+str(i)+")"
                    chat_dir = str(target_base) + '/' + chatName_New
                    i+=1
            chat_dir = Path(chat_dir+".json")
            chat_dir_str = str(chat_dir.stem)

        print("Saving: ", chat_dir)
        if chat_dir_str in logs["ChatList"] and logs["ChatList"].index(chat_dir_str) != len(logs["ChatList"])-1:
            logs["ChatList"].remove(chat_dir_str)
        if chat_dir_str not in logs["ChatList"]:
            logs["ChatList"].append(chat_dir_str)
        logs["LastChat"]=str(chat_dir)
        with open(logsPath, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=4)
        # -----------------------------
        # Create / overwrite JSON
        # -----------------------------
        json_data = {
            "Name": chat_name,
            "Bot Path": self.botSettingPath,
            "Temperature": self.ui.doubleSpinBox.value(),
            "Model": self.ui.comboBox.currentText(),
            "Chat": self.chatHist,
            "Payload":self.payload
        }

        with open(chat_dir, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4)

        print("save success")

    def loadSettings(self, path):
        self.chatPath = path
        if path == "":
            print("new chat settings")
            self.ui.lineEdit_2.setText("")
            self.ui.textBrowser_2.setText('')
            self.ui.textBrowser.setText('')
            self.ui.doubleSpinBox.setValue(0.7)
            self.ui.comboBox.setCurrentIndex(0)
            self.chatHist =[]
            self.payload = []
            self.scene.clear()
        else:
            print("loading chat settings")
            print("path: ", path)
            settings = self.read_json_file(path)
            if settings is not None:
                self.botSettingPath = settings["Bot Path"]
                self.ui.lineEdit.setText(settings["Name"])
                self.load_bot(self.botSettingPath)
                self.ui.doubleSpinBox.setValue(settings["Temperature"])
                self.ui.comboBox.setCurrentText(settings["Model"])
                self.chatHist = settings["Chat"]
                self.payload = settings["Payload"]