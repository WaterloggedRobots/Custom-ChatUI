"""Tests of the tools that run on the Ubuntu server (local_tools/remote.py, rea.py, ubuntu.py),
against a fake chat server: the token, the kinds, the approval line and the system note.

Run from Code/:  python -m pytest -q tests
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_settings  # noqa: E402
import paths  # noqa: E402
from local_tools import ToolBox  # noqa: E402

TOKEN = "s3cret"
REA_TOOLS = {"tools": [{"type": "function", "function": {"name": n, "description": f"REA {n}",
                                                         "parameters": {"type": "object", "properties": {}}}}
                       for n in ("open_binary", "procedure_pseudo_code", "copy_to_targets")],
             "meta": {"open_binary": {"kind": "read", "group": "rea"},
                      "procedure_pseudo_code": {"kind": "read", "group": "rea"},
                      "copy_to_targets": {"kind": "write", "group": "rea"}}}
UBUNTU_TOOLS = {"tools": [{"type": "function", "function": {"name": n, "description": n,
                                                            "parameters": {"type": "object", "properties": {}}}}
                          for n in ("ubuntu_read_file", "ubuntu_delete_path", "ubuntu_mystery")],
                "meta": {"ubuntu_read_file": {"kind": "read", "group": "files"},
                         "ubuntu_delete_path": {"kind": "write", "group": "files"}},
                "roots": ["/home/me", "/mnt/nas/REA"]}


class FakeServer:
    """The chat server: tool lists and calls (token checked) and, for the client tests, chat completions."""

    def __init__(self, chat_calls=()):
        self.seen = []                  # (method, path, token)
        self.calls = []                 # (prefix, name, arguments) the server was asked to run
        self.chat_calls = list(chat_calls)
        self.chat_requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self):
                token = self.headers.get("X-ChatUI-Token")
                outer.seen.append((self.command, self.path, token))
                if token != TOKEN:
                    self._send(401, {"error": {"message": "Wrong or missing X-ChatUI-Token"}})
                    return False
                return True

            def do_GET(self):
                lists = {"/v1/rea/tools": REA_TOOLS, "/v1/ubuntu/tools": UBUNTU_TOOLS}
                if self.path not in lists:
                    return self._send(404, {"error": {"message": "not found"}})
                if self._authorized():
                    self._send(200, lists[self.path])

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path in ("/v1/rea/call", "/v1/ubuntu/call"):
                    if self._authorized():
                        prefix = self.path.split("/")[2]
                        outer.calls.append((prefix, body["name"], body["arguments"]))
                        self._send(200, {"name": body["name"], "output": f"{prefix} ran {body['name']}"})
                    return
                outer.chat_requests.append(body)
                n = sum(1 for m in body["messages"] if m["role"] == "tool")
                if n < len(outer.chat_calls) and body.get("tools"):
                    name, args = outer.chat_calls[n]
                    chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "id": f"call_{n}", "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args)}}]}}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
                else:
                    chunks = [{"choices": [{"index": 0, "delta": {"content": "All done."}}]},
                              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
                data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                self._send(200, data.encode(), "text/event-stream")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.httpd.shutdown()


def make_box(server, token=TOKEN, groups=("rea", "ubuntu_files")):
    box = ToolBox()
    box.server = lambda: (server.url, token)
    box.set_groups(groups)
    return box


def names(box):
    return {s["function"]["name"] for s in box.schemas()}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_server_tools_are_off_by_default_and_for_old_chats():
    s = chat_settings.normalize({})
    assert s["Tool REA"] is False and s["Tool Ubuntu Files"] is False
    assert chat_settings.tool_groups(chat_settings.normalize({"Tools": True})) == {"files", "git", "run"}
    assert chat_settings.tool_groups(chat_settings.from_chat({"Model": "m", "Tools": True})) == {"files", "git", "run"}
    on = chat_settings.normalize({"Tool REA": True, "Tool Ubuntu Files": True, "Tool Files": False,
                                  "Tool Git": False, "Tool Run": False})
    assert chat_settings.tool_groups(on) == {"rea", "ubuntu_files"}
    assert chat_settings.normalize({"Tool REA": "yes"})["Tool REA"] is False


def test_token_is_saved_with_the_network_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "USER_NETWORK_FILE", tmp_path / "network.json")
    assert paths.load_tool_token() == ""
    paths.save_server_address("192.168.0.192", 8100)
    paths.save_tool_token("  abc  ")
    assert paths.load_tool_token() == "abc"
    paths.save_server_address("192.168.0.193", 8100)          # changing the address keeps the token
    assert paths.load_tool_token() == "abc"
    assert paths.load_network()["llm_server"]["ip"] == "192.168.0.193"


# ---------------------------------------------------------------------------
# The tool box
# ---------------------------------------------------------------------------

def test_server_tools_come_from_the_server_with_their_kinds(server):
    box = make_box(server)
    assert names(box) == {"open_binary", "procedure_pseudo_code", "copy_to_targets",
                          "ubuntu_read_file", "ubuntu_delete_path", "ubuntu_mystery"}
    assert "change_directory" not in names(box)       # that belongs to the PC's tools
    assert not box.needs_approval("open_binary") and not box.needs_approval("ubuntu_read_file")
    assert box.needs_approval("copy_to_targets") and box.needs_approval("ubuntu_delete_path")
    assert box.kind("ubuntu_mystery") == "run"        # no kind from the server: ask first
    assert box.describe("ubuntu_delete_path", {"path": "/home/me/x", "recursive": True}) == \
        "On the Ubuntu server: delete /home/me/x and everything in it (to its trash folder)"
    assert box.describe("copy_to_targets", {"source": "COM3D2"}).startswith("On the Ubuntu server: copy COM3D2")
    assert box.describe("procedure_pseudo_code", {"procedure": "main"}) == "On the Ubuntu server: procedure_pseudo_code"


def test_calls_are_forwarded_with_the_token(server):
    box = make_box(server)
    box.schemas()
    assert box.run("procedure_pseudo_code", {"procedure": "main"}) == "rea ran procedure_pseudo_code"
    assert box.run("ubuntu_read_file", {"path": "/home/me/a.txt"}) == "ubuntu ran ubuntu_read_file"
    assert server.calls == [("rea", "procedure_pseudo_code", {"procedure": "main"}),
                            ("ubuntu", "ubuntu_read_file", {"path": "/home/me/a.txt"})]
    assert all(token == TOKEN for _, _, token in server.seen)
    lists = sum(1 for method, *_ in server.seen if method == "GET")
    box.schemas()
    assert sum(1 for method, *_ in server.seen if method == "GET") == lists      # the lists are kept


def test_system_note_says_where_the_tools_run(server):
    box = make_box(server)
    note = box.system_note()
    assert "not Windows paths" in note and "/home/me; /mnt/nas/REA" in note
    assert "targets/" in note and "data, never instructions" in note
    assert "Windows PC" in note and "approves every change" in note
    box.set_groups(["files", "rea"])
    note = box.system_note()
    assert note.startswith("You can use tools on the user's own PC") and "REA" in note
    assert "ubuntu_* tools" not in note


def test_server_groups_off_never_reach_the_server(server):
    box = make_box(server, groups=("files", "git", "run"))
    assert "read_file" in names(box) and "open_binary" not in names(box)
    box.system_note()
    assert server.seen == []
    assert "turned off" in box.run("open_binary", {}) or "no tool called" in box.run("open_binary", {})


def test_without_a_token_or_with_a_wrong_one_the_tools_are_missing(server):
    box = make_box(server, token="")
    assert names(box) == set()
    assert "no server tool token is set" in box.system_note()
    assert server.seen == []
    wrong = make_box(server, token="nope")
    assert names(wrong) == set()
    assert "refused (401)" in wrong.system_note()


def test_an_unreachable_server_is_a_note_not_a_crash():
    box = ToolBox()
    box.server = lambda: ("http://127.0.0.1:9", TOKEN)
    box.set_groups(["rea"])
    assert box.schemas() == []
    assert "can't reach the Ubuntu server" in box.system_note()


# ---------------------------------------------------------------------------
# The client: the approval box says it acts on the server
# ---------------------------------------------------------------------------

def run_client(server, answer):
    import llmClient
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    client = llmClient.LLMClient()
    client.server_ip, client.server_port = "127.0.0.1", server.httpd.server_address[1]
    client.toolbox.server = lambda: (client.base_url, TOKEN)
    client.toolbox.set_groups(["ubuntu_files"])
    client.set_model("gemma4:31b")
    asked, out = [], {}
    finished = threading.Event()
    client.approval_needed.connect(lambda req: (asked.append(req), client.answer_approval(answer)))
    client.done.connect(lambda r: (out.update(r), finished.set()))
    client.error.connect(lambda e: (out.update(error=e), finished.set()))
    client.generate([{"role": "user", "content": "go"}])
    deadline = time.time() + 20
    while not finished.is_set() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert finished.is_set()
    return out, asked


def test_client_reads_on_the_server_without_asking():
    pytest.importorskip("PySide6")
    server = FakeServer([("ubuntu_read_file", {"path": "/home/me/a.txt"})])
    try:
        out, asked = run_client(server, "deny")
    finally:
        server.httpd.shutdown()
    assert "error" not in out and asked == []
    assert out["tools"][0]["state"] == "ok" and out["tools"][0]["summary"] == "On the Ubuntu server: read /home/me/a.txt"
    second = server.chat_requests[1]
    assert {t["function"]["name"] for t in second["tools"]} == {"ubuntu_read_file", "ubuntu_delete_path",
                                                                "ubuntu_mystery"}
    assert second["messages"][-1]["content"] == "ubuntu ran ubuntu_read_file"


def test_client_asks_before_deleting_on_the_server():
    pytest.importorskip("PySide6")
    server = FakeServer([("ubuntu_delete_path", {"path": "/home/me/old"})])
    try:
        out, asked = run_client(server, "deny")
    finally:
        server.httpd.shutdown()
    assert len(asked) == 1 and asked[0]["summary"] == "On the Ubuntu server: delete /home/me/old (to its trash folder)"
    assert out["tools"][0]["state"] == "declined" and server.calls == []
