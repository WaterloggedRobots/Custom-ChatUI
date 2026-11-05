"""Links between chats, projects and personalities, as drawn in the link graph.

Chats are the nodes that hold conversations; projects and personalities are hubs
they link to. A chat with no links is on its own and shares nothing. There are
three kinds of link, always stored from the lower kind of node to the higher:

  member   chat -> project       the chat is a session of the project
  speaks   chat -> personality   the chat talks as this personality (one per chat)
  reads    chat or project -> personality
                                 the personality can look through it, and a
                                 project's "reads" covers all of its chats

It all lives in Save/links.json:
  {"Projects": [names], "Personalities": [names],
   "Links": [{"From": node, "To": node, "Kind": kind}],
   "Private": [chat names], "Layout": {node: {"x", "y", "Pinned"}},
   "Summaries": {chat: {"Upto": messages covered, "Text": one line}},
   "Overrides": {chat: [settings it changed from its personality]}}
A node is "<type>:<name>", such as "chat:Avatar part 2".

No Qt here, so it can be tested on its own (tests/test_chat_links.py).
"""

import json
import os
from pathlib import Path

CHAT, PROJECT, PERSONALITY = "chat", "project", "personality"
MEMBER, SPEAKS, READS = "member", "speaks", "reads"
_RANK = {CHAT: 0, PROJECT: 1, PERSONALITY: 2}
MAKE_PROJECT = "make project"       # what link_kinds offers for two chats: put them in a new project
FIRST_SUMMARY_AT = 4                # messages before a linked chat gets its one-line summary
SUMMARY_STEP = 10                   # messages between refreshes after that


def node(kind: str, name: str) -> str:
    return f"{kind}:{name}"


def split_node(text: str):
    """"chat:a" -> ("chat", "a")."""
    kind, _, name = text.partition(":")
    return kind, name


def clean_name(name: str) -> str:
    """A project or personality name that's safe as a folder name; "" if nothing is left."""
    return "".join(c for c in str(name) if c not in '<>:"/\\|?*' and ord(c) >= 32).strip().rstrip(".")


def empty() -> dict:
    return {"Projects": [], "Personalities": [], "Links": [], "Private": [], "Layout": {}, "Summaries": {}, "Overrides": {}}


# ---------------------------------------------------------------------------
# Reading and saving
# ---------------------------------------------------------------------------

def _names(value) -> list:
    seen = []
    for name in value if isinstance(value, list) else []:
        if isinstance(name, str) and clean_name(name) == name and name and name not in seen:
            seen.append(name)
    return seen


def normalize(data, chats) -> dict:
    """Valid links from whatever `data` holds; anything about a chat not in `chats` is dropped."""
    out = empty()
    if not isinstance(data, dict):
        return out
    out["Projects"] = _names(data.get("Projects"))
    out["Personalities"] = _names(data.get("Personalities"))
    known = {node(CHAT, c) for c in chats}
    known |= {node(PROJECT, p) for p in out["Projects"]} | {node(PERSONALITY, p) for p in out["Personalities"]}
    for link in data.get("Links") if isinstance(data.get("Links"), list) else []:
        if isinstance(link, dict) and link.get("From") in known and link.get("To") in known:
            add_link(out, link["From"], link["To"], link.get("Kind"))
    out["Private"] = [c for c in _names(data.get("Private")) if c in set(chats)]
    layout = data.get("Layout") if isinstance(data.get("Layout"), dict) else {}
    for name, place in layout.items():
        if name in known and isinstance(place, dict) and all(
                isinstance(place.get(k), (int, float)) and not isinstance(place.get(k), bool) for k in "xy"):
            out["Layout"][name] = {"x": place["x"], "y": place["y"], "Pinned": place.get("Pinned") is True}
    summaries = data.get("Summaries") if isinstance(data.get("Summaries"), dict) else {}
    for chat, entry in summaries.items():
        if chat in set(chats) and isinstance(entry, dict) and isinstance(entry.get("Text"), str) \
                and isinstance(entry.get("Upto"), int):
            out["Summaries"][chat] = {"Upto": entry["Upto"], "Text": entry["Text"]}
    overrides = data.get("Overrides") if isinstance(data.get("Overrides"), dict) else {}
    for chat, keys in overrides.items():
        if speaks_as(out, chat) and isinstance(keys, list) and keys:
            out["Overrides"][chat] = [k for k in keys if isinstance(k, str)]
    return out


def load(path, chats) -> dict:
    """The saved links. A file that can't be read is kept as <name>.bad instead of being overwritten."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    except (OSError, ValueError) as e:
        print(f"Can't read {path.name}: {e}")
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return empty()
    return normalize(data, chats)


def save(data: dict, path) -> None:
    """Written to a temporary file first, so a crash can't leave half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def link_kinds(a: str, b: str) -> list:
    """What linking these two nodes can mean, in the order to offer it. [] if they can't link."""
    if a == b:
        return []
    kinds = {split_node(a)[0], split_node(b)[0]}
    if kinds == {CHAT}:
        return [MAKE_PROJECT]
    if kinds == {CHAT, PROJECT}:
        return [MEMBER]
    if kinds == {CHAT, PERSONALITY}:
        return [SPEAKS, READS]
    if kinds == {PROJECT, PERSONALITY}:
        return [READS]
    return []


def _ordered(a: str, b: str):
    return (a, b) if _RANK.get(split_node(a)[0], 9) < _RANK.get(split_node(b)[0], 9) else (b, a)


def has_link(data: dict, a: str, b: str, kind: str) -> bool:
    start, end = _ordered(a, b)
    return any(l["From"] == start and l["To"] == end and l["Kind"] == kind for l in data["Links"])


def add_link(data: dict, a: str, b: str, kind: str) -> bool:
    """Add a link if its kind suits the nodes. "speaks" replaces the chat's earlier personality."""
    if kind == MAKE_PROJECT or kind not in link_kinds(a, b) or has_link(data, a, b, kind):
        return False
    start, end = _ordered(a, b)
    if kind == SPEAKS:
        set_speaks_as(data, split_node(start)[1], None)
    data["Links"].append({"From": start, "To": end, "Kind": kind})
    return True


def remove_link(data: dict, a: str, b: str, kind: str) -> None:
    start, end = _ordered(a, b)
    data["Links"] = [l for l in data["Links"]
                     if not (l["From"] == start and l["To"] == end and l["Kind"] == kind)]
    if kind == SPEAKS:
        data["Overrides"].pop(split_node(start)[1], None)


def links_of(data: dict, name: str) -> list:
    return [l for l in data["Links"] if name in (l["From"], l["To"])]


def speaks_as(data: dict, chat: str):
    """The personality this chat talks as, or None for its own settings."""
    start = node(CHAT, chat)
    return next((split_node(l["To"])[1] for l in data["Links"] if l["From"] == start and l["Kind"] == SPEAKS), None)


def set_speaks_as(data: dict, chat: str, personality) -> None:
    start = node(CHAT, chat)
    data["Links"] = [l for l in data["Links"] if not (l["From"] == start and l["Kind"] == SPEAKS)]
    data["Overrides"].pop(chat, None)     # they were changes from the personality it had
    if personality in data["Personalities"]:
        data["Links"].append({"From": start, "To": node(PERSONALITY, personality), "Kind": SPEAKS})


def overrides_of(data: dict, chat: str) -> list:
    return data["Overrides"].get(chat, [])


def set_overrides(data: dict, chat: str, keys) -> bool:
    """Remember what the chat changed from its personality; True if that's different from before."""
    keys = list(keys)
    if keys == overrides_of(data, chat):
        return False
    if keys:
        data["Overrides"][chat] = keys
    else:
        data["Overrides"].pop(chat, None)
    return True


def projects_of(data: dict, chat: str) -> list:
    start = node(CHAT, chat)
    return sorted(split_node(l["To"])[1] for l in data["Links"] if l["From"] == start and l["Kind"] == MEMBER)


def is_linked(data: dict, chat: str) -> bool:
    return bool(links_of(data, node(CHAT, chat)))


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _list_key(kind: str) -> str:
    return "Projects" if kind == PROJECT else "Personalities"


def add_hub(data: dict, kind: str, name: str) -> bool:
    """A new project or personality; False if the name is empty or taken."""
    names = data[_list_key(kind)]
    if not name or clean_name(name) != name or name in names:
        return False
    names.append(name)
    return True


def rename_node(data: dict, kind: str, old: str, new: str) -> None:
    """Follow a renamed chat, project or personality through the links."""
    was, now = node(kind, old), node(kind, new)
    if kind == CHAT:
        data["Private"] = [new if c == old else c for c in data["Private"]]
        for table in (data["Summaries"], data["Overrides"]):
            if old in table:
                table[new] = table.pop(old)
    else:
        data[_list_key(kind)] = [new if n == old else n for n in data[_list_key(kind)]]
    for link in data["Links"]:
        link["From"] = now if link["From"] == was else link["From"]
        link["To"] = now if link["To"] == was else link["To"]
    if was in data["Layout"]:
        data["Layout"][now] = data["Layout"].pop(was)


def remove_hub(data: dict, kind: str, name: str) -> None:
    """Delete a project or personality and its links. The chats stay."""
    gone = node(kind, name)
    for link in links_of(data, gone):
        if link["Kind"] == SPEAKS:
            data["Overrides"].pop(split_node(link["From"])[1], None)
    data[_list_key(kind)] = [n for n in data[_list_key(kind)] if n != name]
    data["Links"] = [l for l in data["Links"] if gone not in (l["From"], l["To"])]
    data["Layout"].pop(gone, None)


def set_private(data: dict, chat: str, private: bool) -> None:
    data["Private"] = [c for c in data["Private"] if c != chat] + ([chat] if private else [])


# ---------------------------------------------------------------------------
# What one chat can see
# ---------------------------------------------------------------------------

def readable_chats(data: dict, chat: str) -> dict:
    """{other chat: why this chat can read it}.

    Sessions of its own projects, plus whatever its personality can read. Being
    private keeps a chat from every personality, even when it's linked, but not
    from the other sessions of its own project."""
    found = {}
    own_projects = projects_of(data, chat)
    members = {}
    for link in data["Links"]:
        if link["Kind"] == MEMBER:
            members.setdefault(split_node(link["To"])[1], []).append(split_node(link["From"])[1])
    for project in own_projects:
        for other in members.get(project, []):
            found.setdefault(other, f"same project: {project}")
    personality = speaks_as(data, chat)
    if personality:
        target = node(PERSONALITY, personality)
        private = set(data["Private"])
        for link in data["Links"]:
            if link["To"] != target or link["Kind"] != READS:
                continue
            kind, name = split_node(link["From"])
            for other in [name] if kind == CHAT else members.get(name, []):
                if other not in private:
                    why = f"{personality} can read it" if kind == CHAT else f"{personality} can read project {name}"
                    found.setdefault(other, why)
    found.pop(chat, None)
    return found


def summary_due(data: dict, chat: str, messages: int) -> bool:
    """Whether this linked chat's one-line summary needs writing or refreshing."""
    if not is_linked(data, chat):
        return False
    upto = data["Summaries"].get(chat, {}).get("Upto", 0)
    return messages - upto >= (SUMMARY_STEP if upto else FIRST_SUMMARY_AT)
