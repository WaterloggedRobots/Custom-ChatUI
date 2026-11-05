"""Tests of the link graph window with real mouse events, and of the chat window following its links."""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chat_links as cl  # noqa: E402
import paths  # noqa: E402
import personalities  # noqa: E402

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def main(tmp_path, monkeypatch):
    for name, value in (("CLIENT_DATA_DIR", tmp_path), ("SAVE_DIR", tmp_path / "Save"),
                        ("CHAT_DIR", tmp_path / "Save" / "Chat"), ("CHAT_INDEX_FILE", tmp_path / "Save" / ".temp.json"),
                        ("LINKS_FILE", tmp_path / "Save" / "links.json"), ("PROJECT_DIR", tmp_path / "Save" / "Projects"),
                        ("PERSONALITY_DIR", tmp_path / "Save" / "Personalities"), ("AUDIO_DIR", tmp_path / "Audio")):
        monkeypatch.setattr(paths, name, value)
    paths.ensure_data_dirs()
    for chat in ("a", "b", "c"):
        (paths.CHAT_DIR / f"{chat}.json").write_text(json.dumps({
            "Name": chat, "Settings": {"Character": f"Own {chat}", "Pretext": f"own {chat}"},
            "Chat": [{"role": "user", "content": f"hello from {chat}"}]}), encoding="utf-8")
    from Widgets import chatMain
    monkeypatch.setattr(chatMain.llmClient.LLMClient, "list_models", lambda self: [])
    window = chatMain.ChatMain()
    window.load_chat(str(paths.CHAT_DIR / "a.json"))
    yield window
    window.close()


@pytest.fixture
def graph(main):
    from PySide6.QtCore import QSettings
    QSettings().remove("graph/show_unlinked")
    from Widgets import linkGraph
    window = linkGraph.LinkGraph(main)
    cl.add_hub(main.links, cl.PROJECT, "Work")
    cl.add_hub(main.links, cl.PERSONALITY, "Mio")
    personalities.save("Mio", {**main.settings, "Character": "Mio", "Pretext": "tsundere", "Model": ""})
    window.unlinked_check.setChecked(True)
    window.open()
    window.layout.heat = 0                 # no drifting while the test clicks
    QApplication.processEvents()
    window.fit_view()
    yield window
    window.close()


def spot(graph, name) -> QPoint:
    return graph.view.mapFromScene(graph.nodes[name].pos())


def click(graph, name):
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, name))


def double_click(graph, name):
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, name))
    QTest.mouseDClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, name))


A, B, WORK, MIO = "chat:a", "chat:b", "project:Work", "personality:Mio"


def test_shapes_differ_and_nodes_show(graph):
    assert {A, B, "chat:c", WORK, MIO} == set(graph.nodes)
    assert graph.nodes[A].kind == cl.CHAT and graph.nodes[WORK].kind == cl.PROJECT
    assert graph.nodes[MIO].kind == cl.PERSONALITY


def test_opening_starts_the_physics_and_it_settles(graph):
    graph.close()
    graph.layout.heat = 1.0
    graph.open()
    assert graph.timer.isActive()
    for _ in range(400):
        graph.tick()
    assert graph.layout.settled and not graph.timer.isActive()


def test_unlinked_chats_stay_hidden_unless_asked_except_the_open_one(graph):
    graph.unlinked_check.setChecked(False)
    assert {A, WORK, MIO} == set(graph.nodes)      # a is open
    graph.search.setText("b")
    assert B in graph.nodes
    graph.search.setText("")
    assert B not in graph.nodes


def test_click_selects_without_opening_and_double_click_links(graph, main):
    click(graph, B)
    assert graph.selected == B and main.chat_name() == "a"
    double_click(graph, A)
    assert graph.link_from == A
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, WORK))
    assert graph.link_from is None and cl.has_link(main.links, A, WORK, cl.MEMBER)
    assert json.loads(paths.LINKS_FILE.read_text(encoding="utf-8"))["Links"]
    assert len(graph.edges) == 1


def test_esc_or_empty_space_cancels_a_link(graph, main):
    double_click(graph, A)
    QTest.keyClick(graph.view, Qt.Key_Escape)
    assert graph.link_from is None
    double_click(graph, A)
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=QPoint(2, 2))
    assert graph.link_from is None and main.links["Links"] == []


def test_a_node_that_cant_link_keeps_the_link_going(graph, main):
    cl.add_hub(main.links, cl.PROJECT, "Home")
    graph.refresh()
    graph.layout.heat = 0
    graph.fit_view()
    double_click(graph, WORK)
    assert graph.nodes["project:Home"].target < 1 and graph.nodes[A].target == 1
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, "project:Home"))
    assert graph.link_from == WORK and main.links["Links"] == []


def test_chat_to_personality_asks_what_the_link_means(graph, main, monkeypatch):
    monkeypatch.setattr(graph, "_choose_kind", lambda a, b, choices: cl.READS)
    double_click(graph, B)
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, MIO))
    assert cl.has_link(main.links, B, MIO, cl.READS)
    monkeypatch.setattr(graph, "_choose_kind", lambda a, b, choices: None)    # the menu was dismissed
    double_click(graph, A)
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, MIO))
    assert not cl.has_link(main.links, A, MIO, cl.SPEAKS)


def test_two_chats_link_through_a_new_project(graph, main, monkeypatch):
    monkeypatch.setattr(graph, "_ask_name", lambda *a, **k: "Together")
    double_click(graph, A)
    QTest.mouseClick(graph.view.viewport(), Qt.LeftButton, pos=spot(graph, B))
    assert "Together" in main.links["Projects"]
    assert sorted(cl.readable_chats(main.links, "a")) == ["b"]
    assert "b" in main.client.toolbox.link_scope.chats         # the open chat can reach it straight away


def test_dragging_moves_and_pins_and_saves(graph, main):
    view = graph.view.viewport()
    start = spot(graph, B)
    QTest.mousePress(view, Qt.LeftButton, pos=start)
    QTest.mouseMove(view, start + QPoint(30, 25))
    QTest.mouseMove(view, start + QPoint(60, 50))
    assert graph.held == B
    QTest.mouseRelease(view, Qt.LeftButton, pos=start + QPoint(60, 50))
    assert graph.held is None and B in graph.layout.pinned and graph.selected is None
    assert main.links["Layout"][B]["Pinned"] is True
    graph.layout.heat = 1.0
    x = graph.layout.x[B]
    graph.tick()
    assert graph.layout.x[B] == x                  # the physics can't move it now
    graph.unpin(B)
    assert B not in graph.layout.pinned


def test_dragging_empty_space_moves_the_view(graph):
    view = graph.view.viewport()
    before = graph.view.horizontalScrollBar().value()
    QTest.mousePress(view, Qt.LeftButton, pos=QPoint(3, 3))
    QTest.mouseMove(view, QPoint(60, 3))
    QTest.mouseMove(view, QPoint(120, 3))
    QTest.mouseRelease(view, Qt.LeftButton, pos=QPoint(120, 3))
    assert graph.view.horizontalScrollBar().value() != before


def test_hover_brightens_direct_links_only(graph, main):
    cl.add_link(main.links, A, WORK, cl.MEMBER)
    cl.add_link(main.links, B, MIO, cl.READS)
    graph.refresh()
    graph.layout.heat = 0
    graph.set_hover(A)
    assert graph.nodes[A].target == 1 and graph.nodes[WORK].target == 1
    assert graph.nodes[B].target < 1 and graph.nodes[MIO].target < 1
    bright = [e for e in graph.edges if e.target == 1]
    assert [e.link["From"] for e in bright] == [A]
    graph.set_hover(None)
    assert graph.nodes[B].target == 1


def test_removing_a_link_with_the_keyboard(graph, main):
    cl.add_link(main.links, A, WORK, cl.MEMBER)
    graph.refresh()
    graph.select(graph.edges[0])
    QTest.keyClick(graph.view, Qt.Key_Delete)
    assert main.links["Links"] == [] and graph.edges == []


def test_the_panel_says_links_from_the_nodes_side_and_clears(graph, main):
    from PySide6.QtWidgets import QLabel, QListWidget
    cl.add_link(main.links, A, MIO, cl.READS)
    graph.refresh()
    graph.select(graph.nodes[MIO])
    rows = graph.panel.findChildren(QListWidget)[-1]
    assert rows.item(0).text() == "Can read a"
    graph.select(graph.nodes[A])
    assert graph.panel.findChildren(QListWidget)[-1].item(0).text() == "Mio can read this"
    graph.select(None)
    QApplication.processEvents()
    shown = [w.text() for w in graph.panel.findChildren(QLabel) if not w.isHidden()]
    assert "Link Graph" in shown and "a" not in shown


def test_positions_come_back(graph, main):
    graph.drop_node(graph.nodes[A])
    graph.layout.place(A, 123, 45, pinned=True)
    graph.save_layout()
    saved = cl.load(paths.LINKS_FILE, main.all_chats())
    assert saved["Layout"][A] == {"x": 123, "y": 45, "Pinned": True}


# ---------------------------------------------------------------- the open chat follows

def test_the_open_chat_follows_and_gets_its_own_back(graph, main):
    assert main.settings["Character"] == "Own a"
    cl.add_link(main.links, A, MIO, cl.SPEAKS)
    main.links_updated()
    assert main.settings["Character"] == "Mio" and main.client.preset == "tsundere"
    main.save_chat()
    stored = json.loads((paths.CHAT_DIR / "a.json").read_text(encoding="utf-8"))["Settings"]
    assert stored["Character"] == "Own a" and stored["Pretext"] == "own a"
    cl.remove_link(main.links, A, MIO, cl.SPEAKS)
    main.links_updated()
    assert main.settings["Character"] == "Own a" and main.client.preset == "own a"


def test_a_change_in_the_chat_stays_when_the_personality_changes(graph, main):
    cl.add_link(main.links, A, MIO, cl.SPEAKS)
    main.links_updated()
    main.apply_settings("a", {**main.settings, "Temperature": 1.5}, "Mio")
    assert cl.overrides_of(main.links, "a") == ["Temperature"]
    personalities.save("Mio", {**personalities.load("Mio"), "Pretext": "kuudere", "Temperature": 0.2})
    main.links_updated()
    assert main.client.preset == "kuudere" and main.settings["Temperature"] == 1.5
    main.load_chat(str(paths.CHAT_DIR / "b.json"))
    main.load_chat(str(paths.CHAT_DIR / "a.json"))
    assert main.client.preset == "kuudere" and main.settings["Temperature"] == 1.5


def test_a_new_chat_can_start_as_a_personality(graph, main):
    main.create_chat("new one", {**main.own_settings, "Character": "Mio", "Pretext": "tsundere", "Temperature": 1.9},
                     "Mio")
    assert main.personality_name == "Mio" and main.settings["Temperature"] == 1.9
    assert cl.speaks_as(main.links, "new one") == "Mio"


def test_renaming_the_open_chat_keeps_its_links(graph, main):
    cl.add_link(main.links, A, WORK, cl.MEMBER)
    main.apply_settings("renamed", main.settings)
    assert cl.projects_of(main.links, "renamed") == ["Work"]
    assert cl.node(cl.CHAT, "renamed") in graph.nodes and A not in graph.nodes


def test_deleting_a_hub_keeps_chats_and_notes(graph, main, monkeypatch):
    monkeypatch.setattr(graph, "_confirm", lambda *a: True)
    cl.add_link(main.links, A, MIO, cl.SPEAKS)
    main.links_updated()
    note = paths.personality_memory_dir("Mio") / "n.md"
    note.parent.mkdir(parents=True)
    note.write_text("x", encoding="utf-8")
    graph.delete_hub(cl.PERSONALITY, "Mio")
    assert "Mio" not in main.links["Personalities"] and main.settings["Character"] == "Own a"
    assert (paths.PERSONALITY_DIR / "Deleted" / "Mio" / "memory" / "n.md").exists()


def test_the_model_sees_links_with_the_memory_and_only_for_this_chat(graph, main):
    cl.add_link(main.links, A, WORK, cl.MEMBER)
    cl.add_link(main.links, B, WORK, cl.MEMBER)
    main.links_updated()
    note = paths.project_memory_dir("Work") / "rules.md"
    note.parent.mkdir(parents=True)
    note.write_text("Use tabs.", encoding="utf-8")
    text = "\n".join(m["content"] for m in main.client.build_messages([{"role": "user", "content": "hi"}]))
    assert "Use tabs." in text and "- b (same project: Work): hello from b" in text
    main.load_chat(str(paths.CHAT_DIR / "c.json"))
    text = "\n".join(m["content"] for m in main.client.build_messages([{"role": "user", "content": "hi"}]))
    assert "Use tabs." not in text and "hello from b" not in text


def test_a_summary_replaces_the_preview(graph, main):
    cl.add_link(main.links, A, WORK, cl.MEMBER)
    cl.add_link(main.links, B, WORK, cl.MEMBER)
    main.links_updated()
    main.on_chat_summarized("b", 4, "All about plums.")
    assert main.client.toolbox.link_scope.chats["b"].summary == "All about plums."
