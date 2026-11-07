"""The Ubuntu file and run tools and the folders they may use (ubuntu.py, server_tools.resolve).

Run from Server/ollama:  python -m pytest -q tests
"""

import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import server_tools, ubuntu  # noqa: E402
from chat_server.app import create_app  # noqa: E402
from chat_server.config import Config  # noqa: E402
from chat_server.server_tools import ToolError  # noqa: E402

TOKEN = "test-token"


@pytest.fixture
def root(tmp_path, monkeypatch):
    work, nas, secret = tmp_path / "work", tmp_path / "nas", tmp_path / "work" / ".ssh"
    (nas / "Game File Copy").mkdir(parents=True)
    (nas / "Game File Copy" / "game.txt").write_text("game data\n")
    secret.mkdir(parents=True)
    (secret / "id_rsa").write_text("KEY")
    (work / "notes.txt").write_text("one\ntwo apples\nthree\n")
    (work / "sub").mkdir()
    (work / "sub" / "code.py").write_text("print('apples')\n")
    (tmp_path / "outside.txt").write_text("no")
    monkeypatch.setattr(Config, "TOOL_ROOTS", f"{work}:{nas}")
    monkeypatch.setattr(Config, "TOOL_DENY", str(secret))
    monkeypatch.setattr(Config, "TOOL_READ_ONLY", str(nas / "Game File Copy"))
    monkeypatch.setattr(Config, "TOOL_TOKEN", TOKEN)
    return work


def test_paths_outside_the_roots_are_refused(root):
    for bad, why in ((str(root.parent / "outside.txt"), "outside the folders"),
                     (f"{root}/../outside.txt", "outside the folders"),
                     ("C:\\Users\\me\\x.txt", "Windows path"),
                     ("notes.txt", "relative"),
                     (str(root / ".ssh" / "id_rsa"), "private keys"),
                     ("", "no path")):
        with pytest.raises(ToolError, match=why):
            server_tools.resolve(bad)
    os.symlink(root.parent / "outside.txt", root / "link.txt")
    with pytest.raises(ToolError, match="outside the folders"):
        server_tools.resolve(str(root / "link.txt"))
    game = root.parent / "nas" / "Game File Copy" / "game.txt"
    assert server_tools.resolve(str(game)) == game
    with pytest.raises(ToolError, match="read-only"):
        server_tools.resolve(str(game), write=True)


def test_reading_tools(root):
    listed = ubuntu.list_directory(str(root))
    assert "notes.txt" in listed and "sub/" in listed
    assert "two apples" in ubuntu.read_file(str(root / "notes.txt"), start_line=2, max_lines=1)
    assert ubuntu.find_files("*.py", str(root)) == str(root / "sub" / "code.py")
    hits = ubuntu.search_in_files("apples", str(root))
    assert f"{root}/notes.txt:2:" in hits and "code.py:1:" in hits
    assert ubuntu.search_in_files("KEY", str(root)).startswith("No matches")   # the denied folder is skipped


def test_writing_tools(root):
    f = root / "new" / "a.txt"
    ubuntu.write_file(str(f), "hello world")
    with pytest.raises(ToolError, match="already exists"):
        ubuntu.write_file(str(f), "again")
    ubuntu.edit_file(str(f), "world", "there")
    assert f.read_text() == "hello there"
    ubuntu.copy_path(str(f), str(root / "sub"))
    assert (root / "sub" / "a.txt").read_text() == "hello there"
    ubuntu.move_path(str(root / "sub" / "a.txt"), str(root / "b.txt"))
    assert (root / "b.txt").exists() and not (root / "sub" / "a.txt").exists()
    ubuntu.create_folder(str(root / "x" / "y"))
    assert (root / "x" / "y").is_dir()
    with pytest.raises(ToolError, match="inside itself"):
        ubuntu.copy_path(str(root / "sub"), str(root / "sub" / "deeper"))
    with pytest.raises(ToolError, match="read-only"):
        ubuntu.write_file(str(root.parent / "nas" / "Game File Copy" / "new.txt"), "x")


def test_delete_goes_to_the_trash(root):
    out = ubuntu.delete_path(str(root / "notes.txt"))
    assert not (root / "notes.txt").exists()
    trashed = list((root / ".chatui-trash").rglob("notes.txt"))
    assert len(trashed) == 1 and trashed[0].read_text().startswith("one") and str(trashed[0]) in out
    with pytest.raises(ToolError, match="recursive=true"):
        ubuntu.delete_path(str(root / "sub"))
    ubuntu.delete_path(str(root / "sub"), recursive=True)
    assert list((root / ".chatui-trash").rglob("code.py"))
    for refused in (root, root / ".chatui-trash"):
        with pytest.raises(ToolError, match="refusing"):
            ubuntu.delete_path(str(refused), recursive=True)


def test_run_command(root, monkeypatch):
    out = ubuntu.run_command("pwd; echo $TOOL_TOKEN; exit 3", cwd=str(root / "sub"))
    assert out.startswith(f"exit code 3  (in {root / 'sub'})") and str(root / "sub") in out
    assert TOKEN not in out                       # the token doesn't reach the command
    monkeypatch.setenv("TOOL_TOKEN", TOKEN)
    assert TOKEN not in ubuntu.run_command("echo $TOOL_TOKEN", cwd=str(root))
    with pytest.raises(ToolError, match="outside the folders"):
        ubuntu.run_command("ls", cwd="/etc")
    with pytest.raises(ToolError, match="empty command"):
        ubuntu.run_command("  ", cwd=str(root))
    marker = root / "after-timeout"
    with pytest.raises(ToolError, match="stopped after 1s") as e:
        ubuntu.run_command(f"echo started; (sleep 3; touch {marker}) & sleep 30", cwd=str(root), timeout=1)
    assert "started" in str(e.value)
    import time
    time.sleep(3.5)
    assert not marker.exists()                    # the whole process group was stopped


def test_endpoints(root):
    app = create_app(ollama_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})))
    with TestClient(app) as c:
        auth = {"X-ChatUI-Token": TOKEN}
        body = c.get("/v1/ubuntu/tools", headers=auth).json()
        names = [t["function"]["name"] for t in body["tools"]]
        assert "ubuntu_read_file" in names and "ubuntu_delete_path" in names
        assert body["meta"]["ubuntu_read_file"] == {"kind": "read", "group": "files"}
        assert body["meta"]["ubuntu_write_file"]["kind"] == "write"
        assert body["meta"]["ubuntu_run_command"] == {"kind": "run", "group": "run"}
        assert body["roots"][0] == str(root)

        def call(name, **arguments):
            return c.post("/v1/ubuntu/call", json={"name": name, "arguments": arguments}, headers=auth).json()["output"]

        assert "two apples" in call("ubuntu_read_file", path=str(root / "notes.txt"))
        assert call("ubuntu_read_file", path="/etc/passwd").startswith("Error: /etc/passwd is outside")
        assert call("ubuntu_read_file", path=str(root / "notes.txt"), bogus=1).startswith("Error: bad arguments")
        assert call("rm_rf").startswith("Error: there is no tool called rm_rf")
        assert call("ubuntu_run_command", command="echo hi", cwd=str(root)).endswith("\nhi")


def test_linked_server_folders_narrow_every_tool(root):
    app = create_app(ollama_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})))
    sub, nas = root / "sub", root.parent / "nas"
    with TestClient(app) as c:
        auth = {"X-ChatUI-Token": TOKEN}

        def call(name, folders, **arguments):
            return c.post("/v1/ubuntu/call", json={"name": name, "arguments": arguments, "folders": folders},
                          headers=auth).json()["output"]

        assert "apples" in call("ubuntu_read_file", [str(sub)], path=str(sub / "code.py"))
        out = call("ubuntu_read_file", [str(sub)], path=str(root / "notes.txt"))
        assert out.startswith("Error:") and "outside the server folders linked to this chat" in out
        assert "code.py" in call("ubuntu_list_directory", [str(sub)])          # ~ means the first linked folder
        assert call("ubuntu_run_command", [str(sub)], command="pwd").endswith(f"\n{sub}")
        assert "outside the server folders" in call("ubuntu_run_command", [str(sub)], command="pwd", cwd=str(root))
        # several folders; none linked = all the roots, as before
        assert "notes.txt" in call("ubuntu_list_directory", [str(sub), str(root)], path=str(root))
        assert "two apples" in call("ubuntu_read_file", None, path=str(root / "notes.txt"))
        # a linked folder must itself be allowed
        assert "outside the folders" in call("ubuntu_list_directory", ["/etc"], path="/etc")
        assert "private keys" in call("ubuntu_list_directory", [str(root / ".ssh")], path=str(root / ".ssh"))
        # and it doesn't stick to later calls
        assert "two apples" in call("ubuntu_read_file", [], path=str(root / "notes.txt"))


def test_folder_picker_lists_roots_and_subfolders(root):
    app = create_app(ollama_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})))
    with TestClient(app) as c:
        auth = {"X-ChatUI-Token": TOKEN}
        top = c.get("/v1/ubuntu/folders", headers=auth).json()
        assert [f["path"] for f in top["folders"]] == [str(root), str(root.parent / "nas")]
        inside = c.get("/v1/ubuntu/folders", params={"path": str(root)}, headers=auth).json()
        assert [f["name"] for f in inside["folders"]] == ["sub"]           # .ssh is denied, files aren't folders
        assert inside["parent"] is None
        sub = c.get("/v1/ubuntu/folders", params={"path": str(root / "sub")}, headers=auth).json()
        assert sub["parent"] == str(root) and sub["folders"] == []
        bad = c.get("/v1/ubuntu/folders", params={"path": "/etc"}, headers=auth)
        assert bad.status_code == 400 and "outside" in bad.json()["error"]["message"]
        assert c.get("/v1/ubuntu/folders").status_code == 401
