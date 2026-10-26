"""The GitHub sign-in window, opened from Chat Settings next to the Git tools.

The work is in local_tools/github_login.py; git calls run on a thread so the
window doesn't freeze while the browser sign-in waits for you.
"""

import threading

from local_tools import github_login

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QPushButton)
from PySide6.QtCore import QObject, Signal, Qt

STYLE = """QWidget{background:rgb(44, 44, 44);color:rgb(255, 255, 255)}
QLineEdit, QPushButton{background:rgb(57, 57, 57);color:rgb(255, 255, 255);border:1px solid rgb(90, 90, 90);padding:3px}
QPushButton:disabled{color:rgb(130, 130, 130)}
QGroupBox{border:1px solid rgb(80, 80, 80);margin-top:14px;padding-top:6px;font-weight:700}
QGroupBox::title{subcontrol-origin:margin;left:8px;padding:0 4px}"""


class _Relay(QObject):
    done = Signal(object, object)       # (callback, result or LoginError)


class GitHubLogin(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GitHub sign-in for git")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(460)
        self._relay = _Relay()
        self._relay.done.connect(lambda callback, result: callback(result))
        self._busy = False

        self.status = QLabel("Checking…")
        self.status.setStyleSheet("font-weight:700")
        self.browser_button = QPushButton("Sign in with browser")
        self.browser_button.setToolTip("Git Credential Manager opens GitHub's sign-in page")
        self.sign_out_button = QPushButton("Sign out")
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.browser_button)
        top.addWidget(self.sign_out_button)

        token_box = QGroupBox("Or use a personal access token")
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("ghp_… or github_pat_…")
        self.token_button = QPushButton("Save token")
        link = QLabel(f'<a style="color:#7ab7ff" href="{github_login.TOKEN_PAGE}">Make a token on GitHub</a> '
                      "(scopes repo and workflow). git keeps it; this app doesn't.")
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.token_edit, 1)
        row.addWidget(self.token_button)
        box = QVBoxLayout(token_box)
        box.addLayout(row)
        box.addWidget(link)

        who_box = QGroupBox("Commits are made as")
        self.name_edit = QLineEdit()
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("the email on your GitHub account, or its noreply address")
        self.identity_button = QPushButton("Save")
        form = QFormLayout(who_box)
        form.addRow("Name", self.name_edit)
        form.addRow("Email", self.email_edit)
        form.addRow("", self.identity_button)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message.setStyleSheet("color:#8a8a8a")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(token_box)
        layout.addWidget(who_box)
        layout.addWidget(self.message)

        self.browser_button.clicked.connect(self.sign_in_browser)
        self.token_button.clicked.connect(self.sign_in_token)
        self.token_edit.returnPressed.connect(self.sign_in_token)
        self.sign_out_button.clicked.connect(self.sign_out)
        self.identity_button.clicked.connect(self.save_identity)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._busy:
            self.refresh()

    # ------------------------------------------------------------------

    def _run(self, work, then, note=""):
        """work() on a thread, then then(result or LoginError) back here."""
        self._busy = True
        for w in (self.browser_button, self.token_button, self.sign_out_button, self.identity_button):
            w.setEnabled(False)
        if note:
            self.message.setText(note)

        def job():
            try:
                result = work()
            except github_login.LoginError as e:
                result = e
            except Exception as e:                 # a bug shouldn't leave the window stuck
                result = github_login.LoginError(f"{type(e).__name__}: {e}")
            self._relay.done.emit(then, result)

        threading.Thread(target=job, daemon=True).start()

    def _finished(self, result, ok_text):
        self._busy = False
        if isinstance(result, github_login.LoginError):
            self.message.setText(f"⚠ {result}")
        elif ok_text:
            self.message.setText(ok_text(result) if callable(ok_text) else ok_text)
        self.refresh(keep_message=True)

    def refresh(self, keep_message=False):
        def work():
            return (github_login.signed_in_user(), github_login.has_credential_manager(),
                    github_login.identity())

        def then(result):
            self._busy = False
            if isinstance(result, github_login.LoginError):
                self.status.setText("git isn't available")
                self.message.setText(f"⚠ {result}")
                return
            user, gcm, (name, email) = result
            self.status.setText(f"Signed in as {user}" if user else "Not signed in")
            self.browser_button.setEnabled(gcm)
            self.browser_button.setToolTip("Git Credential Manager opens GitHub's sign-in page" if gcm else
                                           "Needs Git Credential Manager (part of Git for Windows); use a token")
            self.sign_out_button.setEnabled(bool(user))
            self.token_button.setEnabled(True)
            self.identity_button.setEnabled(True)
            if not self.name_edit.hasFocus():
                self.name_edit.setText(name)
            if not self.email_edit.hasFocus():
                self.email_edit.setText(email)
            if not keep_message:
                self.message.setText("" if name and email else
                                     "git needs a name and email before the model can commit.")

        self._run(work, then)

    def sign_in_browser(self):
        self._run(github_login.browser_login,
                  lambda r: self._finished(r, lambda user: f"Signed in{' as ' + user if user else ''}."),
                  "Finish signing in in the window or browser page that opened…")

    def sign_in_token(self):
        token = self.token_edit.text()
        self.token_edit.clear()
        self._run(lambda: github_login.token_login(token),
                  lambda r: self._finished(r, lambda user: f"Token saved; git signs in as {user}."),
                  "Checking the token with GitHub…")

    def sign_out(self):
        self._run(github_login.sign_out, lambda r: self._finished(r, "Signed out."))

    def save_identity(self):
        name, email = self.name_edit.text(), self.email_edit.text()
        self._run(lambda: github_login.set_identity(name, email),
                  lambda r: self._finished(r, "Saved in git's global settings."))
