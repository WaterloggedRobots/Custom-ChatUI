import sys
import json
import time
from pathlib import Path

from typing import Set, Optional

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
import paths
from Widgets import chatMain, chatSettings, botSettings, warningWidget

SETTINGS_KEYS = {"Name", "Bot Path", "Temperature", "Model", "Chat"}
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

    if not folder_path.exists():
        print("No such directory")
        return None
    if not folder_path.is_dir():
        print("This is not a directory")
        return None

    search_method = folder_path.rglob if recursive else folder_path.glob

    for file_path in search_method(f"*{file_extension}"):
        #print(file_path)
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

def read_json_file(file_path):
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

class Main:
    def __init__(self):
        self.x =0
        self.setupConnectors()

    def setupConnectors(self) -> None:
        mainChat.ui.actionChat_Settings.triggered.connect(self.openChatSettings)
        mainChat.ui.actionNew_Chat.triggered.connect(self.newChatSettings)
        mainChat.ui.listWidget.clicked.connect(self.quick_select_chat)
        mainChat.ui.actionEdit_Message.triggered.connect(self.openEditMsg)
        mainChat.ui.actionSet_IP.triggered.connect(self.openIPSettings)
        mainChat.ui.actionCreate_New_Chara.triggered.connect(self.newBot)
        mainChat.ui.actionEdit_Chara.triggered.connect(self.loadBot)

        settingsChat.ui.pushButton_2.clicked.connect(self.newBot)
        settingsChat.ui.pushButton_3.clicked.connect(self.cancelChatSettings)
        settingsChat.ui.pushButton_5.clicked.connect(self.saveChatSettings)

        settingsBot.ui.pushButton.clicked.connect(self.exitBotSettings)

        emptyStart.ui.pushButton.clicked.connect(self.newChatSettings)

        messageEdit.ui.pushButton.clicked.connect(self.exitEditMsg)

        settingsIP.ui.pushButton.clicked.connect(self.exitIPSettings)

    def read_json_file(self, file_path):
        """Read a JSON file and return its contents"""
        try:
            # Convert to Path object for better handling
            json_path = Path(file_path)

            if not json_path.exists():
                print(f"❌ File not found from Main: {file_path}")
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

    def get_currentChat(self):
        log = self.read_json_file(paths.CHAT_INDEX_FILE)
        return log["LastChat"]

    def get_currentBot(self):
        chatJson = self.get_currentChat()
        botPath = chatJson["BotPath"]
        return botPath

    def cancelChatSettings(self):

        if noChats:
            warningLeaveEmpty.show()
        else:
            settingsChat.close()

    def saveChatSettings(self):
        if False:
            invalidChatSettings.show()
        else:
            settingsChat.save_settings()
            settingsChat.close()  # Close current window
            if not mainChat.isVisible():
                mainChat.show()
            time.sleep(0.2)
            mainChat.load_chat(self.get_currentChat())

    def refreshModels(self):
        settingsChat.set_models(mainChat.refresh_models())

    def newChatSettings(self):
        self.refreshModels()
        settingsChat.loadSettings("")
        settingsChat.show()
        if emptyStart.isVisible():
            emptyStart.close()

    def openChatSettings(self):
        self.refreshModels()
        settingsChat.loadSettings(str(self.get_currentChat()))
        settingsChat.show()

    def newBot(self):
        settingsBot.loadSettings("")
        settingsBot.show()

    def loadBot(self):
        settingsBot.loadSettings(str(self.get_currentBot()))
        settingsBot.show()

    def exitBotSettings(self):
        settingsBot.close()

    def quick_select_chat(self):
        chatName = mainChat.ui.listWidget.currentItem().text()
        if chatName == "Create New Chat [+]":
            self.newChatSettings()
        else:
            mainChat.qs_chat(chatName)

    def openEditMsg(self):
        message = mainChat.get_last_user_message()
        messageEdit.show()
        messageEdit.ui.plainTextEdit.clear()
        messageEdit.ui.plainTextEdit.appendPlainText(message)

    def exitEditMsg(self):
        mainChat.edit_last_user_message(messageEdit.ui.plainTextEdit.toPlainText().strip())
        messageEdit.close()

    def openIPSettings(self):
        settingsIP.show()
        settingsIP.ui.lineEdit.setText(mainChat.client.server_address)

    def exitIPSettings(self):
        mainChat.client.set_server_address(settingsIP.ui.lineEdit.text())
        print("LLM server set to:", mainChat.client.server_address)
        settingsIP.close()

if __name__ == "__main__":
    QCoreApplication.setOrganizationName("Waterlogged")
    QCoreApplication.setApplicationName("ChatUI")

    paths.ensure_data_dirs()
    print("Client app files:", paths.CLIENT_REPO_DIR)
    print("Client data:     ", paths.CLIENT_DATA_DIR)

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(paths.IMG_DIR / "AppIcon.png")))
    mainChat = chatMain.ChatMain()
    emptyStart = warningWidget.EmptyStart()
    settingsChat = chatSettings.ChatSettings()
    settingsBot = botSettings.BotSettings()
    warningLeaveEmpty = warningWidget.EmptyLeave()
    invalidChatSettings = warningWidget.InvalidChatSettings()
    messageEdit = chatMain.EditMessage()
    settingsIP = chatMain.ServerIP()

    connector = Main()

    if find_json_with_format(str(paths.CHAT_DIR), SETTINGS_KEYS) is not None:
        noChats = False
        mainChat.show()

    else:
        noChats = True
        emptyStart.show()

    sys.exit(app.exec())