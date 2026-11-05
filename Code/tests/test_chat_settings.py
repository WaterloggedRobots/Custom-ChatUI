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
         "Temperature": 0.3, "Think": False, "Web Search": "off", "Tool Git": False}
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
    assert not win.ui.gitToolsCheck.isEnabled()


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


@pytest.mark.parametrize("select", [False, True])
def test_message_stays_at_the_end_after_clicking_in_the_chat(gui, monkeypatch, select):
    from PySide6.QtGui import QTextCursor
    main, _, _ = gui
    main.create_chat("Click", {**chat_settings.DEFAULTS, "Model": "m1"})
    monkeypatch.setattr(main.client, "generate", lambda *a, **k: None)
    main.input.setPlainText("first")
    main.sendMessage()
    main.on_done({"content": "Reasons:\n\n* one item\n* two item\n\nThe end"})

    block = main.chat.document().begin()
    while not block.textList():
        block = block.next()
    cursor = main.chat.textCursor()
    cursor.setPosition(block.position() + 2)
    if select:
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
    main.chat.setTextCursor(cursor)

    main.input.setPlainText("second")
    main.sendMessage()
    blocks = []
    block = main.chat.document().begin()
    while block.isValid():
        blocks.append(block)
        block = block.next()
    you = next(b for b in reversed(blocks) if b.text().startswith("You"))
    assert you.text().endswith("second") and not you.textList()
    listed = [b.text().strip() for b in blocks if b.textList()]
    assert listed == ["one item", "two item"]


def test_repo_link_is_saved_with_the_chat_and_listed(gui, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    main, _, _ = gui
    main.create_chat("Repos", {**chat_settings.DEFAULTS, "Model": "m1"})
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("not a repo", True))
    main.add_repo()
    assert main.client.toolbox.repos == [] and "isn't a GitHub" in main.status_label.text()

    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("github.com/owner/repo.git", True))
    main.add_repo()
    url = "https://github.com/owner/repo"
    assert main.client.toolbox.repos == [url] and main.repo_list_button.text() == "▾ 1"
    assert _saved(main)["Tools Repos"] == [url]
    main._fill_repo_menu()
    assert len(main.repo_menu.actions()) == 1

    main.remove_repo(url)
    assert _saved(main)["Tools Repos"] == [] and main.repo_list_button.text() == "▾"


def _wait_for_repos(main, app):
    for _ in range(200):
        app.processEvents()
        if not main._fetching_repos:
            return
        time.sleep(0.01)
    raise AssertionError("the repositories never arrived")


def test_repo_button_lists_your_repos_with_other_last(gui, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QInputDialog
    from local_tools import github_login
    main, _, _ = gui
    app = QApplication.instance()
    main.create_chat("Repos", {**chat_settings.DEFAULTS, "Model": "m1"})
    names = [f"me/repo{i}" for i in range(60)]
    monkeypatch.setattr(github_login, "own_repos", lambda: names)
    main.repo_button.click()
    assert main.repo_picker_list.item(0).text() == "Loading your repositories…"
    _wait_for_repos(main, app)
    rows = main.repo_picker_list
    assert rows.count() == 60 and rows.item(0).text() == "me/repo0"
    assert rows.height() <= 320 and rows.verticalScrollBar().maximum() > 0    # it scrolls
    assert [a.text() for a in main.repo_picker.actions()][-1] == "Other…"

    main.pick_repo(rows.item(2))
    assert main.client.toolbox.repos == ["https://github.com/me/repo2"]
    assert _saved(main)["Tools Repos"] == ["https://github.com/me/repo2"]
    main.show_repo_picker()
    _wait_for_repos(main, app)
    assert rows.item(2).text() == "✓ me/repo2" and rows.item(2).flags() == Qt.NoItemFlags

    # Other… asks for a link
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("someone/else", True))
    main.repo_picker.actions()[-1].trigger()
    assert main.client.toolbox.repos[-1] == "https://github.com/someone/else"
    main.repo_picker.close()


def test_repo_picker_says_why_it_has_no_repos(gui, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from local_tools import github_login
    main, _, _ = gui
    app = QApplication.instance()
    main.create_chat("Repos", {**chat_settings.DEFAULTS, "Model": "m1"})
    monkeypatch.setattr(github_login, "own_repos", lambda: None)
    main.show_repo_picker()
    _wait_for_repos(main, app)
    assert "Not signed in" in main.repo_picker_list.item(0).text()

    def refused():
        raise github_login.LoginError("can't reach GitHub")

    monkeypatch.setattr(github_login, "own_repos", refused)
    main.show_repo_picker()
    _wait_for_repos(main, app)
    assert "can't reach GitHub" in main.repo_picker_list.item(0).text()
    main.repo_picker.close()


def test_memory_folder_and_settings_section(gui):
    main, win, tmp = gui
    main.create_chat("First", {**chat_settings.DEFAULTS, "Model": "m1"})
    folder = tmp / "Chat" / "First" / "memory"
    assert main.client.toolbox.memory_dir == folder and main.memory_folder() == folder
    assert main.client.tools_enabled

    win.open_for("First", main.settings, is_new=False)
    assert win.ui.memoryCheck.isChecked() and win.ui.memoryButton.isEnabled()
    assert win.ui.memoryLabel.text() == "No notes yet"
    main.client.toolbox.run("save_memory", {"name": "rules", "content": "Keep it short."})
    win.open_for("First", main.settings, is_new=False)
    assert win.ui.memoryLabel.text() == "rules.md"
    system = [m["content"] for m in main.client.build_messages([{"role": "user", "content": "hi"}]) if m["role"] == "system"]
    assert any("Keep it short." in c for c in system)

    # The notes follow the chat when it's renamed, and stay out of other chats.
    main.apply_settings("Renamed", win.settings())
    assert (tmp / "Chat" / "Renamed" / "memory" / "rules.md").exists() and not (tmp / "Chat" / "First").exists()
    assert main.client.toolbox.memory_dir == tmp / "Chat" / "Renamed" / "memory"
    main.create_chat("Second", {**chat_settings.DEFAULTS, "Model": "m1"})
    assert main.client.toolbox.memory_note() == ""

    # Off: not sent and no tools, though the notes are kept.
    main.qs_chat("Renamed")
    main.apply_settings("Renamed", {**main.settings, "Memory": False})
    assert main.client.toolbox.memory_note() == "" and main.client.toolbox.memory_dir is None
    assert (tmp / "Chat" / "Renamed" / "memory" / "rules.md").exists()
    main.create_chat("Taken", {**chat_settings.DEFAULTS, "Model": "m1"})
    assert main.chat_path.endswith("Taken.json")
    win.open_for("", main.settings, is_new=True)
    assert not win.ui.memoryButton.isEnabled()


def test_general_settings_show_the_github_user(gui, monkeypatch):
    from local_tools import github_login
    from PySide6.QtWidgets import QApplication
    from Widgets.generalSettings import GeneralSettings
    import time
    win = GeneralSettings()
    monkeypatch.setattr(github_login, "signed_in_user", lambda: "someone")
    win.check_github()
    deadline = time.monotonic() + 5
    while win.github_label.text() == "Checking GitHub…" and time.monotonic() < deadline:
        QApplication.processEvents()
    assert win.github_label.text() == "✓ Connected as someone"
    win._show_github_user(None)
    assert win.github_label.text() == "Not signed in"
