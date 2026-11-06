"""The REA bridge and the tool token, with a fake REA (fake_rea_server.py) over real MCP stdio.

Run from Server/ollama:  python -m pytest -q tests
"""

import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import rea  # noqa: E402
from chat_server.app import create_app  # noqa: E402
from chat_server.config import Config  # noqa: E402

FAKE_REA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_rea_server.py")
TOKEN = "test-token"
AUTH = {"X-ChatUI-Token": TOKEN}


def no_ollama(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"models": []})


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A REA folder, a NAS with game copies, and the tool settings pointing at them."""
    rea_home, nas = tmp_path / "REA", tmp_path / "nas"
    (rea_home / "targets").mkdir(parents=True)
    (rea_home / "scripts").mkdir()
    games = nas / "Game File Copy" / "Game"
    games.mkdir(parents=True)
    (games / "Game.dll").write_bytes(b"MZ\0fake")
    monkeypatch.setattr(Config, "REA_HOME", str(rea_home))
    monkeypatch.setattr(Config, "REA_ASSETS", str(nas))
    monkeypatch.setattr(Config, "REA_GAME_COPIES", str(nas / "Game File Copy"))
    monkeypatch.setattr(Config, "SERVER_DIR", tmp_path)
    monkeypatch.setattr(Config, "TOOL_ROOTS", f"{rea_home}:{nas}")
    monkeypatch.setattr(Config, "TOOL_READ_ONLY", str(nas / "Game File Copy"))
    monkeypatch.setattr(Config, "TOOL_TOKEN", TOKEN)
    return tmp_path


@pytest.fixture
def client(home):
    app = create_app(ollama_transport=httpx.MockTransport(no_ollama),
                     rea_connect=lambda: rea.stdio_session(f"{sys.executable} {FAKE_REA}"))
    with TestClient(app) as c:
        yield c


def call(client, tool, **arguments):
    r = client.post("/v1/rea/call", json={"name": tool, "arguments": arguments}, headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["output"]


def test_no_token_configured_refuses_everything(home, monkeypatch):
    monkeypatch.setattr(Config, "TOOL_TOKEN", "")
    monkeypatch.setattr(Config, "TOOL_TOKEN_FILE", str(home / "missing"))
    app = create_app(ollama_transport=httpx.MockTransport(no_ollama), rea_connect=lambda: pytest.fail("started"))
    with TestClient(app) as c:
        for path in ("/v1/rea/tools", "/v1/ubuntu/tools"):
            r = c.get(path, headers=AUTH)
            assert r.status_code == 503 and "TOOL_TOKEN" in r.json()["error"]["message"]
        assert c.post("/v1/rea/call", json={"name": "list_targets"}).status_code == 503


def test_wrong_or_missing_token_is_401(client):
    assert client.get("/v1/rea/tools").status_code == 401
    assert client.get("/v1/rea/tools", headers={"X-ChatUI-Token": "nope"}).status_code == 401
    assert client.post("/v1/ubuntu/call", json={"name": "ubuntu_list_directory"}).status_code == 401


def test_token_file_is_read(home, monkeypatch):
    monkeypatch.setattr(Config, "TOOL_TOKEN", "")
    (home / "token").write_text("from-file\n")
    monkeypatch.setattr(Config, "TOOL_TOKEN_FILE", str(home / "token"))
    app = create_app(ollama_transport=httpx.MockTransport(no_ollama))
    with TestClient(app) as c:
        assert c.get("/v1/ubuntu/tools", headers={"X-ChatUI-Token": "from-file"}).status_code == 200


def test_only_allowed_tools_are_listed(client):
    body = client.get("/v1/rea/tools", headers=AUTH).json()
    names = [t["function"]["name"] for t in body["tools"]]
    # The fake has gdb_console too; it must never reach the model.
    assert names == ["binary_overview", "open_binary", "search_strings", "trace_feature",
                     "list_targets", "copy_to_targets", "export_assets"]
    assert body["meta"]["binary_overview"] == {"kind": "read", "group": "rea"}
    assert body["meta"]["copy_to_targets"]["kind"] == "write"
    schema = body["tools"][2]["function"]["parameters"]
    assert schema["type"] == "object" and "pattern" in schema["properties"] and "$schema" not in schema


def test_calls_go_to_one_process_and_are_marked_untrusted(client):
    first = call(client, "binary_overview")
    assert first.startswith(rea.UNTRUSTED) and "overview of pid" in first
    assert call(client, "binary_overview") == first           # same process: same pid
    assert "'abc' (case_sensitive=True)" in call(client, "search_strings", pattern="abc", case_sensitive=True)
    assert call(client, "gdb_console", command="run").startswith("Error: gdb_console is not an allowed REA tool")


def test_rea_restarts_after_it_dies(client):
    before = call(client, "binary_overview")
    out = call(client, "trace_feature", query="crash")
    assert out.startswith("Error: REA stopped during trace_feature")
    after = call(client, "binary_overview")
    assert "overview of pid" in after and after != before
    assert client.get("/v1/rea/status", headers=AUTH).json()["starts"] == 2


def test_a_call_past_the_timeout_stops_rea(client, monkeypatch):
    call(client, "binary_overview")
    monkeypatch.setattr(Config, "REA_TIMEOUT", 1.5)
    assert "took longer than 2s" in call(client, "trace_feature", query="slow")
    monkeypatch.setattr(Config, "REA_TIMEOUT", 60)
    assert call(client, "trace_feature", query="ok").endswith("traced ok")


def test_long_output_is_clipped(home, monkeypatch):
    monkeypatch.setenv("FAKE_REA_PAD", "50000")
    monkeypatch.setattr(Config, "TOOL_OUTPUT_CHARS", 1000)
    app = create_app(ollama_transport=httpx.MockTransport(no_ollama),
                     rea_connect=lambda: rea.stdio_session(f"{sys.executable} {FAKE_REA}"))
    with TestClient(app) as c:
        out = call(c, "search_strings", pattern="x")
        assert len(out) < 1200 and "more characters" in out


def test_paths_must_be_in_the_roots_and_not_game_copies(client, home):
    assert "outside the folders" in call(client, "open_binary", path="/etc/passwd")
    game = home / "nas" / "Game File Copy" / "Game" / "Game.dll"
    assert "copy_to_targets" in call(client, "open_binary", path=str(game))
    target = home / "REA" / "targets" / "Game.dll"
    target.write_bytes(b"MZ")
    assert call(client, "open_binary", path=str(target)).endswith(f"opened {target}")


def test_copy_list_and_export(client, home):
    assert "Copied" in call(client, "copy_to_targets", source="Game")
    assert (home / "REA" / "targets" / "Game" / "Game.dll").is_file()
    assert "already exists" in call(client, "copy_to_targets", source="Game")
    assert "copies from inside" in call(client, "copy_to_targets", source="/etc")
    assert "inside targets" in call(client, "copy_to_targets", source="Game", name="../escape")
    listed = call(client, "list_targets")
    assert "REA targets" in listed and "Game/" in listed and "Game.dll" in listed

    assert "export-assets.sh is missing" in call(client, "export_assets", source=str(home / "REA" / "targets"),
                                                 destination="Decompile/Game")
    script = home / "REA" / "scripts" / "export-assets.sh"
    script.write_text('echo "export $1 -> $2"\n')
    out = call(client, "export_assets", source=str(home / "REA" / "targets" / "Game"), destination="Decompile/Game")
    assert out.startswith("exit code 0") and "-> Decompile/Game" in out
    assert "never Game File Copy" in call(client, "export_assets", source=str(home / "REA" / "targets"),
                                          destination="Game File Copy/x")


def test_flatten_schema_writes_out_refs():
    schema = {"type": "object", "$schema": "x", "additionalProperties": False, "examples": [{}],
              "properties": {"pattern": {"$ref": "#/$defs/s0", "description": "what to find"},
                             "mode": {"$ref": "#/$defs/s1"}},
              "required": ["pattern"],
              "$defs": {"s0": {"type": "string", "minLength": 1}, "s1": {"$ref": "#/$defs/s2", "default": "literal"},
                        "s2": {"type": "string", "enum": ["literal", "regex"]}}}
    out = rea.flatten_schema(schema)
    assert out == {"type": "object", "required": ["pattern"], "properties": {
        "pattern": {"type": "string", "minLength": 1, "description": "what to find"},
        "mode": {"type": "string", "enum": ["literal", "regex"], "default": "literal"}}}
