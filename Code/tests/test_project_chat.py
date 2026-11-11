"""A project's own chat hands out work to sessions, which answer in the background (Widgets/projectWork.py)."""

import json
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chat_links as cl  # noqa: E402
import llmClient  # noqa: E402
import paths  # noqa: E402
import personalities  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QApplication.instance() or QApplication([])

MODELS = [{"id": "llama3:8b", "capabilities": ["completion", "tools"]},
          {"id": "gemma4-heretic:latest", "capabilities": ["completion", "thinking", "tools", "vision"]},
          {"id": "gemma4:12b", "capabilities": ["completion", "thinking", "tools", "vision"]},
          {"id": "gemma4:31b", "capabilities": ["completion", "thinking", "tools", "vision"]}]


def answer(self, history):
    """The model, answering at once."""
    self.done.emit({"content": f"Done: {history[-1]['content']}", "thinking": "", "sources": [], "tools": [],
                    "images": [], "usage": {}, "timings": {}, "aborted": False})


@pytest.fixture
def main(tmp_path, monkeypatch):
    for name, value in (("CLIENT_DATA_DIR", tmp_path), ("SAVE_DIR", tmp_path / "Save"),
                        ("CHAT_DIR", tmp_path / "Save" / "Chat"), ("CHAT_INDEX_FILE", tmp_path / "Save" / ".temp.json"),
                        ("LINKS_FILE", tmp_path / "Save" / "links.json"), ("PROJECT_DIR", tmp_path / "Save" / "Projects"),
                        ("PERSONALITY_DIR", tmp_path / "Save" / "Personalities"), ("AUDIO_DIR", tmp_path / "Audio")):
        monkeypatch.setattr(paths, name, value)
    paths.ensure_data_dirs()
    for chat in ("a", "b"):
        (paths.CHAT_DIR / f"{chat}.json").write_text(json.dumps({
            "Name": chat, "Settings": {"Model": "llama3:8b"},
            "Chat": [{"role": "user", "content": f"hello from {chat}"}]}), encoding="utf-8")
    monkeypatch.setattr(llmClient.LLMClient, "list_models", lambda self: MODELS)
    monkeypatch.setattr(llmClient.LLMClient, "generate", answer)
    from Widgets import chatMain
    window = chatMain.ChatMain()
    window.refresh_models()
    window.load_chat(str(paths.CHAT_DIR / "a.json"))
    cl.add_hub(window.links, cl.PROJECT, "Work")
    window.links_updated()
    yield window
    window.close()


def saved(chat) -> list:
    return json.loads((paths.CHAT_DIR / f"{chat}.json").read_text(encoding="utf-8"))["Chat"]


def test_the_project_chat_starts_on_gemma_and_leads(main):
    from Widgets import projectWork
    assert projectWork.lead_model(main.models) == "gemma4:31b"
    main.open_project_chat("Work")
    assert cl.lead_of(main.links, "Work") == "Work" == main.chat_name()
    assert main.is_lead() and main.settings["Model"] == "gemma4:31b" and main.client.think
    assert main.client.toolbox.project is main.desk and "technical lead" in main.client.role_note
    assert {"start_session", "assign_task", "list_sessions"} <= {t.name for t in main.client.toolbox.active()}
    combo = main.model_combo
    assert not combo.model().item(combo.findData("llama3:8b")).isEnabled()     # can't think: greyed out
    assert combo.model().item(combo.findData("gemma4:12b")).isEnabled()

    main.qs_chat("a")                                    # an ordinary chat doesn't lead
    assert main.client.toolbox.project is None and main.client.role_note == ""
    assert combo.model().item(combo.findData("llama3:8b")).isEnabled()


def test_a_project_chat_whose_model_cant_think_moves_to_one_that_can(main):
    main.open_project_chat("Work")
    main.settings["Model"] = "llama3:8b"
    main.save_chat()
    main.qs_chat("a")
    main.qs_chat("Work")
    assert main.settings["Model"] == "gemma4:31b"


def test_a_new_session_gets_the_brief_answers_it_and_reports_back(main):
    main.open_project_chat("Work")
    name = main.desk.start("Login page", "Build the login form. Done when it validates emails.")
    assert name == "Login page"
    assert cl.sessions_of(main.links, "Work") == ["Login page"]
    session = saved(name)
    assert session[0] == {**session[0], "role": "user", "from": "Work"}
    assert session[1]["role"] == "assistant" and session[1]["content"].startswith("Done: Build the login form")
    report = main.chat_markdown[-2]                       # the project chat is open: it got the report
    assert report["report"] == name and "Report from session Login page" in report["content"]
    assert saved("Work")[-2]["report"] == name
    reply = main.chat_markdown[-1]                        # and, all sessions done, it answered the report
    assert reply["role"] == "assistant" and reply["content"].startswith("Done: Report from session Login page")
    assert main.runner.state_of(name) == "idle"
    rows = main.desk.sessions()
    assert rows == [{"name": name, "summary": rows[0]["summary"], "state": "idle"}]
    assert "login form" in rows[0]["summary"]


def test_a_session_speaks_as_the_projects_personality(main):
    cl.add_hub(main.links, cl.PERSONALITY, "Mio")
    personalities.save("Mio", {**main.settings, "Character": "Mio", "Pretext": "tsundere", "Model": "llama3:8b"})
    lead = main.project_chat("Work")
    assert cl.add_link(main.links, cl.node(cl.PROJECT, "Work"), cl.node(cl.PERSONALITY, "Mio"), cl.SPEAKS)
    main.qs_chat(lead)
    assert main.personality_name == "Mio" and "juggling" in main.client.role_note
    assert main.settings["Model"] == "gemma4:31b"         # the personality's model doesn't reach the project chat
    name = main.desk.start("Groceries", "Make the shopping list")
    assert cl.speaks_as(main.links, name) == "Mio"


def test_an_existing_chat_joins_the_project_for_its_task(main):
    main.open_project_chat("Work")
    result = main.desk.assign("b", "Check the numbers")
    assert "Linked b to project Work" in result
    assert cl.projects_of(main.links, "b") == ["Work"]
    assert saved("b")[-1]["content"] == "Done: Check the numbers"
    assert main.desk.assign("nope", "x").startswith("Error: there's no chat")
    assert main.desk.assign("Work", "x").startswith("Error: Work is a project's own chat")


def test_the_open_session_answers_in_the_main_window(main):
    main.open_project_chat("Work")
    main.desk.assign("b", "First")
    main.qs_chat("b")
    main.append_message("b", {"role": "user", "content": "Second", "from": "Work"})
    main.runner.add("b", "Work")
    assert main.chat_markdown[-1]["content"] == "Done: Second"      # answered here, where you watch it
    assert saved("Work")[-1]["content"].startswith("Report from session b:\n\nDone: Second")
    main.qs_chat("Work")                                  # opened later, it answers what came in meanwhile
    assert main.chat_markdown[-1]["content"].startswith("Done: Report from session b")


def test_a_session_working_in_the_background_is_read_only_when_open(main, monkeypatch):
    main.open_project_chat("Work")
    waiting = []
    monkeypatch.setattr(llmClient.LLMClient, "generate", lambda self, history: waiting.append(self))
    main.desk.assign("b", "Slow task")
    assert main.runner.busy_with("b")
    main.qs_chat("b")
    assert main.input.isReadOnly() and not main.ui.pushButton_5.isEnabled()
    answer(waiting[0], saved("b"))
    assert not main.input.isReadOnly()
    assert main.chat_markdown[-1]["content"] == "Done: Slow task"     # shown once it's done


def test_the_graph_draws_the_project_as_its_chat(main):
    from Widgets import linkGraph
    lead = main.project_chat("Work")
    main.desk = None
    main.qs_chat("a")
    cl.add_hub(main.links, cl.PERSONALITY, "Mio")
    personalities.save("Mio", {**main.settings, "Character": "Mio", "Pretext": "tsundere"})
    cl.add_link(main.links, cl.node(cl.PROJECT, "Work"), cl.node(cl.PERSONALITY, "Mio"), cl.SPEAKS)
    main.links_updated()
    graph = linkGraph.LinkGraph(main)
    graph.unlinked_check.setChecked(True)
    graph.open()
    try:
        assert cl.node(cl.CHAT, lead) not in graph.nodes
        edge = next(e for e in graph.edges if e.link["Kind"] == cl.SPEAKS)
        assert (edge.start.name, edge.end.name) == ("project:Work", "personality:Mio")
        labels = [label for label, _ in graph._actions("project:Work")]
        assert labels[0] == "Open project chat"
        graph._actions("project:Work")[0][1]()
        assert main.chat_name() == lead
    finally:
        graph.close()


def test_the_project_chat_answers_reports_three_times_per_message_at_most(main):
    main.open_project_chat("Work")
    for i in range(5):
        main.desk.assign("b", f"Task {i}")
    reports = [m for m in main.chat_markdown if m.get("report")]
    answers = [m for m in main.chat_markdown if m["role"] == "assistant"]
    assert len(reports) == 5 and len(answers) == 3
    main.input.setPlainText("Next?")
    main.sendMessage()                                    # your message: it may follow up again
    main.desk.assign("b", "Task 5")
    assert main.chat_markdown[-1]["role"] == "assistant"
