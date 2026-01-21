import sys
import os
import json
from pathlib import Path

import llm_client as llm

from typing import Set, Optional

from PySide6.QtWidgets import (QApplication, QMainWindow, QDialog, QFileDialog,
                               QVBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTextEdit, QDialogButtonBox, QMessageBox, QSizePolicy)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QPixmap
import shutil

SETTINGS_KEYS = {"ID","Name","Character","Model","Datasets", 'Chat History'}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.isShowImg = True
        self.isShowChatList = False

        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

        self.client = llm.LLMClient()
        self.client.token.connect(self.on_token)
        self.client.done.connect(self.on_done)

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(parent_directory+r"\UI\Chat Window.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("ChatUI")
                self.hideChatList()

                # Connect signals and slots here
                # Example: self.ui.button.clicked.connect(self.on_button_click)

        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        self.ui.pushButton.clicked.connect(self.toggle_graphics_view)
        self.ui.pushButton_2.clicked.connect(self.toggle_chat_list)
        self.ui.actionChat_Settings.triggered.connect(self.openChatSettings)
        self.ui.actionNew_Chat.triggered.connect(self.newChatSettings)

        self.ui.actionNew_Chat.setShortcut("Ctrl+N")
        self.ui.actionNew_Chat.setShortcut("Ctrl+E")

    def on_token(self, text):
        self.chat.insertPlainText(text)
        self.chat.ensureCursorVisible()

    def on_done(self, full_text):
        self.chat.insertPlainText("\n\n")
        print(self.chat)

    def keyPressEvent(self, event):
        if self.input.hasFocus():
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.input.insertPlainText("\n")
                else:
                    text = self.input.toPlainText().strip()
                    self.input.clear()
                    if text:
                        self.chat.append(f"\nYou:\n{text}\n\nModel:\n")
                        self.client.add_user_message(text)
                        self.client.generate()
                return
        super().keyPressEvent(event)

    def newChatSettings(self):
        global chatSettings
        chatSettings.loadSettings("")
        chatSettings.show()

    def openChatSettings(self):
        global chatSettings
        chatSettings.loadSettings("www")
        chatSettings.show()

    def toggle_graphics_view(self):
        """Toggle graphics view visibility by adjusting width constraints"""
        if self.isShowImg:
            self.hideImg()
            self.isShowImg = False
        else:
            self.showImg()
            self.isShowImg = True

    def hideImg(self):
        layoutH3 = self.ui.horizontalLayout_3
        # HIDE: Set max width to 0, disable the widget, and collapse layout space
        self.ui.graphicsView.setMaximumWidth(0)
        self.ui.graphicsView.setMinimumWidth(0)

        layoutH3.itemAt(3).changeSize(0, 20, QSizePolicy.Fixed, QSizePolicy.Fixed)
        # layoutH3.itemAt(5).changeSize(0,20, QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.ui.pushButton_7.setMaximumWidth(0)
        self.ui.pushButton_7.setEnabled(False)
        self.ui.graphicsView.setEnabled(False)

        # Optional: Also hide it visually
        self.ui.graphicsView.setVisible(False)

        # Update button text
        self.ui.pushButton.setText("<<")

        # Optional: Adjust layout column stretch if using gridLayout
        # This helps textBrowser expand into the freed space
        if hasattr(self.ui, 'gridLayout'):
            self.ui.gridLayout.setColumnStretch(1, 0)  # Column 1 is graphicsView

    def showImg(self):
        layoutH3 = self.ui.horizontalLayout_3
        # SHOW: Restore original width constraints and enable
        self.ui.graphicsView.setMaximumWidth(16777215)
        self.ui.graphicsView.setMinimumWidth(0)

        layoutH3.itemAt(3).changeSize(40, 20, QSizePolicy.Expanding, QSizePolicy.Fixed)
        # layoutH3.itemAt(5).changeSize(30, 20, QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.ui.graphicsView.setEnabled(True)
        self.ui.graphicsView.setVisible(True)

        # Update button text
        self.ui.pushButton.setText(">>")

        self.ui.pushButton_7.setMaximumWidth(16777215)
        self.ui.pushButton_7.setEnabled(True)

        self.is_graphics_view_visible = True

        # Force layout update
        self.ui.graphicsView.parentWidget().updateGeometry()

    def toggle_chat_list(self):
        """Toggle graphics view visibility by adjusting width constraints"""
        if self.isShowChatList:
            self.hideChatList()
            self.ui.pushButton_2.setText(">>")
        else:
            self.showChatList()
            self.ui.pushButton_2.setText("<<")

    def hideChatList(self):
        self.ui.listWidget.setVisible(False)
        if self.isShowImg:
            self.showImg()
        self.isShowChatList = False
        self.ui.pushButton.setEnabled(True)

    def showChatList(self):
        self.ui.listWidget.setVisible(True)
        self.hideImg()
        self.isShowChatList = True
        self.ui.pushButton.setEnabled(False)

class EmptyStart(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = None
        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(parent_directory+r"\UI\Empty Start.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Warning: No Chats")

        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        self.ui.pushButton.clicked.connect(self.newChatSettings)
        #self.ui.pushButton_2.clicked.connect(self.close())

    def newChatSettings(self):
        global chatSettings
        self.close()        # Close current window
        chatSettings.newChat = True
        chatSettings.loadSettings("")
        chatSettings.show() # Show the second window

class ChatSettings(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = None
        self.chatPath = ""
        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(parent_directory+r"\UI\Chat Settings.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Chat Settings")

        except Exception as e:
            print(f"Error loading UI: {e}")

    def loadSettings(self,path):
        self.chatPath = path
        if path=="":
            print("new chat settings")

        else:
            print("loading chat settings")
            print("path: ", path)
            #loadSettingsFile =

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        self.ui.pushButton_3.clicked.connect(self.cancel)
        self.ui.pushButton_5.clicked.connect(self.save)

    def cancel(self):

        if noChats:
            global warningEmptyChat
            warningEmptyChat.show()
        else:
            self.close()

    def save(self):
        global window
        if False:
            pass
        else:
            #newChatPath.save
            #if self.chatPath !="":
                #delete old file at self.chatPath
            self.close()  # Close current window
            if not window.isVisible():
                window.show()

class WarningEmptyChat(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = None
        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(parent_directory+r"\UI\Warning Chat.ui")  # Change to your .ui file name
            if not ui_file.open(QFile.ReadOnly):
                print(f"Cannot open UI file: {ui_file.errorString()}")
                return

            loader = QUiLoader()
            self.ui = loader.load(ui_file)
            ui_file.close()

            if self.ui:
                self.setCentralWidget(self.ui)
                self.setWindowTitle("Warning: No Chats")

        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        self.ui.pushButton.clicked.connect(self.cancel)
        self.ui.pushButton_2.clicked.connect(self.exit)

    def cancel(self):
        self.close()

    def exit(self):
        global chatSettings
        chatSettings.close()
        self.close()

def find_json_with_format(
        target_folder: str,
        required_keys: Set[str],
        file_extension: str = ".json",
        recursive: bool = False
) -> Optional[Path]:
    """
    Same as above but returns the Path of the first matching file.
    Returns None if no match is found.
    """
    folder_path = Path(target_folder)

    if not folder_path.exists() or not folder_path.is_dir():
        return None

    search_method = folder_path.rglob if recursive else folder_path.glob

    for file_path in search_method(f"*{file_extension}"):
        if not file_path.is_file():
            continue

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if isinstance(data, dict) and required_keys.issubset(data.keys()):
                return file_path
        except (json.JSONDecodeError, UnicodeDecodeError, PermissionError):
            continue

    return None

noChats = True
newChatID = -1

def main():
    global noChats, window, emptyStart, chatSettings, warningEmptyChat
    app = QApplication(sys.argv)
    window = MainWindow()
    emptyStart = EmptyStart()
    chatSettings = ChatSettings()
    warningEmptyChat = WarningEmptyChat()

    if find_json_with_format(parent_directory+r"Save\Chats", SETTINGS_KEYS) is not None:
        noChats = False
        window.show()
    else:
        noChats = True
        emptyStart.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    window = None
    emptyStart = None
    chatSettings = None
    warningEmptyChat = None

    current_script_path = os.path.abspath(__file__)
    parent_directory = os.path.dirname(current_script_path)
    print(parent_directory)
    main()