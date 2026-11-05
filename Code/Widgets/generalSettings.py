"""General settings: what belongs to the whole program, not to one chat.

For now that's the GitHub sign-in: there's one login on this PC, shared by every
chat. Whether a chat's model may use git is still its own choice, under Git in the
settings window of the chat or its personality.
"""

import threading

from local_tools import github_login

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

STYLE = """QWidget{background:rgb(44, 44, 44);color:rgb(255, 255, 255)}
QPushButton{background:rgb(57, 57, 57);color:rgb(255, 255, 255);border:1px solid rgb(90, 90, 90);padding:3px 10px}
QGroupBox{border:1px solid rgb(80, 80, 80);margin-top:14px;padding-top:6px;font-weight:700}
QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px}"""


class GeneralSettings(QMainWindow):
    _github_checked = Signal(object)    # the signed in user name or None, from the checking thread

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

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(box)
        layout.addStretch(1)
        self.setCentralWidget(root)
        self._github_checked.connect(self._show_github_user)

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
