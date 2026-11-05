"""Memory: Markdown notes the model keeps for one chat, in that chat's memory folder.

Every .md in the folder is sent to the model at the start of each reply (see
llmClient.build_messages), as context and rules for this chat only. Models that
can call tools write them with these tools; you can also add or edit them by
hand (Chat Settings > Memory opens the folder).

A chat in a project or using a personality also gets their notes, after its own,
and can write to them by naming the place (ToolBox.memory_places). Notes share one
limit and the chat's own come first, so they're the last to be cut.
"""

import re
from pathlib import Path

from . import Tool, ToolError, clip, params
from .files import _read_text, _recycle

MAX_FILE_CHARS = 10000       # one note
MAX_NOTE_CHARS = 20000       # all of a chat's notes together, as sent to the model


def memory_files(folder) -> list:
    """The chat's .md files by name; none if the folder doesn't exist yet."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".md"),
                  key=lambda p: p.name.lower())


def system_note(folder, heading="Your memory for this chat: context and rules saved earlier, by you or the user. "
                                "Follow them unless the user says otherwise.", limit=MAX_NOTE_CHARS) -> str:
    """Everything in the memory folder, for the system message; "" if there's nothing."""
    parts = []
    for path in memory_files(folder):
        try:
            text = _read_text(path).strip()
        except OSError:
            continue
        if text:
            parts.append(f"## {path.name}\n{text}")
    if not parts:
        return ""
    return heading + "\n\n" + clip("\n\n".join(parts), limit)


def _folder(box, place) -> Path:
    places = box.memory_places()
    folder = places.get(str(place or "chat"))
    if folder is None:
        raise ToolError(f"there is no memory place called {place}. Places: {', '.join(places)}")
    return folder


def _path(box, name, place=None) -> Path:
    """The note called `name` in one of this chat's memory folders. The name can't lead out of it."""
    stem = str(name).replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem = re.sub(r"\.md$", "", stem, flags=re.I)
    stem = re.sub(r'[<>:"|?*\x00-\x1f]', "", stem).strip(" .")
    if not stem:
        raise ToolError("give the note a file name, such as project-rules")
    return _folder(box, place) / f"{stem}.md"


def save_memory(box, name, content, place=None):
    content = str(content).strip()
    if not content:
        raise ToolError("the note is empty; use delete_memory to remove one")
    if len(content) > MAX_FILE_CHARS:
        raise ToolError(f"the note has {len(content):,} characters; keep it under {MAX_FILE_CHARS:,}")
    path = _path(box, name, place)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(content + "\n", encoding="utf-8")
    return f"{'Updated' if existed else 'Saved'} {path.name}"


def read_memory(box, name, place=None):
    path = _path(box, name, place)
    if not path.is_file():
        found = ", ".join(p.name for p in memory_files(_folder(box, place))) or "none"
        raise ToolError(f"there is no note called {path.name}. Notes: {found}")
    return _read_text(path)


def delete_memory(box, name, place=None):
    path = _path(box, name, place)
    if not path.is_file():
        raise ToolError(f"there is no note called {path.name}")
    if _recycle(path):
        return f"Moved {path.name} to the Recycle Bin"
    path.unlink()
    return f"Deleted {path.name}"


NAME = {"type": "string", "description": "The note's file name, such as project-rules"}
PLACE = {"type": "string", "description": "Whose note: chat (the default), or a project or personality place "
                                          "named in your instructions, such as project:Avatar"}


def _where(args) -> str:
    return f" in {args['place']}" if args.get("place") else ""

TOOLS = [
    Tool("save_memory", "Save a Markdown note to this chat's memory, replacing any note of that name. Notes are "
                        "shown to you at the start of every reply in this chat. Use them for lasting context and "
                        "rules: facts, decisions and how the user wants things done.",
         params(["name", "content"], name=NAME, content={"type": "string", "description": "The note, in Markdown"},
                place=PLACE),
         save_memory, kind="write",
         summary=lambda a: f"Save memory note {a.get('name')}{_where(a)}: {str(a.get('content', '')).strip()[:80]}"),
    Tool("read_memory", "Read one note from this chat's memory in full.",
         params(["name"], name=NAME, place=PLACE), read_memory,
         summary=lambda a: f"Read memory note {a.get('name')}{_where(a)}"),
    Tool("delete_memory", "Delete a note from this chat's memory.",
         params(["name"], name=NAME, place=PLACE), delete_memory, kind="write",
         summary=lambda a: f"Delete memory note {a.get('name')}{_where(a)}"),
]
