"""General settings: what belongs to the whole program, not to one chat.

The GitHub sign-in: there's one login on this PC, shared by every chat. Whether
a chat's model may use git is still its own choice, under Git in the settings
window of the chat or its personality.

The Ubuntu server's tool token: reverse engineering and the Ubuntu file and run
tools run on the server, which refuses them without it (Server/ollama/README.md says
where to find it). It's kept in the saved network settings (paths.py); each
chat still turns those tools on for itself.
"""

import threading

import requests

import paths
from local_tools import github_login
from local_tools.remote import TOKEN_HEADER

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QVBoxLayout,
                               QWidget)

STYLE = """QWidget{background:rgb(44, 44, 44);color:rgb(255, 255, 255)}
QPushButton{background:rgb(57, 57, 57);color:rgb(255, 255, 255);border:1px solid rgb(90, 90, 90);padding:3px 10px}
QGroupBox{border:1px solid rgb(80, 80, 80);margin-top:14px;padding-top:6px;font-weight:700}
QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px}"""


class GeneralSettings(QMainWindow):
    _github_checked = Signal(object)    # the signed in user name or None, from the checking thread
    _token_checked = Signal(bool, str)  # whether the server took the token, and what to show

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("General Settings")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(460)
        self._github = None             # the GitHub sign-in window, made when first opened

        self.github_label = QLabel("")
        self.github_label.setTextFormat(Qt.PlainText)
        self.github_label.setToolTip("The GitHub account git is signed in with")
        self.github_button = QPushButton("GitHub…")
        self.github_button.setToolTip("Sign in to GitHub so git can pull and push")
        self.github_button.clicked.connect(self.github_login)
        row = QHBoxLayout()
        row.addWidget(self.github_label, 1)
        row.addWidget(self.github_button)
        note = QLabel("One login for every chat. Each chat or personality chooses whether its model may use git.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8a8a8a;font-weight:400")
        group = QVBoxLayout()
        group.addLayout(row)
        group.addWidget(note)
        box = QGroupBox("GitHub")
        box.setLayout(group)

        self.token_edit = QLineEdit(paths.load_tool_token())
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("The server's tool token")
        self.token_edit.setToolTip("On the server: cat ~/.config/chatui/tool_token (or TOOL_TOKEN in Server/ollama/.env)")
        self.token_edit.editingFinished.connect(self.save_token)
        self.token_button = QPushButton("Test")
        self.token_button.setToolTip("Save the token and ask the server whether it takes it")
        self.token_button.clicked.connect(self.check_token)
        self.token_label = QLabel("")
        self.token_label.setTextFormat(Qt.PlainText)
        self.token_label.setWordWrap(True)
        token_row = QHBoxLayout()
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(self.token_button)
        token_note = QLabel("Reverse engineering and the Ubuntu file and run tools run on the server, which needs "
                            "this token. Each chat turns them on in its own settings.")
        token_note.setWordWrap(True)
        token_note.setStyleSheet("color:#8a8a8a;font-weight:400")
        token_group = QVBoxLayout()
        token_group.addLayout(token_row)
        token_group.addWidget(self.token_label)
        token_group.addWidget(token_note)
        server_box = QGroupBox("Ubuntu server")
        server_box.setLayout(token_group)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(box)
        layout.addWidget(server_box)
        layout.addStretch(1)
        self.setCentralWidget(root)
        self._github_checked.connect(self._show_github_user)
        self._token_checked.connect(self._show_token_state)

    def open(self):
        self.check_github()
        self.show()
        self.raise_()
        self.activateWindow()

    def github_login(self):
        from Widgets.githubLogin import GitHubLogin
        if self._github is None:
            self._github = GitHubLogin(self)
            self._github.user_changed.connect(self._show_github_user)
        self._github.show()
        self._github.raise_()
        self._github.activateWindow()

    def check_github(self):
        """Show who git is signed in as. Asking git can take a while, so it runs on a thread."""
        self.github_label.setStyleSheet("color:#8a8a8a;font-weight:400")
        self.github_label.setText("Checking GitHub…")

        def job():
            try:
                user = github_login.signed_in_user()
            except github_login.LoginError:
                user = None
            try:
                self._github_checked.emit(user)
            except RuntimeError:               # the window is gone
                pass

        threading.Thread(target=job, daemon=True).start()

    def _show_github_user(self, user):
        self.github_label.setStyleSheet(("color:#7fd88f" if user else "color:#8a8a8a") + ";font-weight:400")
        self.github_label.setText(f"✓ Connected as {user}" if user else "Not signed in")

    # ------------------------------------------------------------------
    # The server's tool token
    # ------------------------------------------------------------------

    def save_token(self):
        if self.token_edit.text().strip() != paths.load_tool_token():
            paths.save_tool_token(self.token_edit.text())
            self.token_label.setText("")

    def check_token(self):
        self.save_token()
        token = paths.load_tool_token()
        server = paths.load_network().get("llm_server", {})
        url = f"http://{server.get('ip') or '127.0.0.1'}:{server.get('port') or 8100}"
        self._show_token_state(None, "Checking…")

        def job():
            try:
                r = requests.get(f"{url}/v1/ubuntu/tools", headers={TOKEN_HEADER: token}, timeout=6)
                if r.status_code == 200:
                    ok, text = True, f"✓ The server at {url} takes this token"
                elif r.status_code == 404:
                    ok, text = False, f"The server at {url} is older than this app and has no server tools"
                else:
                    try:
                        msg = r.json()["error"]["message"]
                    except Exception:
                        msg = r.text[:200]
                    ok, text = False, f"Refused ({r.status_code}): {msg}"
            except requests.RequestException as e:
                ok, text = False, f"Can't reach the server at {url}: {e}"
            try:
                self._token_checked.emit(ok, text)
            except RuntimeError:               # the window is gone
                pass

        threading.Thread(target=job, daemon=True).start()

    def _show_token_state(self, ok, text):
        color = "#8a8a8a" if ok is None else "#7fd88f" if ok else "#e08a8a"
        self.token_label.setStyleSheet(f"color:{color};font-weight:400")
        self.token_label.setText(text)
