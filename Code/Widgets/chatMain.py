import llmClient

from PySide6.QtWidgets import QMainWindow, QSizePolicy, QFileDialog, QPlainTextEdit, QGraphicsScene, QGraphicsView, QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsItemGroup
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, Qt, QObject, QEvent, QUrl
from PySide6.QtGui import QTextCursor, QPixmap, QIcon, QPixmap
import json
from pathlib import Path
import os
import markdown
import time
from datetime import datetime
from PySide6.QtCore import Signal
import threading
import re

from test_client import parent_directory


ICON_SIZE = 96
COLUMNS = 4
SPACING = 20


class DropGraphicsView(QGraphicsView):
    def __init__(self, parent=None, file_callback=None):
        super().__init__(parent)
        self.file_callback = file_callback
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)


    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()


    def dragMoveEvent(self, event):  # <-- THIS IS REQUIRED
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()


    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if file_path and self.file_callback:
                    self.file_callback(file_path)
            event.acceptProposedAction()
        else:
            event.ignore()

class DropTextEdit(QPlainTextEdit):
    def __init__(self, parent=None, file_callback=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.file_callback = file_callback


    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()


    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if file_path and self.file_callback:
                    self.file_callback(file_path)

            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class FileTile(QGraphicsItemGroup):
    def __init__(self, file_path, parent_window):
        super().__init__()
        self.file_path = file_path
        self.parent_window = parent_window
        self.setAcceptedMouseButtons(Qt.LeftButton)


    def mousePressEvent(self, event):
        self.parent_window.remove_tile(self)
        event.accept()


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
                cursor = self.input_widget.textCursor()
                cursor.insertText("\n")
                return True            # stop further processing

            # Plain Enter → send the message
            self.chat_main.sendMessage()
            return True                # stop further processing

        return False                    # let Qt process other events

class ChatMain(QMainWindow):
    final_response = Signal(str)

    def __init__(self, pd, fd, ver):
        super().__init__()
        self.isShowImgWindow = True
        self.isShowImgWindowLock = False
        self.isShowChatList = False
        self.bot_path = None
        self.bot_desc_path = None
        self.botName =""
        self.chat_name=""
        self.chat_markdown = []
        self.chat_path = None  # active chat file path
        self.directoryParent = pd
        self.directoryDefault = fd
        self.response_cursor = None
        self.current_response = ""
        self.current_response_full = ""
        self.response_start_time = time.time()
        self.version = ver
        self.status = None
        self.fileList=[]
        self.imgList=[]
        self.enableWebSearch = False
        self.enableImgGen = False
        self.enableCOTButton = False
        self.enableCOT = False
        self.enableWebSearchAdvanced = False
        self.is_chain_of_thought = False
        self.cot_thread = None
        self.cot_abort = False


        self.client = llmClient.LLMClient(fd)
        self.client.token.connect(self.on_token)
        self.client.done.connect(self.on_done)
        self.client.statusNew.connect(self.on_status)
        self.final_response.connect(self._start_final_response)
        self.client.error.connect(print)

        self.scene = QGraphicsScene()
        #self.setWindowIcon(QIcon((self.directoryParent + r"\Img\AppIcon.png")))
        self.load_ui()
        self.setup_connections()
        # Store the initial stretch for the graphicsView column (e.g., 1)
        self.graphics_view_column_stretch = 1

        self.ui.graphicsView_2.setScene(self.scene)


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
                #self.input = DropPlainTextEdit()
                self.input = self.ui.plainTextEdit
                # Save original widget
                old_input = self.ui.plainTextEdit
                parent_layout = old_input.parentWidget().layout()

                # Create DropTextEdit
                self.input = DropTextEdit(
                    parent=old_input.parentWidget(),
                    file_callback=self.add_file_to_view
                )


                self.input.setObjectName(old_input.objectName())
                self.input.setStyleSheet(old_input.styleSheet())
                self.input.setFont(old_input.font())

                self.input.setSizePolicy(old_input.sizePolicy())
                self.input.setMinimumSize(old_input.minimumSize())
                self.input.setMaximumSize(old_input.maximumSize())

                old_view = self.ui.graphicsView_2
                parent_layout = old_view.parentWidget().layout()

                self.ui.graphicsView_2 = DropGraphicsView(
                    parent=old_view.parentWidget(),
                    file_callback=self.add_file_to_view
                )

                self.ui.graphicsView_2.setObjectName(old_view.objectName())
                self.ui.graphicsView_2.setScene(self.scene)

                parent_layout.replaceWidget(old_view, self.ui.graphicsView_2)
                old_view.deleteLater()

                parent_layout.replaceWidget(old_input, self.input)

                old_input.deleteLater()

                self._shift_filter = ShiftEnterFilter(self, self.input, parent=self)
                self.input.installEventFilter(self._shift_filter)
                self.input.setFocusPolicy(Qt.StrongFocus)
                self.input.setFocus()
                self.hideChatList()
                lastChat = self.get_lastChat()
                self.load_chat(lastChat)
                self.ui.label_2.setText(self.client.get_status())
                self.ui.label.setText(self.version)
                self.ui.graphicsView_2.setAcceptDrops(True)
                self.ui.graphicsView_2.viewport().setAcceptDrops(True)
                self.ui.graphicsView_2.setMaximumHeight(20)

                self.ui.checkBox.setChecked(False)  # web search
                self.ui.checkBox_2.setChecked(False)  # image gen

                self.ui.checkBox_4.setMaximumHeight(0)

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
        self.ui.pushButton_3.clicked.connect(self.load_file)
        self.ui.checkBox.clicked.connect(self.enable_web_search)
        self.ui.checkBox_2.clicked.connect(self.enable_img_gen)
        self.ui.checkBox_3.clicked.connect(self.enable_cot)
        self.ui.checkBox_4.clicked.connect(self.enable_web_search_advanced)

    def on_token(self, text):
        if not self.is_chain_of_thought:

            self.current_response += text

            safe = (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
                .replace("  ", "&nbsp;&nbsp;")
            )

            cursor = QTextCursor(self.response_cursor)
            cursor.movePosition(QTextCursor.End)

            cursor.insertHtml(safe)

            self.response_cursor = cursor

            self.chat.moveCursor(QTextCursor.End)
            self.chat.ensureCursorVisible()

    def on_done(self, full_text: str):
        if not self.is_chain_of_thought:
            cursor = self.chat.textCursor()

            # return to start of assistant block
            cursor.setPosition(self.assistant_block_start)

            # remove placeholder + streamed tokens
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()

            end_time = time.time()
            response_time = end_time - self.response_start_time
            print("Response Text:", full_text)
            msg = {
                "role": "assistant",
                "content": full_text,
                "ts": end_time,
                "response_time": round(response_time, 2),
                "tokens used": self.client.token_count
            }

            self.chat_markdown.append(msg)

            # render final message using normal renderer
            self.render_chat_message(msg)

            self.save_chat()

            #self.current_response = ""

            self.input.setFocus()

            self.chat.moveCursor(QTextCursor.End)
            self.chat.ensureCursorVisible()
        self.current_response_full = self.current_response

    def on_status(self, status:str):
        self.ui.label_2.setText(status)
        self.status = status


    def load_file(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Files",
            "",
            "All Files (*);;Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )

        if not files:
            return

        for file_path in files:
            self.add_file_to_view(file_path)


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


    def get_first_directory_os(self,directory: str):
        """Using os module instead of pathlib"""
        if not os.path.isdir(directory):
            print(f"Path is not a directory: {directory}")
            return None
        if not os.path.exists(directory):
            print(f"Directory does not exist: {directory}")
            return None

            # Check only the given directory
        items = os.listdir(directory)

        # Filter for directories
        directories = []
        for item in items:
            item_path = os.path.join(directory, item)
            if os.path.isdir(item_path):
                directories.append(item)

        # Return first directory if found
        if directories:
            # Sort to ensure consistent results (optional)
            directories.sort()
            first_dir = os.path.join(directory, directories[0])
            return os.path.abspath(first_dir)

        print(f"No directories found in: {directory}")
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
            path = self.get_first_directory_os(str(savePath))
            if path is None:
                return

        self.chat_path = Path(path + '/Chat.json')
        self.chat_name = Path(path).stem
        chatHist = read_json_file(self.chat_path)

        self.update_lastChat(path)
        self.ui.lineEdit.setText(chatHist["Name"])
        self.chat.clear()

        self.chat_markdown = chatHist["Chat"]

        logsPath = Path(self.directoryParent) / "Save" / ".temp.json"
        logs = read_json_file(logsPath)
        #chat_path_str = Path(self.chat_path).stem

        if self.chat_name in logs["ChatList"] and logs["ChatList"].index(self.chat_name) != len(logs["ChatList"])-1:
            logs["ChatList"].remove(self.chat_name)
        if self.chat_name not in logs["ChatList"]:
            logs["ChatList"].append(self.chat_name)

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

        self.chat.clear()

        for msg in self.chat_markdown:
            self.render_chat_message(msg)

        self.chat.moveCursor(QTextCursor.End)

        if self.client.model_name != self.client.get_model():
            self.client.switch_model(self.client.model_name)
            print("Now Using Model: ",self.client.get_model())

    def save_chat(self):
        if not self.chat_path:
            return

        data = {
            "Name": self.ui.lineEdit.text(),
            "Bot Path": self.bot_path,
            "Temperature": self.client.temperature,
            "Model": self.client.model_name,
            "Chat": self.chat_markdown,  # ✅ markdown only
            "Payload": self.client.export_payload() #full chat
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

        text_raw = self.input.toPlainText()
        raw_text = text_raw.strip()

        if not raw_text and not self.imgList and not self.fileList:
            return

        self.input.clear()
        self.scene.clear()
        self.auto_resize_graphics_view()

        # Store user message in UI (visible)
        msg = {
            "role": "user",
            "content": raw_text,
            "ts": self.now_ts(),
            "attachments": {
                "images": [os.path.basename(p) for p in self.imgList],
                "files": [os.path.basename(p) for p in self.fileList]
            }
        }
        self.chat_markdown.append(msg)
        self.render_chat_message(msg)

        # Insert assistant placeholder
        self.insert_assistant_placeholder()

        self.input.setEnabled(False)
        self.ui.pushButton_5.setEnabled(False)
        self.ui.pushButton_6.setEnabled(False)

        # Start CoT in background thread
        self.cot_abort = False
        self.cot_thread = threading.Thread(
            target=self._run_cot_chain,
            args=(raw_text,),
            daemon=True
        )
        self.cot_thread.start()

    def _run_cot_chain(self, raw_text):
        """Run CoT chain in background thread"""
        try:
            if self.enableCOT or self.enableCOTButton:
                final_plan_prompt =  f"Based on all analysis:\n- Original query: {raw_text}\n"
                cot_context = f"Previous reasoning steps:\n1. Query analysis completed"
                function_prompt = (f"Understand the user's motive and respond what they plan to do based on their prompt: {raw_text}\n Simply respond what they are expecting you to do.\n Now compile your tasks-to-do into a python array by appending specific strings given the specific task as shown: \n"
                                   f"If the user wishes to find information via the internet or the web and uses prompts such as 'search the net' or 'find ... on the internet', append the exact string 'search_web' into the array.\n"
                                   f"If the user wishes to power on the 'mercury machine' or his computer, append 'power_on_mercury' into the array. \n"
                                   f"if no requests were made that fit the specific parameters, return an empty list. Do not return the code to generate said array.")
                cot_function_comprehension="[]"
                cot_function_comprehension = self.client.run_cot_step(function_prompt)
                print("FUNCTIONS :", cot_function_comprehension)

                descriptions = []
                if self.imgList:
                    for i, image in enumerate(self.imgList):
                        instruction = f"Describe this image #{i + 1} in detail. Extract any text visible."
                        input_dir = str(Path(self.directoryParent) / "Save" / "Chat" / str(self.chat_name) / "Input")
                        result = self.client.analyze_image_nas(image, instruction, input_dir, self.chat_name)
                        descriptions.append(result)

                        cot_image = self.client.run_cot_step(
                            f"Based on this image description: '{result}'\n"
                            f"How does this relate to the user's query: '{raw_text}'?"
                        )
                        print(f"🖼️ Image reasoning: {cot_image[:100]}...")
                    final_plan_prompt += f"- Images: {len(descriptions)} analyzed\n"
                    cot_context += f"Image analysis completed\n"

                file_content =[]
                if self.fileList:
                    for i, file in enumerate(self.fileList):
                        instruction = f"Describe this file #{i + 1} in detail. Extract any text visible."
                        input_dir = str(Path(self.directoryParent) / "Save" / "Chat" / str(self.chat_name) / "Input")
                        result = self.client.analyze_file_nas(file, instruction, input_dir, self.chat_name)
                        file_content.append(result)
                        #print(result)
                        cot_file = self.client.run_cot_step(
                            f"Based on this file description: '{result}'\n"
                            f"How does this relate to the user's query: '{raw_text}'?"
                        )
                        print(f"🖼️ File reasoning: {cot_file[:100]}...")
                    final_plan_prompt += f"- Files: {len(file_content)} analyzed\n"
                    cot_context += f"File analysis completed\n"

                search_context = ""
                # Step 3: Web search if enabled
                if self.enableWebSearch:

                    cot1 = self.client.run_cot_step(
                        f"Analyze this query: '{raw_text}'. "
                        "What keywords should I search for? What am I trying to find? Give me only an list of keywords or phrases in the form of a python list of length 5 in string type, unless otherwise specified, that i can use to search on the internet. You don't have to respond with anything else"
                    )
                    print(f"🔍 Search analysis: {cot1}")

                    search_context_from_prompt = self.client.web_search(raw_text)
                    search_context_from_keyword = []
                    keywords_from_prompt = get_array_from_str(cot1)
                    print(keywords_from_prompt, type(keywords_from_prompt))
                    for keyword in keywords_from_prompt:
                        search_context_from_keyword.append(self.client.web_search(keyword))
                    search_context = f"Search results: '{search_context_from_prompt[:500]}'\n Other search results include:\n"

                    for i in range (len(search_context_from_keyword)):
                        search_context += f"searched with keyword '" + keywords_from_prompt[i] + f"':\n{search_context_from_keyword[i][:500]}\n"
                    search_context += f"How should I synthesize this information to answer: '{raw_text}'?"
                    cot_search = self.client.run_cot_step(search_context)

                    print(f"🌐 Search synthesis: {cot_search[:100]}...")
                    final_plan_prompt += f"- Search: {'Available' if search_context else 'Not used'}\n\n"
                    cot_context += f"Search analysis completed\n"


                # Step 4: Plan the final response
                final_plan_prompt += f"Plan the structure of your final response.\n Consider: tone, key points, any limitations."
                final_plan = self.client.run_cot_step(final_plan_prompt)
                print(f"📋 Response plan: {final_plan[:100]}...")
                cot_context += f"Response plan: {final_plan[:200]}"

                # Build the final prompt
                full_text = raw_text

                if descriptions:
                    descriptions_text = "\n".join([f"Image {i + 1}: {d}" for i, d in enumerate(descriptions)])
                    full_text = f"""Based on these image descriptions:\n{descriptions_text}\n{raw_text}"""

                if file_content:
                    file = "\n".join([f"File {i + 1}: {d}" for i, d in enumerate(file_content)])
                    full_text = f"""Based on these file contents:\n{file}\n{full_text}"""

                if search_context:
                    full_text = f"""Based on these search results:\n{search_context}\n{full_text}"""

                # Add CoT context
                cot_context +=f"Now provide the final answer.\n"
                full_text = cot_context + "\n\n" + full_text

                # Switch back to UI thread to start final response
                self.final_response.emit(full_text)

            else:
                self.final_response.emit(raw_text)

        except Exception as e:
            print(f"CoT chain error: {e}")
            # Fallback to just the raw text
            self.final_response.emit(raw_text)


    def _start_final_response(self, full_text):
        """Start final response generation (runs in UI thread)"""
        print(f"🔵 FINAL RESPONSE PROMPT: {full_text[:200]}...")

        # Re-enable UI
        self.input.setEnabled(True)
        self.ui.pushButton_5.setEnabled(True)
        self.ui.pushButton_6.setEnabled(True)

        # Clear temporary data
        self.imgList.clear()
        self.fileList.clear()

        self.enableCOT = self.enableWebSearch

        self.auto_resize_graphics_view()

        # Start final response
        self.client.generate_final_response(full_text)
        self.input.setFocus()


    def abort_cot(self):
        """Abort current CoT chain"""
        self.cot_abort = True
        self.client._abort_flag = True
        self.input.setEnabled(True)
        self.ui.pushButton_5.setEnabled(True)
        self.ui.pushButton_6.setEnabled(True)


    def render_markdown(self, text: str) -> str:

        html = markdown.markdown(
            text,
            extensions=["fenced_code", "tables"]
        )

        return f"""
        <div style="
            font-family: Segoe UI;
            font-size:14px;
            line-height:1.5;
        ">
        {html}
        </div>
        """


    def render_chat_message(self, msg: dict):

        role = msg["role"]
        ts = msg.get("ts")
        ts_str = self.format_ts(ts) if ts else ""

        #self.stream_marker = f"STREAM_{int(time.time() * 1000)}"

        if role == "user":

            text = msg["content"].replace("\n", "<br/>")
            attachments = msg.get("attachments", {})

            html_attach = f""" """

            html = f"""
            <table width="100%" cellspacing="0" cellpadding="0">
            <tr>
            <td style="
            background:#3a3a3a;
            padding:12px;
            border-radius:8px;
            ">
            <b>You: </b> [{ts_str}]<br>
            """
            if attachments:
                html_attach = self.get_attachment_html(attachments)
            html += html_attach
            html += f"""
                    {text}
                    </td>
                    </tr>
                    </table>
                    """

            cursor = self.chat.textCursor()
            cursor.movePosition(QTextCursor.End)

            cursor.insertHtml("<p style='margin:8px 0'></p>")  # force new block
            cursor.insertHtml(html)

        elif role == "assistant":

            text_html = self.render_markdown(msg["content"])

            html = f"""
                <table width="100%" cellspacing="0" cellpadding="0">
                <tr>
                <td style="
                background:#2b2b2b;
                padding:12px;
                border-radius:8px;
                ">
                <b>{self.botName}: </b> [{ts_str}]<br>
                {text_html}
                </td>
                </tr>
                </table>
            """

            html += f"""
            <div style="margin-top:6px;">
            <span style="white-space:pre-wrap;"></span>
            </div>
            """

            if "response_time" in msg or "tokens used" in msg:
                rt = msg.get("response_time", "")
                tk = msg.get("tokens used", "")

                html += f"""
                <div style="color:#888;font-size:11px;margin-top:6px">
                ⏱ {rt}s · 🪙 {tk} <br/>
                </div>
                """

            html += """
                </div>
            </div>
            """

            cursor = self.chat.textCursor()
            cursor.movePosition(QTextCursor.End)

            cursor.insertHtml("<p style='margin:8px 0'></p>")  # force new block
            cursor.insertHtml(html)

        # auto scroll
        self.chat.moveCursor(QTextCursor.End)
        self.chat.ensureCursorVisible()

    def insert_assistant_placeholder(self):

        # store where assistant block begins
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.assistant_block_start = cursor.position()

        ts = self.format_ts(self.now_ts())

        cursor.insertHtml("<p style='margin:8px 0'></p>")

        cursor.insertHtml(f"""
        <table width="100%" cellspacing="0" cellpadding="0">
        <tr>
        <td style="
            background:#2b2b2b;
            padding:12px;
            border-radius:8px;
        ">
        <b>{self.botName}:</b> [{ts}]<br>
        """)

        # streaming cursor
        self.response_cursor = self.chat.textCursor()

        cursor.insertHtml("</td></tr></table>")

        self.chat.moveCursor(QTextCursor.End)

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

        self.chat.moveCursor(QTextCursor.End)

        # ---- prepare streaming state ----
        self.response_cursor = self.chat.textCursor()
        self.response_start_pos = self.response_cursor.position()
        self.current_response = ""
        self.response_start_time = time.time()

        # ---- LLM request ----
        self.client.add_user_message(new_text)
        self.insert_assistant_placeholder()
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
        for i in range(len(self.chat_markdown) - 1, -1, -1):
            if self.chat_markdown[i]["role"] == "user":
                user_index = i
                break
        else:
            return  # no user message
        if (
                user_index + 1 < len(self.chat_markdown)
                and self.chat_markdown[user_index + 1]["role"] == "assistant"
        ):
            self.chat_markdown.pop(user_index + 1)

        # ---- update user message ----


        # Restore prompt
        print("last text: ", self.chat_markdown[user_index]["content"])
        self.input.setPlainText(self.chat_markdown[user_index]["content"] )

        # Restore attachments
        attachments = self.chat_markdown[user_index]["attachments"]

        base_path = os.path.join(
            self.directoryParent,
            "Save",
            "Chat",
            self.chat_name,
            "Input"
        )

        self.imgList = [
            os.path.join(base_path, img)
            for img in attachments.get("images", [])
        ]

        self.fileList = [
            os.path.join(base_path, file)
            for file in attachments.get("files", [])
        ]

        # Refresh attachment view
        #self.update_attachment_view()
        if not self.enableCOTButton:
            self.enableCOT = (self.imgList != [] or self.fileList != []) or self.enableWebSearch

        # Send again
        self.delete_last_user_exchange()
        self.sendMessage()


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
            self.render_chat_message(msg)

        self.chat.moveCursor(QTextCursor.End)


    def get_attachment_html(self, attachments):

        images = attachments.get("images", [])
        files = attachments.get("files", [])

        if not images and not files:
            return ""

        base_path = Path(self.directoryParent) / "Save" / "Chat" / self.chat_name / "Input"

        html = "<div style='margin-top:6px;'>"

        for img in images:
            full_path = base_path / img
            if full_path.exists():
                uri = full_path.resolve().as_uri()

                html += f"""
                <div style="display:inline-block;text-align:left;margin:6px;">
                    <img src="{uri}" width="96"><br/>
                    <span style="font-size:11px;color:#aaa;">{img}</span>
                </div>
                """

        icon_path = Path(self.directoryDefault) / "Img" / "FileIcon.png"
        icon_uri = icon_path.resolve().as_uri()

        for file in files:
            html += f"""
            <div style="display:inline-block;text-align:left;margin:6px;">
                <img src="{icon_uri}" width="96"><br/>
                <span style="font-size:11px;color:#aaa;">{file}</span>
            </div>
            """

        html += "</div>"

        return html


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
        chatPath = Path(self.directoryParent + "//Save//Chat//" + chatName)# + "//Chat.json")
        self.load_chat(str(chatPath))


    def add_file_to_view(self, file_path):
        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()

        # IMAGE?
        if ext in [".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"]:
            self.imgList.append(file_path)
            pixmap = QPixmap(file_path)
        else:
            self.fileList.append(file_path)
            icon = QIcon.fromTheme("text-x-generic")
            pixmap = icon.pixmap(ICON_SIZE, ICON_SIZE)

            if pixmap.isNull():
                pixmap = QPixmap(self.directoryDefault + r"\Img\FileIcon.png")

        pixmap = pixmap.scaled(
            ICON_SIZE,
            ICON_SIZE,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.create_tile(pixmap, filename, file_path)

    def create_tile(self, pixmap, filename, file_path):
        tile = FileTile(file_path, self)

        icon_item = QGraphicsPixmapItem(pixmap)
        text_item = QGraphicsTextItem(filename)

        x_offset = (ICON_SIZE - pixmap.width()) / 2
        y_offset = (ICON_SIZE - pixmap.height()) / 2
        icon_item.setPos(x_offset, y_offset)

        text_item.setTextWidth(ICON_SIZE)
        text_item.setDefaultTextColor(Qt.white)
        text_item.setPos(0, ICON_SIZE + 5)

        tile.addToGroup(icon_item)
        tile.addToGroup(text_item)

        self.scene.addItem(tile)

        self.position_tiles()
        self.auto_resize_graphics_view()


    def position_tiles(self):
        items = [i for i in self.scene.items() if isinstance(i, FileTile)]
        items.reverse()

        for index, item in enumerate(items):
            row = index // COLUMNS
            col = index % COLUMNS

            x = col * (ICON_SIZE + SPACING)
            y = row * (ICON_SIZE + 40)

            item.setPos(x, y)

        self.scene.setSceneRect(self.scene.itemsBoundingRect())


    def remove_tile(self, tile):
        file_path = tile.file_path
        ext = os.path.splitext(file_path)[1].lower()

        # Remove from correct list
        if ext in [".png", ".jpg", ".jpeg", ".bmp", ".webp",".gif"]:
            if file_path in self.imgList:
                self.imgList.remove(file_path)
        else:
            if file_path in self.fileList:
                self.fileList.remove(file_path)

        # Remove from scene
        self.scene.removeItem(tile)
        #print(self.imgList, self.fileList)
        # Re-layout
        self.position_tiles()
        self.auto_resize_graphics_view()

    def auto_resize_graphics_view(self):
        #print(self.imgList, self.fileList)
        rect = self.scene.itemsBoundingRect()
        height = rect.height() + 20

        if height < 120:
            height = 120

        #self.ui.graphicsView_2.setMinimumHeight(height)
        #self.ui.graphicsView_2.setMaximumHeight(height)
        if len(self.imgList) == 0 and len(self.fileList) == 0:
            self.ui.graphicsView_2.setMaximumHeight(20)
            self.ui.graphicsView_2.setMinimumHeight(20)
        else:
            self.ui.graphicsView_2.setMaximumHeight(height)
            self.ui.graphicsView_2.setMinimumHeight(height)

        self.enableCOT = (self.imgList != [] or self.fileList != []) or self.enableWebSearch
        enable = self.enableCOT or self.enableCOTButton
        self.ui.checkBox_3.setChecked(enable)

    def display_saved_attachments(self, attachments, inside_container=False):
        images = attachments.get("images", [])
        files = attachments.get("files", [])

        if not images and not files:
            return

        base_path = Path(self.directoryParent) / "Save" / "Chat" / self.chat_name / "Input"

        html = ""

        if not inside_container:
            html += "<div style='margin-left:12px; margin-top:6px;'>"

        # --- Images ---
        for img in images:
            full_path = base_path / img

            if full_path.exists():
                img_uri = full_path.resolve().as_uri()

                html += f"""
                    <div style="display:inline-block; text-align:center; margin:6px;">
                        <img src="{img_uri}" width="96"><br/>
                        <span style="font-size:11px; color:#aaa;">{img}</span>
                    </div>
                """

        # --- Files ---
        icon_path = Path(self.directoryDefault) / "Img" / "FileIcon.png"
        icon_uri = icon_path.resolve().as_uri()

        for file in files:
            html += f"""
                <div style="display:inline-block; text-align:center; margin:6px;">
                    <img src="{icon_uri}" width="96"><br/>
                    <span style="font-size:11px; color:#aaa;">{file}</span>
                </div>
            """

        if not inside_container:
            html += "</div>"

        self.chat.insertHtml(html)


    def enable_web_search(self):
        if self.enableWebSearchAdvanced:
            self.enableWebSearchAdvanced = False
            self.ui.checkBox_4.setChecked(False)
            self.enableWebSearch = True
        else:
            self.enableWebSearch = not self.enableWebSearch

        print("web search enable: ",self.enableWebSearch)
        if self.enableWebSearch:
            self.ui.checkBox_2.setChecked(False)
            self.ui.checkBox_3.setChecked(True)

            self.enableImgGen = False
            self.enableCOT = True
        else:
            self.enableCOT = self.enableCOTButton or (self.imgList != [] or self.fileList != [])
            self.ui.checkBox_3.setChecked(self.enableCOT)

    def enable_web_search_advanced(self):
        self.enableWebSearchAdvanced = not self.enableWebSearchAdvanced
        self.ui.checkBox.setChecked(False)
        if self.enableWebSearchAdvanced:
            self.enableWebSearch = True

            self.enableCOT = True
            self.ui.checkBox_3.setChecked(True)

            self.ui.checkBox_2.setChecked(False)
            self.enableImgGen = False
        else:
            self.enableCOT = self.enableCOTButton or (self.imgList != [] or self.fileList != [])
            self.ui.checkBox_3.setChecked(self.enableCOT)
            self.enableWebSearch = False


    def enable_img_gen(self):
        self.enableImgGen = not self.enableImgGen
        print("image generation enable: ", self.enableImgGen)
        self.ui.checkBox.setChecked(False)
        self.ui.checkBox_4.setChecked(False)
        self.enableWebSearch = False
        self.enableWebSearchAdvanced = False

        isEnable = self.enableCOTButton or self.enableCOT
        #print("Button: ", self.enableCOTButton, ",state: ", self.enableCOT)
        self.ui.checkBox_3.setChecked(isEnable)

    def enable_cot(self):
        self.enableCOTButton = not self.enableCOTButton
        self.enableCOT = (self.imgList != [] or self.fileList != []) or self.enableWebSearch
        isEnable = self.enableCOTButton or self.enableCOT
        print("Button: ",self.enableCOTButton, ",state: ",self.enableCOT)
        self.ui.checkBox_3.setChecked(isEnable)

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
            print(f"Error loading UI for Chat Window: {e}")


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


def get_array_from_str(text):
    import ast

    array_str = extract_first_string_array(text)
    if array_str:
        try:
            # Convert the string representation to actual Python list
            return ast.literal_eval(array_str)
        except (SyntaxError, ValueError):
            # If parsing fails, return the string version
            return array_str
    return None


def extract_first_string_array(text):

    match = re.search(r'\[.*?\]', text)
    if match:
        return match.group(0)
    return None