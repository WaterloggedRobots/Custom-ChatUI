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
    monkeypatch.setattr(llmClient.LLMClient, "summarize_chat", lambda self, chat, history: None)   # no real server
    monkeypatch.setattr(llmClient.LLMClient, "check_promise", lambda self, chat, reply, sessions: None)
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
    result = main.desk.start("Login page", "Build the login form. Done when it validates emails.")
    name = "Login page"
    assert result.startswith("Started session Login page on gemma4:31b")
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
    result = main.desk.start("Groceries", "Make the shopping list", "llama3:8b")
    assert cl.speaks_as(main.links, "Groceries") == "Mio"
    assert "runs on gemma4:31b, like you, not llama3:8b, so Mio stays herself" in result
    main.qs_chat("Groceries")
    assert main.settings["Model"] == "gemma4:31b"         # the same model as the project chat, not Mio's

    main.save_personality("Mio", {**main.settings, "Character": "Mio", "Pretext": "tsundere", "Model": "gemma4:12b"})
    main.qs_chat(lead)                                    # her own model thinks and calls tools: the project takes it
    assert main.settings["Model"] == "gemma4:12b"
    main.desk.start("Laundry", "Plan the laundry")
    main.qs_chat("Laundry")
    assert main.settings["Model"] == "gemma4:12b"


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


def test_without_a_personality_each_session_gets_the_model_it_needs(main):
    main.open_project_chat("Work")
    assert "- llama3:8b (tools)" in main.client.role_note
    assert "on llama3:8b" in main.desk.start("Quick", "Rename the files", "llama3:8b")
    main.qs_chat("Quick")
    assert main.settings["Model"] == "llama3:8b"
    main.qs_chat("Work")
    assert main.desk.start("Bad", "x", "nope:1b").startswith("Error: nope:1b isn't a model on the server")
    main.models["seer:7b"] = {"id": "seer:7b", "capabilities": ["completion", "vision"]}
    main.qs_chat("Work")
    assert "seer:7b" not in main.client.role_note             # it can't call tools, so it's not offered
    assert main.desk.start("Bad", "x", "seer:7b").startswith("Error: seer:7b isn't a model on the server")


def test_starting_fresh_keeps_the_old_chat_and_hands_over_a_context_md(main, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from Widgets import projectWork
    cl.add_hub(main.links, cl.PERSONALITY, "Mio")
    personalities.save("Mio", {**main.settings, "Character": "Mio", "Pretext": "tsundere", "Model": ""})
    lead = main.project_chat("Work")
    cl.add_link(main.links, cl.node(cl.PROJECT, "Work"), cl.node(cl.PERSONALITY, "Mio"), cl.SPEAKS)
    main.qs_chat(lead)
    rules = paths.chat_memory_dir(lead)
    rules.mkdir(parents=True)
    (rules / "rules.md").write_text("Always ask before buying", encoding="utf-8")
    main.desk.start("Login page", "Build it")
    sent = {}

    class Reply:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "## Goal\nShip the login page"}}]}

    def post(url, json=None, timeout=None):
        sent.update(json)
        return Reply()

    monkeypatch.setattr(projectWork.requests, "post", post)
    main.start_fresh("Work")
    for _ in range(200):
        QCoreApplication.processEvents()
        if main._handing_over is None:
            break
        import time
        time.sleep(0.01)
    told = sent["messages"][1]["content"]
    assert "Login page [idle]: Build it" in told and "Report from session" in told
    assert main.chat_name() == "Work" and main.chat_markdown == []        # the new one, empty
    assert cl.lead_of(main.links, "Work") == "Work" and main.links["Past"] == {"Work": ["Work (old)"]}
    assert (paths.chat_memory_dir("Work") / "context.md").read_text(encoding="utf-8") == "## Goal\nShip the login page\n"
    assert (paths.chat_memory_dir("Work") / "rules.md").exists()           # its notes come along
    assert "Ship the login page" in main.client.toolbox.memory_note()     # it's in every reply
    assert cl.speaks_as(main.links, "Work") == "Mio"
    assert saved("Work (old)")[-1]["role"] == "assistant"                   # the old one keeps its history
    reach = cl.readable_chats(main.links, "Work")
    assert set(reach) == {"Login page", "Work (old)"}
    assert cl.sessions_of(main.links, "Work") == ["Login page"]
    main.desk.assign("Login page", "Add a logout button")                  # reports reach the new one
    assert any(m.get("report") == "Login page" for m in saved("Work"))


def test_an_empty_project_chat_is_fresh_already(main):
    main.open_project_chat("Work")
    main.start_fresh("Work")
    assert main._handing_over is None and "fresh already" in main.status_label.text()


def test_a_fenced_handover_is_unwrapped():
    from Widgets.projectWork import unfence
    assert unfence("```markdown\n## Goal\nShip it\n```\n") == "## Goal\nShip it"
    assert unfence("## Goal\n```py\nx\n```") == "## Goal\n```py\nx\n```"


def test_a_brief_given_while_a_session_works_is_answered_next(main, monkeypatch):
    main.open_project_chat("Work")
    waiting = []
    monkeypatch.setattr(llmClient.LLMClient, "generate", lambda self, history: waiting.append(list(history)))
    main.desk.assign("b", "First task")
    main.desk.assign("b", "Second task")                 # the manager adds a task while b works on the first
    assert main.runner.state_of("b") == "working" and main.runner.queue == ["b"]
    answer(main.runner.client, waiting[0])
    contents = [m["content"] for m in saved("b")]
    assert contents[-3:] == ["First task", "Done: First task", "Second task"]   # its answer went before the brief
    assert waiting[1][-1]["content"] == "Second task"     # so the second one is what it works on now
    answer(main.runner.client, waiting[1])
    assert saved("b")[-1]["content"] == "Done: Second task"
    reports = [m["content"] for m in saved("Work") if m.get("report")]
    assert any("Done: First task" in r for r in reports) and any("Done: Second task" in r for r in reports)


def test_a_session_with_nothing_to_answer_still_reports(main):
    main.open_project_chat("Work")
    cl.add_link(main.links, cl.node(cl.CHAT, "b"), cl.node(cl.PROJECT, "Work"), cl.MEMBER)
    data = json.loads((paths.CHAT_DIR / "b.json").read_text(encoding="utf-8"))
    data["Chat"].append({"role": "assistant", "content": "Already answered"})
    (paths.CHAT_DIR / "b.json").write_text(json.dumps(data), encoding="utf-8")
    main.runner.add("b", "Work")
    assert main.runner.state_of("b") == "idle"
    assert any("Session b couldn't finish its task: its chat had no new task" in m["content"]
               for m in main.chat_markdown + saved("Work"))


def test_the_bottom_bar_and_list_show_what_sessions_do(main, monkeypatch):
    main.open_project_chat("Work")
    waiting = []
    monkeypatch.setattr(llmClient.LLMClient, "generate", lambda self, history: waiting.append(self))
    assert main.sessions_text() == "" and main.sessions_button.isHidden()
    main.desk.assign("b", "Slow task")
    main.desk.assign("a", "Next task")
    assert main.sessions_text().startswith("⏳ b working in the background 0:0")
    assert main.sessions_text().endswith("· 🕓 1 queued") and not main.sessions_button.isHidden()
    tree = main.chat_list
    rows = {tree.topLevelItem(i).data(0, 0x0100)[1]: tree.topLevelItem(i) for i in range(tree.topLevelItemCount())}
    project = rows["Work"]
    kids = {project.child(k).text(0): project.child(k) for k in range(project.childCount())}
    assert tree.state_of_row(kids["b"]) == "working" and tree.state_of_row(kids["a"]) == "queued"
    assert tree.state_of_row(project) == "working"         # seen with the group closed too
    main.runner.set_waiting(True)                          # its command waits for your OK
    assert main.sessions_text().startswith("✋ b needs your OK") and tree.state_of_row(kids["b"]) == "waiting"
    main.runner.set_waiting(False)
    main._open_working_session()
    assert main.chat_name() == "b"
    answer(waiting[0], saved("b"))
    assert main.sessions_text().startswith("⏳ a working")
    answer(waiting[1], saved("a"))
    assert main.sessions_text() == "" and main.sessions_button.isHidden()


def test_tool_use_reaches_the_model_on_the_next_message_not_in_its_own_words():
    client = llmClient.LLMClient()
    client.model_name = "gemma4:31b"
    history = [{"role": "user", "content": "Plan it"},
               {"role": "assistant", "content": "Handed it out.",
                "tools": [{"name": "assign_task", "summary": "Hand Game Designer a task", "state": "ok"}]},
               {"role": "user", "content": "And now?"},
               {"role": "assistant", "content": "[Tools used for this answer:\n- Hand Game Designer a task (ok)]\n\n"
                                                "Passed it on."},                     # one the model made up
               {"role": "user", "content": "Did it go?"}]
    sent = [m for m in client.build_messages(history, None) if m["role"] != "system"]
    assert [m["content"] for m in sent if m["role"] == "assistant"] == ["Handed it out.", "Passed it on."]
    assert sent[2]["content"].startswith("[Note from the app, not the user: your previous reply used these tools:\n"
                                         "- Hand Game Designer a task (ok)]")
    assert sent[4]["content"] == "Did it go?"           # a made-up note isn't passed on as tool use
    assert llmClient.strip_fake_tools_note("[Tools used for this answer:\n- x (ok)]\n\nHi") == ("Hi", True)
    assert llmClient.strip_fake_tools_note("Hi [Tools used for this answer:]") == ("Hi [Tools used for this answer:]",
                                                                                  False)


def test_a_promised_hand_off_without_a_tool_call_gets_one_reminder(main, monkeypatch):
    main.open_project_chat("Work")
    replies = iter(["[Tools used for this answer:\n- Hand b a task (ok)]\n\nI've passed it to b.",
                    "I'll have b check them.", "Waiting for you.", "Done it."])
    asked = []

    def reply(self, history):
        asked.append(list(history))
        self.done.emit({"content": next(replies), "tools": [], "aborted": False})
    monkeypatch.setattr(llmClient.LLMClient, "generate", reply)
    checked = []
    monkeypatch.setattr(llmClient.LLMClient, "check_promise", lambda self, chat, text, sessions: checked.append(text))
    main.input.setPlainText("Give b the numbers")
    main.sendMessage(from_key=True)
    QApplication.processEvents()                       # a made-up tool note is a promise without asking
    assert saved("Work")[1]["content"] == "I've passed it to b."
    note = saved("Work")[2]
    assert note["app_note"] and "you called no tool" in note["content"] and asked[1][-1] is not None
    assert checked == ["I'll have b check them."]       # the reminded answer is checked like any other
    main.on_promise_checked("Work", ["b"])               # it promised again: once is enough, you're told
    assert len(asked) == 2 and main.status_label.text().startswith("⚠ Work said b would get a task")
    main.input.setPlainText("Try again")
    main.sendMessage(from_key=True)                      # your next message: it can be reminded again
    main.on_promise_checked("Work", ["b"])
    assert len(asked) == 4 and saved("Work")[-2]["app_note"]


def test_enter_mid_answer_queues_your_message_instead_of_stopping(main, monkeypatch):
    main.open_project_chat("Work")
    asked = []
    monkeypatch.setattr(llmClient.LLMClient, "generate", lambda self, history: asked.append(list(history)))
    main.input.setPlainText("First")
    main.sendMessage(from_key=True)
    assert main.is_answering()
    stopped = []
    monkeypatch.setattr(main.client, "abort_generation", lambda: stopped.append(1))
    main.input.setPlainText("Second")
    main.sendMessage(from_key=True)                       # Enter while it answers: queued, not a Stop
    assert not stopped and main.status_label.text().startswith("📨") and main.input.toPlainText() == ""
    assert len(asked) == 1
    answer(main.client, asked[0])                         # the answer ends: yours goes out
    assert asked[1][-1]["content"] == "Second" and [m["content"] for m in asked[1][-3:]] == [
        "First", "Done: First", "Second"]
    main.sendMessage()                                    # the button, which reads Stop, still stops it
    assert stopped == [1]
    answer(main.client, asked[1])
    assert saved("Work")[-1]["content"] == "Done: Second"
