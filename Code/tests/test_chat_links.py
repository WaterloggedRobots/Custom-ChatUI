"""Tests of chat links: the rules (chat_links.py), personalities, the physics and the model's context."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import chat_links as cl  # noqa: E402
import chat_settings  # noqa: E402
import graph_layout  # noqa: E402
import paths  # noqa: E402
import personalities  # noqa: E402
from local_tools import ToolBox  # noqa: E402
from local_tools.linked import LinkedChat, LinkScope  # noqa: E402

A, B, C = cl.node(cl.CHAT, "a"), cl.node(cl.CHAT, "b"), cl.node(cl.CHAT, "c")
WORK, HOME = cl.node(cl.PROJECT, "Work"), cl.node(cl.PROJECT, "Home")
MIO, KAI = cl.node(cl.PERSONALITY, "Mio"), cl.node(cl.PERSONALITY, "Kai")


@pytest.fixture
def data():
    d = cl.empty()
    for kind, name in ((cl.PROJECT, "Work"), (cl.PROJECT, "Home"), (cl.PERSONALITY, "Mio"), (cl.PERSONALITY, "Kai")):
        assert cl.add_hub(d, kind, name)
    return d


def test_what_can_link_to_what():
    assert cl.link_kinds(A, WORK) == cl.link_kinds(WORK, A) == [cl.MEMBER]
    assert cl.link_kinds(A, MIO) == [cl.SPEAKS, cl.READS]
    assert cl.link_kinds(WORK, MIO) == [cl.READS]
    assert cl.link_kinds(A, B) == [cl.MAKE_PROJECT]
    assert cl.link_kinds(WORK, HOME) == cl.link_kinds(MIO, KAI) == cl.link_kinds(A, A) == []


def test_links_are_stored_one_way_and_a_chat_speaks_as_one(data):
    assert cl.add_link(data, MIO, A, cl.SPEAKS)                # either order
    assert data["Links"] == [{"From": A, "To": MIO, "Kind": cl.SPEAKS}]
    assert not cl.add_link(data, A, MIO, cl.SPEAKS)            # already there
    assert not cl.add_link(data, A, WORK, cl.SPEAKS)           # wrong kind for these nodes
    assert cl.add_link(data, A, KAI, cl.SPEAKS)
    assert cl.speaks_as(data, "a") == "Kai" and not cl.has_link(data, A, MIO, cl.SPEAKS)
    assert cl.add_link(data, A, MIO, cl.READS) and cl.speaks_as(data, "a") == "Kai"
    cl.remove_link(data, MIO, A, cl.READS)
    assert not cl.has_link(data, A, MIO, cl.READS)


def test_changing_personality_forgets_the_overrides(data):
    cl.add_link(data, A, MIO, cl.SPEAKS)
    assert cl.set_overrides(data, "a", ["Temperature"]) and not cl.set_overrides(data, "a", ["Temperature"])
    cl.add_link(data, A, KAI, cl.SPEAKS)
    assert cl.overrides_of(data, "a") == []
    cl.set_overrides(data, "a", ["Model"])
    cl.remove_hub(data, cl.PERSONALITY, "Kai")
    assert cl.overrides_of(data, "a") == [] and cl.speaks_as(data, "a") is None


def test_a_floating_chat_reads_nothing(data):
    assert cl.readable_chats(data, "a") == {}


def test_project_sessions_read_each_other_but_not_other_projects(data):
    cl.add_link(data, A, WORK, cl.MEMBER)
    cl.add_link(data, B, WORK, cl.MEMBER)
    cl.add_link(data, C, HOME, cl.MEMBER)
    assert list(cl.readable_chats(data, "a")) == ["b"]
    assert cl.readable_chats(data, "c") == {}


def test_personality_reading_goes_one_way(data):
    cl.add_link(data, A, MIO, cl.SPEAKS)
    cl.add_link(data, B, MIO, cl.READS)
    assert list(cl.readable_chats(data, "a")) == ["b"]
    assert cl.readable_chats(data, "b") == {}       # being readable by Mio gives b nothing back


def test_speaking_as_a_personality_doesnt_share_those_chats(data):
    cl.add_link(data, A, MIO, cl.SPEAKS)
    cl.add_link(data, B, MIO, cl.SPEAKS)
    assert cl.readable_chats(data, "a") == {}


def test_reading_a_project_reaches_its_chats_and_new_ones(data):
    cl.add_link(data, A, MIO, cl.SPEAKS)
    cl.add_link(data, WORK, MIO, cl.READS)
    cl.add_link(data, B, WORK, cl.MEMBER)
    assert list(cl.readable_chats(data, "a")) == ["b"]
    cl.add_link(data, C, WORK, cl.MEMBER)
    assert sorted(cl.readable_chats(data, "a")) == ["b", "c"]


def test_private_keeps_a_chat_from_personalities_but_not_its_project(data):
    cl.add_link(data, A, MIO, cl.SPEAKS)
    cl.add_link(data, B, MIO, cl.READS)
    cl.add_link(data, C, WORK, cl.MEMBER)
    cl.add_link(data, WORK, MIO, cl.READS)
    cl.set_private(data, "b", True)
    cl.set_private(data, "c", True)
    assert cl.readable_chats(data, "a") == {}
    cl.add_link(data, A, WORK, cl.MEMBER)
    assert list(cl.readable_chats(data, "a")) == ["c"]


def test_rename_follows_everything(data):
    cl.add_link(data, A, WORK, cl.MEMBER)
    cl.set_private(data, "a", True)
    data["Layout"][A] = {"x": 1, "y": 2, "Pinned": True}
    data["Summaries"]["a"] = {"Upto": 4, "Text": "x"}
    cl.rename_node(data, cl.CHAT, "a", "z")
    z = cl.node(cl.CHAT, "z")
    assert data["Links"][0]["From"] == z and data["Private"] == ["z"]
    assert z in data["Layout"] and "z" in data["Summaries"]
    cl.rename_node(data, cl.PROJECT, "Work", "Job")
    assert data["Links"][0]["To"] == cl.node(cl.PROJECT, "Job") and "Job" in data["Projects"]


def test_normalize_and_saving(data, tmp_path):
    cl.add_link(data, A, WORK, cl.MEMBER)
    cl.add_link(data, B, WORK, cl.MEMBER)
    cl.set_private(data, "b", True)
    path = tmp_path / "links.json"
    cl.save(data, path)
    loaded = cl.load(path, ["a"])                  # b was deleted
    assert [l["From"] for l in loaded["Links"]] == [A] and loaded["Private"] == []
    junk = {"Projects": ["ok", 3, "a/b"], "Links": [{"From": 1}, "x"], "Layout": {A: {"x": "1"}}, "Summaries": []}
    assert cl.normalize(junk, ["a"]) == {**cl.empty(), "Projects": ["ok"]}
    assert cl.normalize(None, []) == cl.empty()


def test_a_broken_file_is_kept(tmp_path):
    path = tmp_path / "links.json"
    path.write_text("{ not json", encoding="utf-8")
    assert cl.load(path, []) == cl.empty()
    assert (tmp_path / "links.json.bad").read_text(encoding="utf-8") == "{ not json"


def test_summary_due(data):
    assert not cl.summary_due(data, "a", 100)      # not linked
    cl.add_link(data, A, WORK, cl.MEMBER)
    assert not cl.summary_due(data, "a", 3) and cl.summary_due(data, "a", 4)
    data["Summaries"]["a"] = {"Upto": 4, "Text": "x"}
    assert not cl.summary_due(data, "a", 13) and cl.summary_due(data, "a", 14)


# ---------------------------------------------------------------- personalities

def settings(**over):
    return {**chat_settings.defaults(), **over}


def test_a_chat_follows_its_personality_and_gets_its_own_back():
    own = settings(Character="Bot", Pretext="own", Model="m1", **{"Tool Files": False})
    mio = settings(Character="Mio", Pretext="tsundere", Model="", Avatar=True, TTS=True)
    run = personalities.resolve(own, mio)
    assert (run["Character"], run["Pretext"], run["Avatar"]) == ("Mio", "tsundere", True)
    assert run["Model"] == "m1" and run["Tool Files"] is False     # no saved model; tools stay with the chat
    new_own, overrides = personalities.split(run, own, mio)
    assert overrides == [] and new_own["Character"] == "Bot" and new_own["Pretext"] == "own"
    assert personalities.resolve(new_own, None)["Pretext"] == "own"


def test_overrides_survive_edits_to_the_personality():
    own = settings(Pretext="own")
    mio = settings(Character="Mio", Temperature=0.3, Model="big")
    run = personalities.resolve(own, mio)
    run["Temperature"] = 1.1
    new_own, overrides = personalities.split(run, own, mio)
    assert overrides == ["Temperature"]
    mio2 = {**mio, "Character": "Mio 2", "Temperature": 0.5}
    run2 = personalities.resolve(new_own, mio2, overrides)
    assert run2["Character"] == "Mio 2" and run2["Temperature"] == 1.1


def test_personality_files(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PERSONALITY_DIR", tmp_path)
    personalities.save("Mio", settings(Character="Mio", Pretext="hi", **{"Tool Run": False}))
    loaded = personalities.load("Mio")
    assert loaded["Pretext"] == "hi" and loaded["Tool Run"] is True      # only who's talking is saved
    assert personalities.load("nobody") is None


def test_removing_a_folder_never_destroys_it(tmp_path):
    folder = tmp_path / "Work"
    (folder / "memory").mkdir(parents=True)
    (folder / "memory" / "a.md").write_text("x", encoding="utf-8")
    personalities.remove_folder(folder)
    assert not folder.exists() and (tmp_path / "Deleted" / "Work" / "memory" / "a.md").exists()
    assert personalities.rename_folder(tmp_path / "gone", tmp_path / "new")
    (tmp_path / "taken").mkdir()
    assert not personalities.rename_folder(tmp_path / "Deleted", tmp_path / "taken")


# ---------------------------------------------------------------- the physics

def test_layout_settles_and_keeps_pins():
    layout = graph_layout.Layout()
    for i, n in enumerate("abcd"):
        layout.place(n, i, 0)
    layout.place("pin", 300, 300, pinned=True)
    layout.edges = [("a", "b"), ("b", "c")]
    steps = 0
    while not layout.settled:
        layout.step()
        steps += 1
    assert 0 < steps < 500
    assert (layout.x["pin"], layout.y["pin"]) == (300, 300)
    assert abs(layout.x["a"] - layout.x["d"]) + abs(layout.y["a"] - layout.y["d"]) > 40    # spread out
    layout.place("a", 5, 5)
    layout.warm(0.5)
    layout.step(held="a")
    assert (layout.x["a"], layout.y["a"]) == (5, 5)


# ---------------------------------------------------------------- the model's side

def write_chat(folder, name, messages):
    path = folder / f"{name}.json"
    path.write_text(json.dumps({"Name": name, "Chat": messages}), encoding="utf-8")
    return path


@pytest.fixture
def box(tmp_path):
    box = ToolBox(str(tmp_path))
    box.set_groups(())
    path = write_chat(tmp_path, "other", [{"role": "user", "content": "the secret word is plum"},
                                          {"role": "assistant", "content": "noted"}])
    write_chat(tmp_path, "unlinked", [{"role": "user", "content": "plum too"}])
    box.link_scope = LinkScope(chats={"other": LinkedChat(path, "same project: Work")})
    return box


def test_linked_tools_only_read_what_is_linked(box):
    names = {t.name for t in box.active()}
    assert {"list_linked_chats", "search_linked_chats", "read_linked_chat"} == names
    assert not any(box.needs_approval(n) for n in names)
    assert "other" in box.run("list_linked_chats", {})
    found = box.run("search_linked_chats", {"query": "PLUM secret"})
    assert "other #0" in found and "unlinked" not in found
    assert "the secret word" in box.run("read_linked_chat", {"chat": "other"})
    assert "isn't linked" in box.run("read_linked_chat", {"chat": "unlinked"})
    assert "isn't linked" in box.run("read_linked_chat", {"chat": "../unlinked"})
    assert "#1 assistant" in box.run("read_linked_chat", {"chat": "other", "start": 1})


def test_no_links_means_no_tools(box):
    box.link_scope = None
    assert box.active() == [] and box.links_note(1000, True) == ""


def test_the_list_shrinks_to_fit_and_says_so(tmp_path):
    box = ToolBox(str(tmp_path))
    chats = {f"c{i}": LinkedChat(tmp_path / "none", "why", summary="x" * 100) for i in range(10)}
    box.link_scope = LinkScope(chats=chats)
    assert "c9" in box.links_note(100000, True)
    short = box.links_note(300, False)
    assert "… and 8 more" in short and "search_linked_chats" not in short


def test_notes_come_nearest_first_and_the_chats_own_are_cut_last(tmp_path):
    box = ToolBox(str(tmp_path))
    box.set_groups(())
    box.memory_dir = tmp_path / "chat"
    project, personality = tmp_path / "project", tmp_path / "personality"
    box.link_scope = LinkScope(places={"project:Work": project, "personality:Mio": personality})
    for place, text in (("chat", "OWN"), ("project:Work", "PROJECT"), ("personality:Mio", "MIO")):
        assert "Saved" in box.run("save_memory", {"name": "n", "content": text, "place": place})
    note = box.memory_note()
    assert note.index("OWN") < note.index("PROJECT") < note.index("MIO")
    assert "project:Work" in box.system_note()
    assert "Error" in box.run("save_memory", {"name": "n", "content": "x", "place": "project:Other"})

    (box.memory_dir / "z big.md").write_text("y" * 30000, encoding="utf-8")
    note = box.memory_note()
    assert "OWN" in note and "MIO" not in note

    box.memory_dir = None                         # memory off: no notes from anywhere
    assert box.memory_note() == "" and "save_memory" not in {t.name for t in box.active()}
