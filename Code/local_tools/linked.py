"""Other chats this chat may look through, because of its links (chat_links.py).

The folders and GitHub repositories those chats have linked come with them. They
cost little: the model is only told their paths, and opens what it needs.

What a chat can reach is worked out in the GUI and handed over as a LinkScope,
so the model can never name its way into a chat it isn't linked to. These tools
only read: they run without asking, and each call shows in the chat like any
other tool.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

from . import Tool, ToolError, clip, params

SUMMARY_CHARS = 160         # one chat's line in the list sent with every reply
SNIPPET_CHARS = 200
MAX_HITS = 20
READ_COUNT = 20             # messages read_linked_chat shows at a time


@dataclass
class LinkedChat:
    path: Path
    why: str
    summary: str = ""       # one line written by the model; else a preview from the chat's file


@dataclass
class LinkScope:
    chats: Dict[str, LinkedChat] = field(default_factory=dict)
    # Memory folders besides the chat's own, nearest first: {"project:Avatar": folder, "personality:Mio": folder}
    places: Dict[str, Path] = field(default_factory=dict)
    # What the linked chats have linked, and the chat each comes from.
    folders: Dict[str, str] = field(default_factory=dict)
    repos: Dict[str, str] = field(default_factory=dict)


def _messages(path: Path) -> list:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ToolError(f"can't read that chat: {e}") from e
    chat = data.get("Chat") if isinstance(data, dict) else None
    return [m for m in chat or [] if isinstance(m, dict) and m.get("role") in ("user", "assistant")]


_chat_info = {}             # chat file -> (its modified time, {"preview", "folders", "repos"})
_NO_INFO = {"preview": "", "folders": (), "repos": ()}


def _info(path: Path) -> dict:
    """What other chats need from a chat's file, read again only when the file changes."""
    try:
        stamp = path.stat().st_mtime
        cached = _chat_info.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        data = json.loads(path.read_text(encoding="utf-8"))
        rolling = (data.get("Summary") or {}).get("text") or ""
        first = next((m.get("content") for m in data.get("Chat") or [] if m.get("role") == "user"), "")
        info = {"preview": " ".join(str(rolling or first).split()),
                "folders": tuple(f for f in data.get("Tools Folders") or () if isinstance(f, str)),
                "repos": tuple(r for r in data.get("Tools Repos") or () if isinstance(r, str))}
    except (OSError, ValueError, AttributeError):
        return _NO_INFO
    _chat_info[path] = (stamp, info)
    return info


def shared_by(path: Path) -> tuple:
    """(the folders, the GitHub repositories) this chat has linked."""
    info = _info(path)
    return info["folders"], info["repos"]


def list_note(scope: LinkScope, room: int, can_call: bool) -> str:
    """The linked chats in a line each, for the system message, in at most about `room` characters."""
    if not scope or not scope.chats:
        return ""
    lines, left = [], max(room, 0)
    names = sorted(scope.chats, key=str.lower)
    for name in names:
        chat = scope.chats[name]
        about = (chat.summary or _info(chat.path)["preview"])[:SUMMARY_CHARS]
        line = f"- {name} ({chat.why})" + (f": {about}" if about else "")
        if len(line) + 1 > left:
            break
        lines.append(line)
        left -= len(line) + 1
    hidden = len(names) - len(lines)
    head = ("Other chats linked to this one, which are not part of this conversation. Bring them up only when "
            "they matter to what the user is asking.")
    if can_call:
        head += " Look through them with search_linked_chats and read_linked_chat."
    tail = f"\n… and {hidden} more." if hidden else ""
    return head + "\n" + "\n".join(lines) + tail


def _chat(box, name) -> LinkedChat:
    scope = box.link_scope
    found = scope.chats.get(str(name)) if scope else None
    if found is None:
        raise ToolError(f"this chat isn't linked to one called {name}. Use list_linked_chats to see which are")
    return found


def list_linked_chats(box):
    text = list_note(box.link_scope, 100000, True)
    if not text:
        return "No chats are linked to this one."
    for title, items in (("Folders", box.shared_folders()), ("GitHub repositories", box.shared_repos())):
        if items:
            text += f"\n\n{title} from these chats:\n" + "\n".join(f"- {i} ({c})" for i, c in sorted(items.items()))
    return text


def search_linked_chats(box, query):
    words = str(query).lower().split()
    if not words:
        raise ToolError("give some words to search for")
    hits = []
    for name in sorted(box.link_scope.chats, key=str.lower):
        try:
            messages = _messages(box.link_scope.chats[name].path)
        except ToolError:
            continue
        for i, m in enumerate(messages):
            text = " ".join(str(m.get("content", "")).split())
            lower = text.lower()
            if all(w in lower for w in words):
                at = max(lower.find(words[0]) - SNIPPET_CHARS // 2, 0)
                hits.append(f"{name} #{i} ({m['role']}): {text[at:at + SNIPPET_CHARS]}")
                if len(hits) >= MAX_HITS:
                    return "\n".join(hits) + f"\n… stopped at {MAX_HITS} matches. Search for something narrower."
    return "\n".join(hits) or "Nothing found."


def read_linked_chat(box, chat, start=None, count=READ_COUNT):
    messages = _messages(_chat(box, chat).path)
    count = max(1, min(int(count), 100))
    first = max(len(messages) - count, 0) if start is None else max(int(start), 0)
    shown = messages[first:first + count]
    if not shown:
        return f"{chat} has {len(messages)} messages; there's nothing from #{first}."
    body = "\n\n".join(f"#{first + i} {m['role']}: {m.get('content', '')}" for i, m in enumerate(shown))
    return clip(f"{chat}: messages {first} to {first + len(shown) - 1} of {len(messages)}\n\n{body}")


CHAT_NAME = {"type": "string", "description": "The linked chat's name, as list_linked_chats shows it"}

TOOLS = [
    Tool("list_linked_chats", "List the other chats this chat may read, why, what each is about, and the folders "
                              "and GitHub repositories they share.",
         params(), list_linked_chats, summary=lambda a: "List linked chats"),
    Tool("search_linked_chats", "Search the linked chats for messages holding all of these words.",
         params(["query"], query={"type": "string", "description": "Words to look for"}),
         search_linked_chats, summary=lambda a: f"Search linked chats: {a.get('query')}"),
    Tool("read_linked_chat", "Read messages from a linked chat: its newest by default, or from message number `start`.",
         params(["chat"], chat=CHAT_NAME,
                start={"type": "integer", "description": "The first message number; leave out for the newest"},
                count={"type": "integer", "description": f"How many messages, {READ_COUNT} by default, 100 at most"}),
         read_linked_chat, summary=lambda a: f"Read linked chat {a.get('chat')}"),
]
