"""Tests of the PC tools and the client's tool loop.

Run from Code/:  python -m pytest -q tests
(The terminal tool uses bash outside Windows, so this also runs on Linux.)
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools import ToolBox  # noqa: E402


@pytest.fixture
def box(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / "notes" / "b.py").write_text("print('beta')\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0\1\2")
    (tmp_path / ".git").mkdir()          # skipped by recursive listings (not a real repo)
    return ToolBox(working_dir=str(tmp_path))


def test_read_tools_need_no_approval_and_writes_do(box):
    assert not box.needs_approval("read_file")
    assert box.needs_approval("write_file") and box.needs_approval("run_command") and box.needs_approval("git_commit")
    box.always_allow.add("write_file")
    assert not box.needs_approval("write_file")
    names = {s["function"]["name"] for s in box.schemas()}
    assert {"list_directory", "read_file", "edit_file", "git_status", "git_commit", "run_command"} <= names


def test_list_and_find_and_search(box, tmp_path):
    out = box.run("list_directory", {})
    assert "notes" + os.sep in out and "blob.bin" in out
    (tmp_path / ".git" / "config").write_text("x")
    deep = box.run("list_directory", {"recursive": True})
    assert os.path.join("notes", "a.txt") in deep and os.path.join(".git", "config") not in deep
    assert "b.py" in box.run("find_files", {"pattern": "*.py"})
    hits = box.run("search_in_files", {"query": "BETA"})
    assert os.path.join("notes", "a.txt") + ":2: beta" in hits and "b.py:1:" in hits
    assert "does not exist" in box.run("list_directory", {"path": "nope"})


def test_read_file_with_lines_and_parts(box):
    out = box.run("read_file", {"path": "notes/a.txt", "start_line": 2, "max_lines": 1})
    assert "(lines 2-2 of 3)" in out and "    2  beta" in out and "start_line=3" in out
    assert "binary" in box.run("read_file", {"path": "blob.bin"})


def test_documents_go_through_the_extractor(box, tmp_path):
    (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 fake")
    box.extract_text = lambda p: f"text of {p.name}"
    assert "text of r.pdf" in box.run("read_file", {"path": "r.pdf"})


def test_write_edit_move_delete(box, tmp_path):
    assert "Wrote" in box.run("write_file", {"path": "new/x.txt", "content": "one\ntwo\n"})
    assert "already exists" in box.run("write_file", {"path": "new/x.txt", "content": "z"})
    assert "Edited" in box.run("edit_file", {"path": "new/x.txt", "old_text": "two", "new_text": "TWO"})
    assert (tmp_path / "new" / "x.txt").read_text() == "one\nTWO\n"
    assert "not found" in box.run("edit_file", {"path": "new/x.txt", "old_text": "zzz", "new_text": "y"})
    assert "Moved" in box.run("move_path", {"source": "new/x.txt", "destination": "notes"})
    assert (tmp_path / "notes" / "x.txt").exists()
    assert "Copied" in box.run("copy_path", {"source": "notes", "destination": "notes2"})
    assert "recursive=true" in box.run("delete_path", {"path": "notes2"})
    assert "Deleted" in box.run("delete_path", {"path": "notes2", "recursive": True})
    assert not (tmp_path / "notes2").exists()


def test_edit_keeps_windows_line_endings(box, tmp_path):
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"one\r\ntwo\r\n")
    box.run("edit_file", {"path": "crlf.txt", "old_text": "one\ntwo", "new_text": "1\n2"})
    assert f.read_bytes() == b"1\r\n2\r\n"


def test_change_directory_moves_relative_paths(box, tmp_path):
    assert "Working folder is now" in box.run("change_directory", {"path": "notes"})
    assert box.working_dir == tmp_path / "notes"
    assert "alpha" in box.run("read_file", {"path": "a.txt"})


def test_terminal(box):
    out = box.run("run_command", {"command": "echo hi && exit 3"})
    assert out.startswith("exit code 3") and "hi" in out
    assert "stopped after 1s" in box.run("run_command", {"command": "sleep 5", "timeout": 1})


def test_git_tools(box, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "f.txt").write_text("1\n")
    assert "?? f.txt" in box.run("git_status", {"repo": "repo"})
    assert "A  f.txt" in box.run("git_add", {"files": ["f.txt"], "repo": "repo"})
    assert "first" in box.run("git_commit", {"message": "first", "repo": "repo"})
    (repo / "f.txt").write_text("2\n")
    assert "+2" in box.run("git_diff", {"repo": "repo"})
    assert "first" in box.run("git_log", {"repo": "repo"})
    box.run("git_switch", {"branch": "dev", "create": True, "repo": "repo"})
    assert "* dev" in box.run("git_branches", {"repo": "repo"})
    assert "1 file changed" in box.run("git_command", {"args": ["diff", "--stat"], "repo": "repo"})
    assert "not a git repository" in box.run("git_status", {"repo": "notes"}).lower()


# ----------------------------------------------------------- client loop

class FakeServer:
    """Answers /v1/chat/completions like the ChatUI server: first a tool call, then text."""

    def __init__(self, calls):
        self.calls = calls          # tool calls to make, one per round
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                n = sum(1 for m in body["messages"] if m["role"] == "tool")
                chunks = []
                if n < len(outer.calls) and body.get("tools"):
                    call = outer.calls[n]
                    chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "id": f"call_{n}", "type": "function",
                         "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}}]})
                    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
                else:
                    chunks.append({"choices": [{"index": 0, "delta": {"content": "All done."}}]})
                    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})
                data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(data.encode())

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]


def run_client(tmp_path, calls, answer):
    import time
    import llmClient
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])   # signals from the worker need an event loop
    server = FakeServer(calls)
    client = llmClient.LLMClient()
    client.server_ip, client.server_port = "127.0.0.1", server.port
    client.set_model("gemma4:31b")
    client.toolbox.working_dir = tmp_path
    asked, events, out = [], [], {}
    finished = threading.Event()
    client.approval_needed.connect(lambda req: (asked.append(req), client.answer_approval(answer)))
    client.tool_event.connect(events.append)
    client.done.connect(lambda r: (out.update(r), finished.set()))
    client.error.connect(lambda e: (out.update(error=e), finished.set()))
    client.generate([{"role": "user", "content": "go"}])
    deadline = time.time() + 20
    while not finished.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert finished.is_set()
    server.httpd.shutdown()
    return server, out, asked, events


def test_client_runs_reads_without_asking_and_sends_results_back(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there\n")
    server, out, asked, _ = run_client(tmp_path, [("read_file", {"path": "hello.txt"})], "deny")
    assert "error" not in out and out["content"] == "All done."
    assert asked == []
    assert len(server.requests) == 2
    second = server.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert second[-1]["role"] == "tool" and "hi there" in second[-1]["content"]
    assert any("working folder" in m["content"] for m in second if m["role"] == "system")
    assert out["tools"][0]["state"] == "ok"
    assert out["usage"]["total_tokens"] == 5


def test_client_asks_before_writing_and_respects_deny(tmp_path):
    server, out, asked, _ = run_client(tmp_path, [("write_file", {"path": "x.txt", "content": "y"})], "deny")
    assert len(asked) == 1 and "Create x.txt" in asked[0]["summary"]
    assert not (tmp_path / "x.txt").exists()
    assert out["tools"][0]["state"] == "declined"
    assert "declined" in server.requests[1]["messages"][-1]["content"]


def test_client_writes_when_allowed(tmp_path):
    _, out, asked, _ = run_client(tmp_path, [("write_file", {"path": "x.txt", "content": "y"}),
                                             ("run_command", {"command": "cat x.txt"})], "allow")
    assert len(asked) == 2
    assert (tmp_path / "x.txt").read_text() == "y"
    assert [t["state"] for t in out["tools"]] == ["ok", "ok"]
    assert "exit code 0" in out["tools"][1]["output"]
