"""Tests of the per-chat settings: defaults, import/export, old-chat migration,
and (when PySide6 is installed) the main window and settings window together.

Run from Code/:  python -m pytest -q tests
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chat_settings  # noqa: E402


def test_missing_and_wrong_entries_take_defaults():
    s = chat_settings.normalize({"Pretext": "Be brief.", "Temperature": "hot", "Think": "yes",
                                 "Web Search": "sometimes", "Unknown": 1})
    assert s == {**chat_settings.DEFAULTS, "Pretext": "Be brief."}
    assert chat_settings.normalize(None) == chat_settings.DEFAULTS
    assert chat_settings.normalize({"Character": "   "})["Character"] == "Bot"
    assert chat_settings.normalize({"Temperature": 5})["Temperature"] == 2.0
    assert chat_settings.normalize({"Temperature": True})["Temperature"] == 0.7


def test_export_then_import_round_trip(tmp_path):
    s = {**chat_settings.DEFAULTS, "Character": "Ada", "Pretext": "You are Ada.", "Model": "qwen3:14b",
         "Temperature": 0.3, "Think": False, "Web Search": "off", "Tools": False}
    path = tmp_path / "Ada settings.json"
    chat_settings.export_file(s, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[chat_settings.EXPORT_TAG] == chat_settings.EXPORT_VERSION
    assert chat_settings.import_file(path) == s


def test_import_partial_chat_and_old_bot_files(tmp_path):
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"Model": "gemma3:27b"}), encoding="utf-8")
    assert chat_settings.import_file(partial) == {**chat_settings.DEFAULTS, "Model": "gemma3:27b"}

    chat = tmp_path / "chat.json"
    chat.write_text(json.dumps({"Name": "x", "Settings": {"Character": "Kit"}, "Chat": []}), encoding="utf-8")
    assert chat_settings.import_file(chat)["Character"] == "Kit"

    bot = tmp_path / "Bot Description.json"
    bot.write_text(json.dumps({"Name": "Old", "Description": "Old pretext", "WorkflowPath": ""}), encoding="utf-8")
    s = chat_settings.import_file(bot)
    assert (s["Character"], s["Pretext"]) == ("Old", "Old pretext")


def test_import_rejects_unusable_files(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        chat_settings.import_file(bad)
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        chat_settings.import_file(bad)
    with pytest.raises(ValueError, match="can't read"):
        chat_settings.import_file(tmp_path / "missing.json")


def test_old_chat_takes_its_bot_folder_and_top_level_options(tmp_path):
    bot_dir = tmp_path / "Bot" / "Ada"
    bot_dir.mkdir(parents=True)
    (bot_dir / "Bot Description.json").write_text(
        json.dumps({"Name": "Ada", "Description": "You are Ada.", "WorkflowPath": ""}), encoding="utf-8")
    old_chat = {"Name": "c", "Bot Path": str(bot_dir), "Temperature": 0.4, "Model": "m1",
                "Web Search": "on", "Think": False, "Tools": True, "Chat": [], "Payload": []}
    s = chat_settings.from_chat(old_chat)
    assert s == {**chat_settings.DEFAULTS, "Character": "Ada", "Pretext": "You are Ada.", "Model": "m1",
                 "Temperature": 0.4, "Web Search": "on", "Think": False}
    # A bot folder that is gone just leaves the defaults.
    assert chat_settings.from_chat({**old_chat, "Bot Path": str(tmp_path / "gone")})["Character"] == "Bot"


# ---------------------------------------------------------------------------
# GUI, offscreen
# ---------------------------------------------------------------------------

MODELS = [{"id": "m1", "capabilities": ["completion", "tools", "thinking"]},
          {"id": "m2", "capabilities": ["completion"]}]


@pytest.fixture
def gui(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import paths
    import llmClient

    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / "Chat")
    monkeypatch.setattr(paths, "CHAT_INDEX_FILE", tmp_path / ".temp.json")
    monkeypatch.setattr(llmClient.LLMClient, "list_models", lambda self: MODELS)
    paths.ensure_data_dirs()
    app = QApplication.instance() or QApplication([])
    from Widgets import chatMain, chatSettings
    main = chatMain.ChatMain()
    settings = chatSettings.ChatSettings(main)
    yield main, settings, tmp_path
    settings.close()
    main.close()
    app.processEvents()


def _saved(main):
    return json.loads(open(main.chat_path, encoding="utf-8").read())


def test_settings_are_saved_in_the_chat_and_follow_the_quick_picker(gui):
    main, win, tmp = gui
    main.create_chat("First", {**chat_settings.DEFAULTS, "Character": "Ada", "Pretext": "You are Ada.",
                               "Model": "m1", "Web Search": "off"})
    data = _saved(main)
    assert {"Name", "Settings", "Tools Folders", "Chat"} <= set(data)
    assert not {"Payload", "Bot Path", "Model"} & set(data)
    assert data["Settings"]["Character"] == "Ada"
    assert (main.botName, main.client.preset, main.client.model_name, main.client.web_mode) == \
           ("Ada", "You are Ada.", "m1", "off")
    assert main.client.tools_enabled

    win.open_for(main.ui.lineEdit.text(), main.settings, is_new=False)
    main.model_combo.activated.emit(main.model_combo.findData("m2"))    # the quick picker in the main window
    assert _saved(main)["Settings"]["Model"] == "m2"
    assert win.settings()["Model"] == "m2"                              # the open settings window follows
    assert not main.client.tools_enabled                                # m2 can't call tools
    assert not win.ui.toolsCheck.isEnabled()


def test_saving_from_the_window_renames_and_applies(gui):
    main, win, tmp = gui
    main.create_chat("First", chat_settings.defaults())
    win.open_for("First", main.settings, is_new=False)
    win.ui.titleEdit.setText("Renamed: chat")
    win.ui.characterEdit.setText("Kit")
    win.ui.thinkCheck.setChecked(False)
    main.apply_settings(win.title(), win.settings())
    assert main.chat_path.endswith("Renamed chat.json")
    assert not (tmp / "Chat" / "First.json").exists()
    assert _saved(main)["Settings"]["Character"] == "Kit"
    assert main.client.think is False
    index = json.loads((tmp / ".temp.json").read_text(encoding="utf-8"))
    assert index["ChatList"] == ["Renamed chat"]


def test_old_chat_is_migrated_on_load(gui):
    main, win, tmp = gui
    bot = tmp / "Bot" / "Ada"
    bot.mkdir(parents=True)
    (bot / "Bot Description.json").write_text(
        json.dumps({"Name": "Ada", "Description": "You are Ada.", "WorkflowPath": ""}), encoding="utf-8")
    old = tmp / "Chat" / "Old.json"
    old.write_text(json.dumps({"Name": "Old", "Bot Path": str(bot), "Temperature": 0.5, "Model": "m1",
                               "Chat": [{"role": "user", "content": "hi", "ts": 1}], "Payload": [1]}),
                   encoding="utf-8")
    main.load_chat(str(old))
    data = _saved(main)
    assert "Payload" not in data and "Bot Path" not in data
    assert data["Settings"]["Character"] == "Ada" and data["Settings"]["Temperature"] == 0.5
    assert data["Chat"][0]["content"] == "hi"


def test_folder_button_is_half_width(gui):
    from PySide6.QtWidgets import QPushButton
    main, _, _ = gui
    full = QPushButton("📁")
    full.setStyleSheet(main.folder_button.styleSheet())
    assert main.folder_button.width() == max(full.sizeHint().width() // 2, 24)
