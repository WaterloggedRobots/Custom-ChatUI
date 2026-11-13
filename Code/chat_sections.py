"""Sections in the chat list: named groups you sort chats into by hand.

They're only labels. A section's chats stay ordinary chats and share nothing.
They live in .temp.json under "Sections", as a list of
{"Name": ..., "Chats": [chat names], "Projects": [project names], "Open": shown expanded or not}.
A chat in no section is listed on its own, below the sections.

A project (chat_links.py) is a group of its own in the list, holding its chats, and
can sit in a section like a chat. Which chats a project holds comes from the links,
not from here, so they can't be dragged out of it.

No Qt here, so it can be tested on its own (tests/test_chat_sections.py).
"""


def normalize(data, chats, projects=None) -> list:
    """Valid sections from whatever `data` holds: chats and projects that no longer exist are
    dropped, and one listed twice stays in its first section. `projects` None keeps every name."""
    sections, names, seen, placed = [], set(), set(), set()
    known = set(chats)
    for s in data if isinstance(data, list) else []:
        name = s.get("Name") if isinstance(s, dict) else None
        if not isinstance(name, str) or not name.strip() or name in names:
            continue
        members = s.get("Chats") if isinstance(s.get("Chats"), list) else []
        kept = []
        for chat in members:
            if isinstance(chat, str) and chat in known and chat not in seen:
                seen.add(chat)
                kept.append(chat)
        groups = []
        for project in s.get("Projects") if isinstance(s.get("Projects"), list) else []:
            if (isinstance(project, str) and project and project not in placed
                    and (projects is None or project in projects)):
                placed.add(project)
                groups.append(project)
        names.add(name)
        sections.append({"Name": name, "Chats": kept, "Projects": groups, "Open": s.get("Open") is not False})
    return sections


def find(sections, name):
    return next((s for s in sections if s["Name"] == name), None)


def section_of(sections, chat):
    """The name of the chat's section, or None."""
    return next((s["Name"] for s in sections if chat in s["Chats"]), None)


def section_of_project(sections, project):
    return next((s["Name"] for s in sections if project in s.get("Projects", [])), None)


def add(sections, name) -> bool:
    """A new, empty section at the end; False if the name is empty or taken."""
    name = name.strip()
    if not name or find(sections, name):
        return False
    sections.append({"Name": name, "Chats": [], "Projects": [], "Open": True})
    return True


def rename(sections, old, new) -> bool:
    new = new.strip()
    section = find(sections, old)
    if not section or not new or (new != old and find(sections, new)):
        return False
    section["Name"] = new
    return True


def remove(sections, name) -> None:
    """Delete a section. Its chats stay, outside any section."""
    sections[:] = [s for s in sections if s["Name"] != name]


def move_chat(sections, chat, to=None) -> None:
    """Put a chat in section `to`, or in no section when `to` is None."""
    for s in sections:
        if chat in s["Chats"]:
            s["Chats"].remove(chat)
    target = find(sections, to) if to else None
    if target:
        target["Chats"].append(chat)


def move_project(sections, project, to=None) -> None:
    """Put a project in section `to`, or in no section when `to` is None."""
    for s in sections:
        if project in s.get("Projects", []):
            s["Projects"].remove(project)
    target = find(sections, to) if to else None
    if target:
        target.setdefault("Projects", []).append(project)


def move_section(sections, name, before=None) -> None:
    """Move a section in front of section `before`, or to the end when `before` is None."""
    section = find(sections, name)
    if not section or name == before:
        return
    sections.remove(section)
    target = find(sections, before) if before else None
    sections.insert(sections.index(target) if target else len(sections), section)


def rename_chat(sections, old, new) -> None:
    for s in sections:
        s["Chats"] = [new if c == old else c for c in s["Chats"]]


def rename_project(sections, old, new) -> None:
    for s in sections:
        s["Projects"] = [new if p == old else p for p in s.get("Projects", [])]
