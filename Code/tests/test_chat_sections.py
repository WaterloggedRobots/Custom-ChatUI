"""Tests of the chat list's sections: the rules (chat_sections.py) and dragging in the list."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chat_sections  # noqa: E402
import chat_settings  # noqa: E402


def test_normalize_drops_bad_entries_and_missing_chats():
    data = [{"Name": "Work", "Chats": ["a", "gone", "a"], "Open": False}, {"Name": "Work"}, {"Name": " "}, "junk",
            {"Name": "Home", "Chats": ["a", "b"]}]
    assert chat_sections.normalize(data, ["a", "b"]) == [
        {"Name": "Work", "Chats": ["a"], "Open": False}, {"Name": "Home", "Chats": ["b"], "Open": True}]
    assert chat_sections.normalize(None, ["a"]) == []


def test_section_rules():
    s = []
    assert chat_sections.add(s, " Work ") and chat_sections.add(s, "Home")
    assert not chat_sections.add(s, "Work") and not chat_sections.add(s, "  ")
    chat_sections.move_chat(s, "a", "Work")
    chat_sections.move_chat(s, "a", "Home")           # a chat is in one section at a time
    assert chat_sections.section_of(s, "a") == "Home" and s[0]["Chats"] == []
    chat_sections.rename_chat(s, "a", "b")
    assert s[1]["Chats"] == ["b"]
    assert not chat_sections.rename(s, "Work", "Home") and chat_sections.rename(s, "Work", "Job")
    chat_sections.move_section(s, "Home", "Job")
    assert [x["Name"] for x in s] == ["Home", "Job"]
    chat_sections.move_section(s, "Home")
    assert [x["Name"] for x in s] == ["Job", "Home"]
    chat_sections.remove(s, "Home")
    assert chat_sections.section_of(s, "b") is None


@pytest.fixture
def gui(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import paths
    import llmClient

    monkeypatch.setattr(paths, "CHAT_DIR", tmp_path / "Chat")
    monkeypatch.setattr(paths, "CHAT_INDEX_FILE", tmp_path / ".temp.json")
    monkeypatch.setattr(llmClient.LLMClient, "list_models", lambda self: {})
    paths.ensure_data_dirs()
    app = QApplication.instance() or QApplication([])
    from Widgets import chatMain
    main = chatMain.ChatMain()
    main.resize(900, 700)
    main.showChatList()
    main.show()
    yield main, app, tmp_path
    main.close()
    app.processEvents()


def _rows(tree):
    """What the list shows, with the chats of a section indented."""
    rows = []
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        rows.append(item.text(0))
        rows += ["  " + item.child(k).text(0) for k in range(item.childCount())]
    return rows


def _find(tree, text):
    from PySide6.QtCore import Qt
    return tree.findItems(text, Qt.MatchExactly | Qt.MatchRecursive)[0]


def _drag(app, tree, source, target=None, upper=False):
    """Drop `source` on `target`, or on the empty space below the rows."""
    from PySide6.QtCore import QMimeData, QPointF, Qt
    from PySide6.QtGui import QDropEvent
    tree.setCurrentItem(source)
    if target is None:
        pos = QPointF(5, tree.viewport().height() - 2)
    else:
        rect = tree.visualItemRect(target)
        pos = QPointF(rect.center().x(), rect.top() + 1 if upper else rect.bottom() - 1)
    event = QDropEvent(pos, Qt.MoveAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)
    tree.dropEvent(event)
    app.processEvents()


def _index(tmp):
    return json.loads((tmp / ".temp.json").read_text(encoding="utf-8"))


def test_drag_chats_into_and_out_of_sections(gui, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    main, app, tmp = gui
    for name in ("One", "Two", "Three"):
        main.create_chat(name, chat_settings.defaults())
    tree = main.chat_list
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Work", True))
    tree.on_click(_find(tree, "Create New Section [+]"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Home", True))
    tree.new_section()
    assert _rows(tree) == ["Create New Chat [+]", "Create New Section [+]", "Work (0)", "Home (0)",
                           "Three", "Two", "One"]

    _drag(app, tree, _find(tree, "One"), _find(tree, "Work (0)"))
    _drag(app, tree, _find(tree, "Two"), _find(tree, "One"))      # onto a chat in the section
    assert _rows(tree)[2:] == ["Work (2)", "  Two", "  One", "Home (0)", "Three"]
    assert _index(tmp)["Sections"][0]["Chats"] == ["One", "Two"]

    _drag(app, tree, _find(tree, "Home (0)"), _find(tree, "Work (2)"), upper=True)
    assert _rows(tree)[2:4] == ["Home (0)", "Work (2)"]
    _drag(app, tree, _find(tree, "Two"))                          # out onto the empty space
    assert _rows(tree)[2:] == ["Home (0)", "Work (1)", "  One", "Three", "Two"]

    # Opening a chat keeps it in its section, on top; renaming keeps it there too.
    main.create_chat("Four", chat_settings.defaults())
    _drag(app, tree, _find(tree, "Four"), _find(tree, "Work (1)"))
    main.qs_chat("One")
    assert _rows(tree)[3:6] == ["Work (2)", "  One", "  Four"]
    main.apply_settings("Uno", main.settings)
    assert _rows(tree)[4] == "  Uno"
    assert sorted(_index(tmp)["Sections"][1]["Chats"]) == ["Four", "Uno"]

    tree.delete_section("Work")                                  # its chats stay
    assert "Uno" in _rows(tree) and "Four" in _rows(tree)


def test_section_stays_collapsed(gui, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    main, app, tmp = gui
    main.create_chat("One", chat_settings.defaults())
    tree = main.chat_list
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Work", True))
    tree.new_section("One")
    tree.on_click(_find(tree, "Work (1)"))
    assert _index(tmp)["Sections"][0]["Open"] is False
    main.qs_chat("One")
    assert not _find(tree, "Work (1)").isExpanded()


def test_clicking_a_row_opens_it(gui):
    main, app, _ = gui
    main.create_chat("One", chat_settings.defaults())
    main.create_chat("Two", chat_settings.defaults())
    opened, new = [], []
    tree = main.chat_list
    tree.chat_clicked.connect(opened.append)
    tree.new_chat_clicked.connect(lambda: new.append(1))
    tree.on_click(_find(tree, "One"))
    tree.on_click(_find(tree, "Create New Chat [+]"))
    app.processEvents()
    assert opened == ["One"] and new == [1]
