import llmClient

from PySide6.QtWidgets import QMainWindow, QSizePolicy, QFileDialog, QWidget
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, QObject, QEvent
from PySide6.QtGui import QTextCursor
import json
from pathlib import Path
import os
import markdown
import time
from datetime import datetime

class CtrlWheelFontFilter(QObject):
    def __init__(self, widget: QWidget, parent=None):
        super().__init__(parent)
        self.widget = widget
        self.original_vpolicy = widget.verticalScrollBarPolicy()
        self.original_hpolicy = widget.horizontalScrollBarPolicy()
        self.current_size = widget.font().pointSizeF()
        if self.current_size <= 0:
            self.current_size = widget.font().pointSize()

    def _suppress_scroll(self, suppress: bool):
        # hide the scrollbars while we repaint
        policy = Qt.ScrollBarAlwaysOff if suppress else self.original_vpolicy
        self.widget.setVerticalScrollBarPolicy(policy)
        # (you can also suppress the horizontal bar if you want)

    def eventFilter(self, obj, event):
        if obj is not self.widget:
            return False
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            # 1️⃣  suppress the scrollbar
            self._suppress_scroll(True)

            delta = event.angleDelta().y()
            step = 1.0
            if delta < 0:
                new_size = max(6.0, self.current_size - step)
            else:
                new_size = self.current_size + step

            # 2️⃣  apply the new font size *to the whole document*
            font = self.widget.font()
            font.setPointSizeF(new_size)
            self.widget.setFont(font)                       # widget level
            self.widget.document().setDefaultFont(font)     # document level
            self.widget.viewport().update()                 # force repaint

            self.current_size = new_size

            # 3️⃣  restore the scrollbar
            self._suppress_scroll(False)

            return True
        return False

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
    def __init__(self, pd, fd):
        super().__init__()
        self.isShowImgWindow = True
        self.isShowImgWindowLock = False
        self.isShowChatList = False
        self.bot_path = None
        self.bot_desc_path = None
        self.botName =""
        self.chat_markdown = []
        self.chat_path = None  # active chat file path
        self.directoryParent = pd
        self.directoryDefault = fd
        self.response_cursor = None
        self.current_response = ""
        self.response_start_time = time.time()

        self.client = llmClient.LLMClient(fd)
        self.client.token.connect(self.on_token)
        self.client.done.connect(self.on_done)
        self.client.error.connect(print)

        #self.setWindowIcon(QIcon((self.directoryParent + r"\Img\AppIcon.png")))
        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

    def load_ui(self):
        """Load the UI file"""
        try:
            ui_file = QFile(self.directoryDefault+r"\UI\Chat Window.ui")  # Change to your .ui file name
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
                self.hideChatList()

                self._chat_font_filter = CtrlWheelFontFilter(self.chat, parent=self)
                self.chat.installEventFilter(self._chat_font_filter)

                self._input_font_filter = CtrlWheelFontFilter(self.input, parent=self)
                self.input.installEventFilter(self._input_font_filter)

                lastChat = self.get_lastChat()
                self.load_chat(lastChat)
                # Connect signals and slots here
                # Example: self.ui.button.clicked.connect(self.on_button_click)



        except Exception as e:
            print(f"Error loading UI: {e}")

    def setup_connections(self):
        """Connect button signals to functions"""
        # Connect the toggle button - replace 'pushButton_7' with your actual button name
        self.ui.pushButton.clicked.connect(self.toggle_graphics_view)
        self.ui.pushButton_2.clicked.connect(self.toggle_chat_list)
        self.ui.pushButton_5.clicked.connect(self.sendMessage)

        self.ui.actionNew_Chat.setShortcut("Ctrl+N")
        self.ui.actionLoad_Chat.setShortcut("Ctrl+O")
        self.ui.actionChat_Settings.setShortcut("Ctrl+E")

        self.ui.pushButton_6.clicked.connect(self.regenerate_last_response) #regen

        self.ui.actionLoad_Chat.triggered.connect(self.selectChat)

        self.ui.actionDelete_Message.triggered.connect(self.delete_last_user_exchange)

        self.input = self.ui.plainTextEdit
        self._shift_filter = ShiftEnterFilter(self, self.input, parent=self)
        self.input.installEventFilter(self._shift_filter)

    def on_token(self, text):
        if self.response_cursor is None:
            return

        self.current_response += text

        cursor = self.chat.textCursor()
        cursor.setPosition(self.chat.document().characterCount() - 1)
        self.chat.setTextCursor(cursor)

        self.chat.insertPlainText(text)
        self.chat.ensureCursorVisible()

    def on_done(self, full_text: str):
        if self.response_cursor is None:
            return

        doc = self.chat.document()
        cursor = QTextCursor(doc)

        # Select EVERYTHING streamed since response start
        cursor.setPosition(self.response_start_pos)
        cursor.setPosition(doc.characterCount() - 1, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

        # Insert rendered markdown
        cursor.insertHtml(self.render_markdown(full_text))

        cursor.insertBlock()

        self.chat.moveCursor(QTextCursor.End)

        # Save markdown
        end_time = time.time()
        response_time = end_time - self.response_start_time

        msg = {
            "role": "assistant",
            "content": full_text,
            "ts": end_time,
            "response_time": round(response_time, 2)
        }
        self.chat_markdown.append(msg)

        ts = self.format_ts(msg["ts"])
        rt = msg["response_time"]
        cursor.insertHtml(
            f"<div style='color:#888;font-size:11px'>⏱ {rt}s · {ts}</div>"
        )

        self.response_cursor = None
        self.current_response = ""

        self.save_chat()

    def keyPressEvent(self, event):
        if self.input.hasFocus():
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.input.insertPlainText("\n")
                    print("newline")
                else:
                    self.sendMessage()
                    print("sendMessage")
                return
        super().keyPressEvent(event)

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

    def showImgWindow(self):
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
        self.isShowChatList = False
        if not self.isShowImgWindowLock and self.isShowImgWindow:
            self.showImgWindow()
            self.ui.pushButton.setEnabled(True)

    def showChatList(self):
        self.ui.listWidget.setVisible(True)
        self.hideImgWindow()
        self.isShowChatList = True
        self.ui.pushButton.setEnabled(False)

    def get_first_json_file_os(self,directory: str, recursive: bool = False):
        """Using os module instead of pathlib"""
        if not os.path.exists(directory):
            print(f"Directory does not exist: {directory}")
            return None

        if not os.path.isdir(directory):
            print(f"Path is not a directory: {directory}")
            return None

        if recursive:
            # Walk through all subdirectories
            for root, dirs, files in os.walk(directory):
                for file in files:
                    if file.lower().endswith('.json'):
                        return os.path.abspath(os.path.join(root, file))
        else:
            # Check only the given directory
            for file in os.listdir(directory):
                if file.lower().endswith('.json'):
                    return os.path.abspath(os.path.join(directory, file))

        print(f"No .json files found in: {directory}")
        return None

    def get_lastChat(self):
        logPath = Path(self.directoryParent) / "Save" / ".temp.json"
        log = read_json_file(logPath)
        return log["LastChat"]

    def update_lastChat(self, path):
        logPath = Path(self.directoryParent) / "Save" / ".temp.json"
        log = read_json_file(logPath)
        log_new = log.copy()
        log_new["LastChat"] = path

        with open(logPath, "w", encoding="utf-8") as f:
            json.dump(log_new, f, indent=4)

    def load_chat(self, path):
        if path == "":
            savePath = Path(self.directoryParent) / "Save" / "Chat"
            path = self.get_first_json_file_os(str(savePath))
            if path is None:
                return

        self.chat_path = path
        chatHist = read_json_file(path)

        self.update_lastChat(path)
        self.ui.lineEdit.setText(chatHist["Name"])
        self.chat.clear()

        self.chat_markdown = chatHist["Chat"]

        logsPath = Path(self.directoryParent) / "Save" / ".temp.json"
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
        self.client.import_payload(chatHist["Payload"])
        self.client.temperature = chatHist["Temperature"]

        self.ui.listWidget.clear()
        self.ui.listWidget.addItem("Create New Chat [+]")
        for chatFile in reversed(logs["ChatList"]):
            self.ui.listWidget.addItem(chatFile)

        for msg in self.chat_markdown:

            ts = msg.get("ts")
            time_str = f" <span style='color:#888'>[{self.format_ts(ts)}]</span>" if ts else ""

            if msg["role"] == "user":
                #self.chat.append("<b>You</b>:")

                ts = self.format_ts(msg["ts"])
                self.chat.append(f"\n<b>You</b>    <span style='color:#888'>[{ts}] </span>:")
                text = msg['content']
                text= text.replace(r"\n", "\n")
                text = text.replace("\n", "<br/>")
                self.chat.insertHtml(
                    f"<div style='color:#ffffff; margin-left:12px;'>{text}</div>"
                )

            else:
                self.chat.append(f"<b>{self.botName}</b>{time_str}: ")
                self.chat.insertHtml(self.render_markdown(msg["content"]))

                if "response_time" in msg:
                    self.chat.insertHtml(
                        f"<div style='color:#888;font-size:11px'>⏱ {msg['response_time']}s</div>"
                    )

            self.chat.append("")
            self.client.get_model()

        if self.client.model_name != self.client.get_model():
            self.client.switch_model(self.client.model_name)

    def save_chat(self):
        if not self.chat_path:
            return

        data = {
            "Name": self.ui.lineEdit.text(),
            "Bot Path": self.bot_path,
            "Temperature": self.client.temperature,
            "Model": self.client.model_name,
            "Chat": self.chat_markdown,  # ✅ markdown only
            "Payload": self.client.export_payload()
        }

        try:
            with open(self.chat_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Save failed: {e}")

    def load_bot(self,path):
        botJsonPath = path + "/Bot Description.json"
        #botJsonPath = path
        botJson = read_json_file(botJsonPath)
        self.bot_path = path
        self.bot_desc_path = botJsonPath

        self.client.set_preset(botJson["Description"])
        self.botName = botJson["Name"]

        if botJson['WorkflowPath'] =="":
            self.isShowImgWindowLock = True
            self.hideImgWindow()
            self.ui.pushButton.setEnabled(False)
        else:
            self.isShowImgWindowLock = False
            self.showImgWindow()
            self.ui.pushButton.setEnabled(True)
        print("Img Window Lock: ", self.isShowImgWindowLock)

    def sendMessage(self):
        text = self.input.toPlainText().strip()
        self.input.clear()

        if not text:
            return

        text = text.replace(r"\n", "\n")
        text = text.replace("\n", "<br/>")

        # UI
        ts = self.format_ts(self.now_ts())
        self.chat.append(
            f"<b>You</b> <span style='color:#888'>[{ts}]</span>:"
        )
        self.chat.insertHtml(
            f"<div style='color:#ffffff; margin-left:12px;'>{text}</div>"
        )

        self.chat.append(f"\n<b>{self.botName}:</b>")

        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.chat.setTextCursor(cursor)

        # MARK the start of the assistant response
        self.response_cursor = self.chat.textCursor()
        self.response_start_pos = self.response_cursor.position()

        self.current_response = ""

        self.current_response = ""

        # Store markdown
        msg = {
            "role": "user",
            "content": text,
            "ts": self.now_ts()
        }
        self.chat_markdown.append(msg)

        self.client.add_user_message(text)
        self.response_start_time = time.time()
        self.client.generate()

    def render_markdown(self, text: str) -> str:
        html = markdown.markdown(
            text,
            extensions=["fenced_code", "tables"]
        )

        #font - size: 14px;
        return f"""
           <div style="
               font-family: Segoe UI, sans-serif;
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
               {html}
           </div>
           """
    def get_last_user_message(self):
        # Find last user message
        for i in range(len(self.chat_markdown) - 1, -1, -1):
            if self.chat_markdown[i]["role"] == "user":
                return self.chat_markdown[i]["content"]
        return None

    def edit_last_user_message(self, new_text: str):
        new_text = new_text.strip()
        if not new_text:
            return

        # ---- find last user message ----
        for i in range(len(self.chat_markdown) - 1, -1, -1):
            if self.chat_markdown[i]["role"] == "user":
                user_index = i
                break
        else:
            return  # no user message

        # ---- remove assistant reply if present ----
        if (
                user_index + 1 < len(self.chat_markdown)
                and self.chat_markdown[user_index + 1]["role"] == "assistant"
        ):
            self.chat_markdown.pop(user_index + 1)

        # ---- update user message ----
        self.chat_markdown[user_index]["content"] = new_text
        self.chat_markdown[user_index]["ts"] = self.now_ts()

        # ---- rebuild LLM + UI ----
        self._rebuild_llm_context()
        self._rebuild_chat_ui()

        # ---- UI: show edited user message (already rebuilt) ----
        ts = self.format_ts(self.chat_markdown[user_index]["ts"])

        self.chat.append(
            f"<b>{self.botName}:</b>"
        )

        self.chat.moveCursor(QTextCursor.End)

        # ---- prepare streaming state ----
        self.response_cursor = self.chat.textCursor()
        self.response_start_pos = self.response_cursor.position()
        self.current_response = ""
        self.response_start_time = time.time()

        # ---- LLM request ----
        self.client.add_user_message(new_text)
        self.client.generate()

        self.save_chat()

    def delete_last_user_exchange(self):
        for i in range(len(self.chat_markdown) - 1, -1, -1):
            if self.chat_markdown[i]["role"] == "user":
                user_index = i
                break
        else:
            return

        # Remove assistant reply if present
        if user_index + 1 < len(self.chat_markdown) and \
           self.chat_markdown[user_index + 1]["role"] == "assistant":
            self.chat_markdown.pop(user_index + 1)

        # Remove user message
        self.chat_markdown.pop(user_index)

        self._rebuild_llm_context()
        self._rebuild_chat_ui()
        self.save_chat()

    def regenerate_last_response(self):
        if not self.chat_markdown:
            return

        # Remove last assistant message if present
        if self.chat_markdown[-1]["role"] == "assistant":
            self.chat_markdown.pop()

        # Find last user message
        for msg in reversed(self.chat_markdown):
            if msg["role"] == "user":
                last_prompt = msg["content"]
                break
        else:
            return

        # Rebuild everything
        self._rebuild_llm_context()
        self._rebuild_chat_ui()

        # ---- UI: prepare assistant response placeholder ----
        self.chat.append(f"<b>{self.botName}:</b>")
        self.chat.moveCursor(QTextCursor.End)

        self.response_cursor = self.chat.textCursor()
        self.response_start_pos = self.response_cursor.position()
        self.current_response = ""

        # ---- LLM ----
        self.client.add_user_message(last_prompt)
        self.client.generate()

    def _rebuild_llm_context(self):
        # Hard reset payload messages
        self.client.payload_messages = []

        # Re-inject preset / system instruction
        if getattr(self.client, "preset", None):
            self.client.payload_messages.append({
                "role": "system",
                "content": self.client.preset
            })

        # Replay chat history
        for msg in self.chat_markdown:
            self.client.payload_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

    def _rebuild_chat_ui(self):
        self.chat.clear()

        for msg in self.chat_markdown:
            if msg["role"] == "user":
                ts = msg.get("ts")
                time_str = f"[{self.format_ts(ts)}]" if ts else ""

                self.chat.append(
                    f"<b>You</b> <span style='color:#888'>{time_str} </span>:"
                )
                self.chat.insertHtml(
                    f"<div style='color:#ffffff; margin-left:12px;'>{msg['content']} </div>"
                )
            else:
                self.chat.append(f"<b>{self.botName}:</b>")
                self.chat.insertHtml(self.render_markdown(msg["content"]))

            self.chat.append("")

        self.chat.moveCursor(QTextCursor.End)

    """
    def reset_context_for_new_model(self):
        self.visible_history.clear()
        self.llm_messages = self.base_system_messages.copy()   
    """

    def now_ts(self):
        return time.time()

    def format_ts(self, ts: float):
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")

    def selectChat(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select chat JSON",
            self.directoryParent+r"\Save\Chat",
            "JSON Files (*.json)"
        )

        if file_path:
            self.load_chat(file_path)

    def qs_chat(self, chatName):
        chatPath = Path(self.directoryParent + "//Save//Chat//" + chatName + ".json")
        self.load_chat(str(chatPath))

class EditMessage(QMainWindow):

    def __init__(self, pd, fd):
        super().__init__()
        self.directoryParent = pd
        self.directoryDefault = fd
        self.load_ui()
        self.setup_connections()

    def load_ui(self):
        try:
            ui_file = QFile(self.directoryDefault+r"\UI\EditMessage.ui")  # Change to your .ui file name
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

    def __init__(self, pd, fd):
        super().__init__()
        self.directoryParent = pd
        self.directoryDefault = fd
        self.load_ui()
        self.setup_connections()

    def load_ui(self):
        try:
            ui_file = QFile(self.directoryDefault+r"\UI\server ip.ui")  # Change to your .ui file name
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
        ipDefaultPath = self.directoryDefault + r"\UI\config.json"
        ipDefault = read_json_file(ipDefaultPath)
        self.ui.lineEdit.setText(ipDefault["ipReset"])

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