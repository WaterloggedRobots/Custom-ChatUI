"""The run_in_sandbox tool (local_tools/sandbox.py), against a fake LLM server.

Run from Code/:  python -m pytest -q tests
"""

import base64
import io
import json
import os
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools import ToolBox  # noqa: E402
from local_tools import sandbox  # noqa: E402


class FakeServer:
    def __init__(self, status=200, reply=None):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                data = json.dumps(reply if reply is not None else {
                    "exit_code": 0, "output": "hi\n", "timed_out": False, "truncated": False, "seconds": 0.4,
                    "command": "python3 main.py", "timeout": 60}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "proj" / "pkg").mkdir(parents=True)
    (tmp_path / "proj" / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "proj" / "pkg" / "util.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "proj" / ".git").mkdir()
    (tmp_path / "proj" / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "proj" / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "proj" / "node_modules" / "x" / "i.js").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("n", encoding="utf-8")
    return tmp_path


def make_box(tmp_path, server):
    box = ToolBox(working_dir=str(tmp_path), server_url=lambda: server.url)
    box.set_groups(["run"])
    return box


def test_sandbox_tools_are_in_the_run_group_and_need_no_approval(tmp_path):
    box = ToolBox(working_dir=str(tmp_path))
    assert not box.needs_approval("run_in_sandbox") and not box.needs_approval("reset_sandbox")
    assert box.needs_approval("run_command")
    box.set_groups(["files"])
    assert "run_in_sandbox" not in {s["function"]["name"] for s in box.schemas()}
    assert "run_in_sandbox" not in box.system_note()
    box.set_groups(["run"])
    assert "run_in_sandbox" in {s["function"]["name"] for s in box.schemas()}
    assert "run_in_sandbox" in box.system_note()


def test_run_sends_code_with_this_chats_session(project):
    server = FakeServer()
    box = make_box(project, server)
    out = box.run("run_in_sandbox", {"code": "print('hi')", "language": "python"})
    assert out.startswith("exit code 0 after 0.4s") and out.endswith("hi")
    path, body = server.requests[0]
    assert path == "/v1/sandbox/run"
    assert body["session"] == box.sandbox_session and body["code"] == "print('hi')"
    assert "archive" not in body
    assert ToolBox().sandbox_session != box.sandbox_session      # each chat its own container


def test_copy_from_pc_packs_folders_without_git_or_node_modules(project):
    server = FakeServer()
    box = make_box(project, server)
    box.run("run_in_sandbox", {"command": "python3 proj/app.py", "copy_from_pc": ["proj", "notes.txt"]})
    archive = base64.b64decode(server.requests[0][1]["archive"])
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = sorted(tar.getnames())
    assert names == ["notes.txt", "proj/app.py", "proj/pkg/util.py"]


def test_copy_skips_big_files_and_refuses_missing(project, monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_FILE_BYTES", 5)
    server = FakeServer()
    box = make_box(project, server)
    out = box.run("run_in_sandbox", {"command": "ls", "copy_from_pc": ["proj"]})
    assert "Not copied" in out and "app.py" in out
    assert "doesn't exist" in box.run("run_in_sandbox", {"command": "ls", "copy_from_pc": ["nope"]})


def test_timeouts_and_server_errors_reach_the_model(project):
    server = FakeServer(reply={"exit_code": 124, "output": "", "timed_out": True, "truncated": False,
                               "seconds": 2.0, "command": "sleep 9", "timeout": 2})
    out = make_box(project, server).run("run_in_sandbox", {"command": "sleep 9", "timeout": 2})
    assert "longer than 2s" in out and "(no output)" in out

    server = FakeServer(status=503, reply={"error": {"message": "sandbox not available: Docker isn't installed"}})
    out = make_box(project, server).run("run_in_sandbox", {"code": "1"})
    assert out == "Error: sandbox: sandbox not available: Docker isn't installed"


def test_reset(project):
    server = FakeServer(reply={"status": "reset"})
    box = make_box(project, server)
    assert "empty again" in box.run("reset_sandbox", {})
    assert server.requests[0] == ("/v1/sandbox/reset", {"session": box.sandbox_session})


def test_summary_line(project):
    box = make_box(project, FakeServer())
    assert box.describe("run_in_sandbox", {"code": "import os\nprint(1)"}) == \
        "Run in the Ubuntu sandbox (python):\nimport os"
    assert box.describe("run_in_sandbox", {"command": "pytest", "copy_from_pc": ["proj"]}) == \
        "Run in the Ubuntu sandbox (copying proj):\npytest"
    assert box.describe("run_in_sandbox", {"command": "ls"}) == "Run in the Ubuntu sandbox:\nls"
