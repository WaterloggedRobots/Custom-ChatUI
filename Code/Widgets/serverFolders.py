"""Pick a folder on the Ubuntu server to link to a chat, for the ubuntu_* tools.

The server shows only what its tools may use (TOOL_ROOTS, without TOOL_DENY;
see Server/ollama/chat_server/ubuntu.py, GET /v1/ubuntu/folders), so nothing is
mounted on this PC: the tree is loaded from the server a folder at a time, as
it's opened. The chat keeps the folders it links, and the server keeps that
chat's ubuntu_* tools inside them.
"""

import threading

import requests

from local_tools.remote import TOKEN_HEADER

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

STYLE = """QWidget{background:rgb(44, 44, 44);color:rgb(255, 255, 255)}
QPushButton{background:rgb(57, 57, 57);color:rgb(255, 255, 255);border:1px solid rgb(90, 90, 90);padding:3px 10px}
QTreeWidget{border:1px solid rgb(80, 80, 80)}"""
PATH_ROLE = Qt.UserRole
LOADING = "Loading…"


def fetch_folders(url: str, token: str, path=None) -> dict:
    """The server's answer for one folder (or the roots); RuntimeError with something to show if it fails."""
    if not token:
        raise RuntimeError("No server tool token is set (Settings > General > Ubuntu server)")
    try:
        r = requests.get(f"{url}/v1/ubuntu/folders", params={"path": path} if path else None,
                         headers={TOKEN_HEADER: token}, timeout=8)
    except requests.RequestException as e:
        raise RuntimeError(f"Can't reach the server at {url}: {e}")
    if r.status_code == 404:
        raise RuntimeError(f"The server at {url} is older than this app and can't list its folders; update it")
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"The server refused ({r.status_code}): {msg}")
    return r.json()


class ServerFolderPicker(QDialog):
    _loaded = Signal(object, object, str)   # the path asked for (None = roots), the answer, an error

    def __init__(self, url: str, token: str, linked=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Link a folder on the Ubuntu server")
        self.setStyleSheet(STYLE)
        self.resize(520, 460)
        self.url, self.token, self.linked = url, token, set(linked)
        self.chosen = None
        self._items = {}                    # path -> its item, for answers that come back later

        note = QLabel("Folders the server lets its tools use. The chat's ubuntu_* tools will stay inside "
                      "the folders you link.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8a8a8a")
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(lambda item, _: self._accept_item(item))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Link")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(lambda: self._accept_item(self.tree.currentItem()))
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.buttons)

        self._loaded.connect(self._fill)
        self._load(None)

    # ------------------------------------------------------------ loading

    def _load(self, path):
        self.status.setText("Asking the server…")

        def job():
            try:
                body, error = fetch_folders(self.url, self.token, path), ""
            except RuntimeError as e:
                body, error = None, str(e)
            try:
                self._loaded.emit(path, body, error)
            except RuntimeError:            # the window is gone
                pass

        threading.Thread(target=job, daemon=True).start()

    def _fill(self, path, body, error):
        parent = self._items.get(path) if path else self.tree.invisibleRootItem()
        if parent is None:
            return
        for i in reversed(range(parent.childCount())):
            if parent.child(i).data(0, PATH_ROLE) is None:      # the Loading… placeholder
                parent.removeChild(parent.child(i))
        if error:
            self.status.setText(error)
            if path:
                parent.setData(0, Qt.UserRole + 1, False)        # let it be tried again
            return
        self.status.setText("" if body["folders"] or path else "The server allows no folders (TOOL_ROOTS).")
        for f in body["folders"]:
            linked = f["path"] in self.linked
            item = QTreeWidgetItem([f["name"] + ("   (linked)" if linked else "")])
            item.setData(0, PATH_ROLE, f["path"])
            item.setToolTip(0, f["path"])
            if linked:
                item.setForeground(0, Qt.gray)
            QTreeWidgetItem(item, [LOADING])                      # shows the arrow until it's opened
            parent.addChild(item)
            self._items[f["path"]] = item

    def _expand(self, item):
        if item.data(0, Qt.UserRole + 1):
            return
        item.setData(0, Qt.UserRole + 1, True)
        self._load(item.data(0, PATH_ROLE))

    # ------------------------------------------------------------ choosing

    def _selected(self):
        item = self.tree.currentItem()
        path = item.data(0, PATH_ROLE) if item else None
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(path) and path not in self.linked)

    def _accept_item(self, item):
        path = item.data(0, PATH_ROLE) if item else None
        if path and path not in self.linked:
            self.chosen = path
            self.accept()
