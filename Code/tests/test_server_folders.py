"""The server folder picker (Widgets/serverFolders.py), against a fake /v1/ubuntu/folders.

Run from Code/:  python -m pytest -q tests
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from Widgets.serverFolders import ServerFolderPicker, fetch_folders  # noqa: E402

TOKEN = "s3cret"
TREE = {None: ["/home/me", "/mnt/nas/REA"], "/home/me": ["/home/me/proj", "/home/me/notes"],
        "/home/me/proj": [], "/home/me/notes": [], "/mnt/nas/REA": []}


@pytest.fixture
def server():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            path = (parse_qs(url.query).get("path") or [None])[0]
            if self.headers.get("X-ChatUI-Token") != TOKEN:
                code, body = 401, {"error": {"message": "Wrong or missing X-ChatUI-Token"}}
            elif url.path != "/v1/ubuntu/folders":
                code, body = 404, {}
            elif path not in TREE:
                code, body = 400, {"error": {"message": f"{path} is outside the folders these tools may use"}}
            else:
                code, body = 200, {"path": path, "folders": [{"name": p if path is None else p.rsplit("/", 1)[1],
                                                              "path": p} for p in TREE[path]]}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_fetch_folders(server):
    assert [f["path"] for f in fetch_folders(server, TOKEN)["folders"]] == TREE[None]
    assert [f["name"] for f in fetch_folders(server, TOKEN, "/home/me")["folders"]] == ["proj", "notes"]
    with pytest.raises(RuntimeError, match="outside"):
        fetch_folders(server, TOKEN, "/etc")
    with pytest.raises(RuntimeError, match="401"):
        fetch_folders(server, "wrong")
    with pytest.raises(RuntimeError, match="Settings > General"):
        fetch_folders(server, "")


def wait_for(app, done, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert done()


def test_picker_loads_lazily_and_returns_the_chosen_folder(app, server):
    picker = ServerFolderPicker(server, TOKEN, linked=["/home/me/notes"])
    tree = picker.tree
    wait_for(app, lambda: tree.topLevelItemCount() == 2)
    home = tree.topLevelItem(0)
    assert home.text(0) == "/home/me" and home.child(0).text(0) == "Loading…"
    home.setExpanded(True)
    wait_for(app, lambda: home.childCount() == 2 and home.child(0).text(0) == "proj")
    notes = home.child(1)
    assert "(linked)" in notes.text(0)
    tree.setCurrentItem(notes)
    ok = picker.buttons.buttons()[0]
    assert not ok.isEnabled()                  # already linked
    tree.setCurrentItem(home.child(0))
    assert ok.isEnabled()
    picker._accept_item(home.child(0))
    assert picker.chosen == "/home/me/proj"


def test_picker_shows_why_it_cant_list(app, server):
    picker = ServerFolderPicker(server, "wrong")
    wait_for(app, lambda: "401" in picker.status.text())
    assert picker.tree.topLevelItemCount() == 0
