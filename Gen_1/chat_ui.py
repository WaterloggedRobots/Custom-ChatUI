import sys
import json
import threading
import requests
import markdown

from pathlib import Path

from PyQt6 import uic
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QInputDialog, QFileDialog
)
from PySide6.QtCore import Qt, Signal, QObject

from PySide6.QtGui import QTextCursor


# ======================
# CONFIG
# ======================
VLLM_URL = "http://192.168.0.247:8000/v1/chat/completions"  # CHANGE THIS
MODEL_NAME = "openai/gpt-oss-20b"
MAX_CONTEXT_MESSAGES = 16      # keep last N messages verbatim
SUMMARY_TRIGGER_COUNT = 24     # when total messages exceed this
SUMMARY_MODEL_MAX_TOKENS = 512

# ======================
# Streaming signal bridge
# ======================
class StreamEmitter(QObject):
    token = Signal(str)
    done = Signal()

# ======================
# Main UI
# ======================
class ChatWindow(QWidget):
    def __init__(self):
        super().__init__()

        dark_style = """
        /* Generic widget */
        QWidget {
            background:#2b2b2b;
            color:#e0e0e0;
        }

        /* Menus */
        QMenuBar { background:#2b2b2b; }
        QMenuBar::item:selected { background:#3c3c3c; }
        QMenu { background:#2b2b2b; }
        QMenu::item:selected { background:#3c3c3c; }

        /* Buttons */
        QPushButton {
            background-color:#3c3c3c;
            border:1px solid #555;
            border-radius:4px;
            padding:4px 8px;
            color:#e0e0e0;
        }
        QPushButton:hover {
            background-color:#4c4c4c;
        }
        QPushButton:pressed {
            background-color:#2c2c2c;
        }

        /* Line edit */
        QLineEdit {
            background:#3c3c3c;
            border:1px solid #555;
            border-radius:4px;
            padding:4px;
            color:#e0e0e0;
        }

        """
        app.setStyleSheet(dark_style)

        self.setWindowTitle("ChatUI")
        self.resize(700, 500)

        self.layout = QVBoxLayout(self)

        self.chat = QTextEdit()
        self.chat.setReadOnly(True)

        self.input = QTextEdit()
        self.input.setPlaceholderText("Type your message…")
        self.input.setFixedHeight(80)
        # --
        btn_row = QHBoxLayout()
        self.layout.addLayout(btn_row)

        self.edit_btn = QPushButton("Edit last prompt")
        self.delete_btn = QPushButton("Delete last prompt")
        # --

        self.send_btn = QPushButton("Send")
        self.status = QLabel("Idle")

        self.save_btn = QPushButton("💾 Save Chat")
        self.load_btn = QPushButton("📂 Load Chat")

        self.layout.addWidget(self.chat)
        self.layout.addWidget(self.input)
        self.layout.addWidget(self.send_btn)
        btn_row.addWidget(self.edit_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.load_btn)
        self.layout.addWidget(self.status)

        self.send_btn.clicked.connect(self.send_message)
        #self.input.returnPressed.connect(self.send_message)

        self.emitter = StreamEmitter()
        self.emitter.token.connect(self.append_token)
        self.emitter.done.connect(self.finish_response)

        self.save_btn.clicked.connect(self.save_chat_manual)
        self.load_btn.clicked.connect(self.load_chat_manual)

        self.edit_btn.clicked.connect(self.edit_last_prompt)
        self.delete_btn.clicked.connect(self.delete_last_prompt)

        self.temperature = 0.7
        self.messages = []
        self.display_map=[]
        self.current_response = ""

        self.history_file = Path("chat_history.json")
        self.chat_history = []

        self.assistant_block_cursor = None

    def send_message(self):
        text = self.input.toPlainText().strip()
        self.input.clear()

        if not text:
            return

        self.input.clear()
        self.send_btn.setEnabled(False)
        self.status.setText("Generating…")

        self.messages.append({"role": "user", "content": text})
        self.refresh_chat_display()

        self.current_response = ""
        #self.chat.append("<b>Model:</b> ")
        self.chat.moveCursor(QTextCursor.End)

        print(repr(text))

        threading.Thread(
            target=self.stream_request,
            args=(text,),
            daemon=True
        ).start()

    def refresh_chat_display(self):
        self.chat.clear()
        self.display_map.clear()

        for i, msg in enumerate(self.messages):
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                self.chat.append(f"<b>You:</b> {content}")
                self.display_map.append(i)


            elif role == "assistant":
                html = self.render_markdown(content)
                self.chat.insertHtml(f"<b>Model:</b><br>{html}<br><br>")
                self.display_map.append(i)

        self.chat.append("")  # spacer

    def stream_request(self, prompt):
        #self.messages.append({"role": "user", "content": prompt})

        payload = {
            "model": MODEL_NAME,
            "messages": self.get_context_messages(),
            "stream": True,
            "temperature": self.temperature,
        }

        try:
            with requests.post(
                VLLM_URL,
                json=payload,
                stream=True,
                timeout=600
            ) as r:
                for line in r.iter_lines():
                    if not line:
                        continue

                    if line.startswith(b"data: "):
                        data = line[6:]
                        if data == b"[DONE]":
                            break

                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"]
                        if "content" in delta:
                            self.emitter.token.emit(delta["content"])

        except Exception as e:
            self.emitter.token.emit(f"\n[ERROR] {e}\n")

        self.emitter.done.emit()

    def append_token(self, text):
        self.current_response += text

    def finish_response(self):
        # Compact history after response
        if len(self.messages) >= SUMMARY_TRIGGER_COUNT:
            context = self.get_context_messages()

            # Extract system summary + recent messages
            self.messages = context

        html = self.render_markdown(self.current_response)

        self.chat.insertHtml(f"<b>\nModel:</b><br>{html}<br><br>")

        self.messages.append({
            "role": "assistant",
            "content": self.current_response
        })

        self.status.setText("Idle")
        self.send_btn.setEnabled(True)

    def delete_last_prompt(self):
        if len(self.messages) < 2:
            return

        # Remove last assistant + user
        self.messages = self.messages[:-2]
        self.refresh_chat_display()

    def edit_last_prompt(self):
        if len(self.messages) < 2:
            return

        last_user_index = len(self.messages) - 2
        old_text = self.messages[last_user_index]["content"]

        new_text, ok = QInputDialog.getMultiLineText(
            self,
            "Edit prompt",
            "Edit your message:",
            old_text
        )

        if not ok or not new_text.strip():
            return

        # Truncate conversation
        self.messages = self.messages[:last_user_index]
        self.messages.append({"role": "user", "content": new_text})

        self.refresh_chat_display()
        self.send_btn.setEnabled(False)
        self.status.setText("Regenerating…")

        self.current_response = ""

        threading.Thread(
            target=self.stream_request,
            args=(new_text,),
            daemon=True
        ).start()

    def save_chat_manual(self):
        if not self.messages:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save chat history",
            str(Path.home() / "chat_history.json"),
            "JSON Files (*.json)"
        )

        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, indent=2, ensure_ascii=False)

            self.status.setText(f"Saved to {file_path}")

        except Exception as e:
            self.status.setText(f"Save failed: {e}")

    def load_chat_manual(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load chat history",
            str(Path.home()),
            "JSON Files (*.json)"
        )

        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            # Basic validation
            if not isinstance(loaded, list):
                raise ValueError("Invalid chat file")

            for msg in loaded:
                if "role" not in msg or "content" not in msg:
                    raise ValueError("Malformed message entry")

            self.messages = loaded
            self.current_response = ""
            self.refresh_chat_display()
            self.status.setText(f"Loaded from {file_path}")

        except Exception as e:
            self.status.setText(f"Load failed: {e}")

    def get_context_messages(self):
        """
        Returns messages trimmed and summarized if needed.
        """
        if len(self.messages) < SUMMARY_TRIGGER_COUNT:
            return self.messages

        # Split old vs recent
        old_messages = self.messages[:-MAX_CONTEXT_MESSAGES]
        recent_messages = self.messages[-MAX_CONTEXT_MESSAGES:]

        try:
            summary_text = self.summarize_messages(old_messages)
        except Exception as e:
            # Fail safe: if summarization fails, just trim
            return recent_messages

        summary_message = {
            "role": "system",
            "content": f"Conversation summary:\n{summary_text}"
        }

        return [summary_message] + recent_messages

    def summarize_messages(self, messages):
        """
        Summarize a list of messages into a single system message.
        """
        prompt = [
            {
                "role": "system",
                "content": (
                    "Summarize the following conversation briefly but precisely. "
                    "Preserve user goals, constraints, technical details, and decisions. "
                    "Do NOT include irrelevant chatter."
                )
            }
        ]

        for m in messages:
            prompt.append(m)

        payload = {
            "model": MODEL_NAME,
            "messages": prompt,
            "temperature": 0.3,
            "max_tokens": SUMMARY_MODEL_MAX_TOKENS,
            "stream": False
        }

        r = requests.post(VLLM_URL, json=payload, timeout=300)
        r.raise_for_status()

        return r.json()["choices"][0]["message"]["content"]

    def render_markdown(self, text: str) -> str:
        html = markdown.markdown(
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
            {html}
        </div>
        """

    def keyPressEvent(self, event):
        if self.input.hasFocus():
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if event.modifiers() & Qt.ShiftModifier:
                    self.input.insertPlainText("\n")
                else:
                    self.send_message()
                return
        super().keyPressEvent(event)


# ======================
# Entry point
# ======================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ChatWindow()
    win.show()
    sys.exit(app.exec())
