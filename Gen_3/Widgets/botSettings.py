from PySide6.QtWidgets import (
    QMainWindow, QGraphicsScene, QGraphicsPixmapItem, QGraphicsView, QFileDialog
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QUrl, Qt
from PySide6.QtGui import QPixmap, QColor
from pathlib import Path
import os
import json
import shutil

class ImageDropView(QGraphicsView):
    def __init__(self, parent=None, dummy_image=None):
        super().__init__(parent)

        self.setAcceptDrops(True)
        self.botPath = ""
        # Scene
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # Single reusable pixmap item
        self._pixmap_item = QGraphicsPixmapItem()
        self._scene.addItem(self._pixmap_item)

        # Appearance
        self.setFrameShape(QGraphicsView.Box)
        self.setBackgroundBrush(QColor(30, 30, 30))
        self.setAlignment(Qt.AlignCenter)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Load dummy image if provided
        if dummy_image and os.path.exists(dummy_image):
            self.set_image(dummy_image)

    # -------------------------
    # Drag & Drop
    # -------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if any(self._is_image(url) for url in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if self._is_image(url):
                path = url.toLocalFile()
                print("Dropped image:", path)  # ✅ requirement #4
                self.set_image(path)
                break  # only take first image

        event.acceptProposedAction()

    # -------------------------
    # Image handling
    # -------------------------

    def set_image(self, path: str):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return

        self._original_pixmap = pixmap
        self._pixmap_item.setPixmap(pixmap)
        self._update_scale()

    def resizeEvent(self, event):
        self._scene.setSceneRect(self.viewport().rect())
        self._update_scale()
        super().resizeEvent(event)

    def _update_scale(self):
        if not hasattr(self, "_original_pixmap"):
            return

        view_rect = self.viewport().rect()
        self.scene().setSceneRect(view_rect)

        pix = self._original_pixmap
        if pix.isNull():
            return

        # Scale based on height only
        scaleH = view_rect.height() / pix.height()
        scaleW = view_rect.width() / pix.width()
        scale = min(scaleH, scaleW)

        self._pixmap_item.setScale(scale)

        # Reset position
        self._pixmap_item.setPos(0, 0)

        # Center the item in the scene
        self._pixmap_item.setTransformOriginPoint(
            self._pixmap_item.boundingRect().center()
        )
        self._pixmap_item.setPos(
            self.scene().sceneRect().center()
            - self._pixmap_item.boundingRect().center()
        )

    def set_image(self, path: str):
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return

        self.current_image_path = path  # ✅ track image path
        self._original_pixmap = pixmap
        self._pixmap_item.setPixmap(pixmap)
        self._update_scale()

    # -------------------------
    # Helpers
    # -------------------------

    def _is_image(self, url: QUrl):
        return os.path.splitext(url.toLocalFile())[1].lower() in {
            ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"
        }

class BotSettings(QMainWindow):
    def __init__(self, pd ,fd):
        super().__init__()
        self.directoryParent = pd
        self.directoryDefault = fd
        self.path =None
        self.load_ui()

    def load_ui(self):
        ui_path = Path(self.directoryDefault) / "UI" / "Bot Settings.ui"
        ui_file = QFile(str(ui_path))
        print(ui_path.resolve())
        if not ui_file.open(QFile.ReadOnly):
            print(ui_file.errorString())
            return

        loader = QUiLoader()

        loader.registerCustomWidget(ImageDropView)

        self.ui = loader.load(ui_file, self)
        ui_file.close()

        self.setCentralWidget(self.ui)

        dummy = Path(self.directoryDefault) / "Img" / "BotDefault.png"

        gv = self.ui.graphicsView
        print(type(gv))

        gv.setAcceptDrops(True)

        if hasattr(gv, "set_image"):
            gv.set_image(str(dummy))
            #print("Dummy uploaded")

        self.setCentralWidget(self.ui)

        self.path_edit = self.ui.lineEdit
        self.browse_btn = self.ui.pushButton_5
        self.invalidPath = self.ui.label
        self.invalidPathSpacer = self.ui.horizontalLayout_2.itemAt(4)

        self.browse_btn.clicked.connect(self.browse_json)
        self.ui.pushButton_2.clicked.connect(self.save_bot)

        self.path_edit.textChanged.connect(self.on_path_changed)

        self.invalidPath.setMaximumSize(0, 16777215)
        self.invalidPathSpacer.maximumSize=(134, 16777215)

    def is_valid_json_path(path_str: str) -> bool:
        if not path_str:
            return False

        path = Path(path_str)

        return (
                path.exists()
                and path.is_file()
                and path.suffix.lower() == ".json"
        )

    def browse_json(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Workflow JSON",
            "",
            "JSON Files (*.json)"
        )

        if file_path:
            self.path_edit.setText(file_path)

    def on_path_changed(self, text: str):
        valid = self.is_valid_json_path(text)
        if valid:
            self.invalidPath.setMaximumSize(0, 16777215)
            self.invalidPathSpacer.maximumSize=(134, 16777215)
        else:
            self.invalidPath.setMaximumSize(114, 16777215)
            self.invalidPathSpacer.maximumSize=(20, 16777215)

    def save_bot(self):
        # Future logic placeholder
        if True:
            pass
        else:
            return
        bot_name = self.ui.textEdit_2.toPlainText().strip()
        if bot_name=="":
            bot_name = "New Bot"
        description = self.ui.textEdit.toPlainText().strip()
        workflow_path = self.ui.lineEdit.text().strip()

        """
        if not bot_name:
            print("save failed")
            return
        """

        target_base = Path(self.directoryParent) / "Save" / "Bot"
        target_base.mkdir(parents=True, exist_ok=True)

        # -----------------------------
        # CASE 1: self.path is empty
        # -----------------------------
        bot_dir = str(target_base / bot_name)
        if self.botPath != bot_dir:
            if self.botPath != "":
                #delete botPath dir
                pass

            if Path(bot_dir).exists():
                bot_dir += ("(1)")
                i = 2
                while Path(bot_dir).exists():
                    new_bot_name = str(bot_name) + " (" + str(i) +")"
                    bot_dir = str(target_base /new_bot_name)
            bot_dir = Path(bot_dir)

        else:
            pass
        bot_dir.mkdir(parents=True, exist_ok=True)
        # -----------------------------
        # Create / overwrite JSON
        # -----------------------------
        json_data = {
            "Name": bot_name,
            "Description": description,
            "WorkflowPath": workflow_path
        }

        json_path = bot_dir / "Bot Description.json"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4)

        # -----------------------------
        # Copy portrait image
        # -----------------------------
        gv = self.ui.graphicsView

        image_src = getattr(gv, "current_image_path", None)

        if image_src and Path(image_src).exists():
            ext = Path(image_src).suffix
            shutil.copy(image_src, bot_dir / f"portrait{ext}")

        print("save success")
        self.close()

    def loadSettings(self,path):
        self.botPath = path
        if path=="":
            print("new bot settings")

        else:
            print("loading bot settings")
            print("path: ", path)
