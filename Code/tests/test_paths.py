"""Where saves go: the NAS folder from network.json, the one-time copy of this PC's saves, the index tidy."""

import json
import sys

import paths


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def test_nas_folder_comes_from_network_json(tmp_path, monkeypatch):
    _write(tmp_path / "default.json", {"nas": {"ip": "10.0.0.5", "share": "home", "mount": "/mnt/nas",
                                               "save_path": "LLM/Save"}})
    monkeypatch.setattr(paths, "DEFAULT_NETWORK_FILE", tmp_path / "default.json")
    monkeypatch.setattr(sys, "platform", "linux")
    assert str(paths.nas_save_dir()).replace("\\", "/") == "/mnt/nas/LLM/Save"
    monkeypatch.setattr(sys, "platform", "win32")
    assert str(paths.nas_save_dir()).replace("\\", "/") == "//10.0.0.5/home/LLM/Save"
    _write(paths.USER_NETWORK_FILE, {"nas": {"share": ""}})          # your own copy wins: no share, no NAS
    assert paths.nas_save_dir() is None


def test_local_saves_are_copied_once_and_merged(tmp_path):
    local, nas = tmp_path / "local", tmp_path / "nas"
    _write(local / "Chat" / "Mine.json", {"Name": "Mine"})
    _write(local / "Chat" / "Both.json", {"Name": "Local copy"})
    _write(local / ".temp.json", {"LastChat": "C:/x/Mine.json", "ChatList": ["Both", "Mine"],
                                  "Sections": [{"Name": "Work", "Chats": ["Mine"]}]})
    _write(local / "links.json", {"Projects": ["A"], "Leads": {"A": "Mine"}})
    _write(nas / "Chat" / "Both.json", {"Name": "NAS copy"})
    _write(nas / ".temp.json", {"ChatList": ["Theirs", "Both"], "Sections": [{"Name": "Home", "Chats": []}]})
    _write(nas / "links.json", {"Projects": ["B"], "Leads": {"A": "Theirs"}})

    assert paths.move_local_saves(local, nas) == 1
    assert json.loads((nas / "Chat" / "Mine.json").read_text())["Name"] == "Mine"
    assert json.loads((nas / "Chat" / "Both.json").read_text())["Name"] == "NAS copy"     # kept
    index = json.loads((nas / ".temp.json").read_text())
    assert index["ChatList"] == ["Theirs", "Both", "Mine"]
    assert [s["Name"] for s in index["Sections"]] == ["Home", "Work"]
    links = json.loads((nas / "links.json").read_text())
    assert links["Projects"] == ["B", "A"] and links["Leads"] == {"A": "Theirs"}
    assert (local / paths.MOVED_MARK).exists() and (local / "Chat" / "Mine.json").exists()   # local stays
    assert paths.move_local_saves(local, nas) == 0                                          # only once


def test_index_drops_missing_chats_and_keeps_the_old_one(tmp_path):
    _write(paths.CHAT_DIR / "Here.json", {"Name": "Here"})
    _write(paths.CHAT_INDEX_FILE, {"LastOpened": "x", "LastChat": "eggs", "ChatList": ["Gone", "Here"]})
    paths.tidy_index()
    assert json.loads(paths.CHAT_INDEX_FILE.read_text())["ChatList"] == ["Here"]
    old = paths.CHAT_INDEX_FILE.with_name(".temp.old.json")
    assert json.loads(old.read_text())["ChatList"] == ["Gone", "Here"]
    paths.CHAT_INDEX_FILE.unlink()
    paths.tidy_index()                                   # none yet: a fresh, empty one
    assert json.loads(paths.CHAT_INDEX_FILE.read_text())["ChatList"] == []
