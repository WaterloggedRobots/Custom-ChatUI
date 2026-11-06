"""Tools the chat model can call on this PC: files and folders, git, and a terminal.

They come in three groups that each chat turns on separately (Chat Settings):
  files  browse, read and edit files and folders   (files.py)
  git    git in the user's repositories              (git.py; github_login.py signs in)
  run    run scripts, programs and shell commands    (terminal.py)
change_directory belongs to all three, since each of them starts in the working folder.
Two more groups run on the Ubuntu server, not here (remote.py lists and calls them):
  rea           reverse engineering with REA           (rea.py)
  ubuntu_files  files and folders on the server        (ubuntu.py)
  ubuntu_run    bash commands on the server            (ubuntu.py)
Memory notes (memory.py) have their own switch: their tools are on while the chat has a memory_dir.
Reading linked chats (linked.py) is on while the chat's links reach any.

They run here, in the client, because the files are on this PC and the LLM
server is another machine. The server only passes the tool list to Ollama and
streams the model's calls back (see Server/ollama/chat_server/pipeline.py);
llmClient runs them and sends the results in the next request.

Each tool has a kind:
  read   looks at something and changes nothing. Runs without asking.
  write  changes files, folders or a repository. Asks you first.
  run    runs a program or shell command. Asks you first.

ToolBox holds the state one chat needs: the folders and GitHub repositories
you linked to the chat (the model is told about them; the first folder is where
it starts), the working folder that relative paths start from, and the tools you chose to "always
allow" for this session.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

MAX_OUTPUT_CHARS = 20000      # what one tool result may add to the conversation
GROUPS = ("files", "git", "run")
SERVER_GROUPS = ("rea", "ubuntu_files", "ubuntu_run")   # their tools come from the server (remote.py)
SHARED_LISTED = 15            # folders or repositories from linked chats named in the note, of each
SHARED = {"change_directory"}  # in every group


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]       # JSON schema of the arguments
    func: Callable[..., str]         # func(ctx, **args) -> text for the model
    kind: str = "read"               # read | write | run
    summary: Optional[Callable[[Dict[str, Any]], str]] = None   # one line for the approval prompt
    group: str = ""                  # one of GROUPS; set by ToolBox

    def schema(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


class ToolError(Exception):
    """A failure the model should read (bad path, git error, ...), not a crash."""


def params(required=(), **props) -> Dict[str, Any]:
    """Shorthand for an object schema: params(["path"], path={"type": "string", ...})."""
    return {"type": "object", "properties": props, "required": list(required)}


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [cut: {len(text) - limit:,} more characters. Ask for a smaller part.]"


_REPO_PART = r"([\w.-]+)/([\w.-]+?)(?:\.git)?"
_REPO_LINK = re.compile(r"(?:(?:https?://|ssh://git@|git@)(?:www\.)?)?github\.com[/:]" + _REPO_PART + r"(?:[/?#].*)?", re.I)
_REPO_SHORT = re.compile(_REPO_PART)


def repo_url(text: str) -> Optional[str]:
    """https://github.com/owner/repo from a GitHub link or owner/repo, or None if it's neither."""
    text = str(text).strip()
    found = _REPO_LINK.fullmatch(text) or _REPO_SHORT.fullmatch(text)
    if not found or {found[1], found[2]} & {".", ".."}:
        return None
    return f"https://github.com/{found[1]}/{found[2]}"


class ToolBox:
    def __init__(self, working_dir: Optional[str] = None,
                 extract_text: Optional[Callable[[Path], str]] = None):
        self.working_dir = Path(working_dir).expanduser() if working_dir else Path.home()
        self.folders: List[Path] = []     # linked in the GUI
        self.repos: List[str] = []        # GitHub repository links, linked in the GUI
        self.memory_dir: Optional[Path] = None   # this chat's memory notes; None = memory is off
        self.link_scope = None            # what this chat's links reach (linked.LinkScope), replaced whole
        self.always_allow: set = set()
        # Turns a PDF or Word file into text (llmClient uses the server's /v1/files/extract).
        self.extract_text = extract_text
        # The chat server's address and the tool token, for the server's tool groups: () -> (url, token).
        self.server: Optional[Callable[[], tuple]] = None
        from . import files, git, linked, memory, rea, terminal, ubuntu
        self.remotes = {"rea": rea.remote(), "ubuntu": ubuntu.remote()}   # each lists one or more groups
        self.tools: Dict[str, Tool] = {}
        for group, module in zip(GROUPS, (files, git, terminal)):
            for t in module.TOOLS:
                t.group = group
                self.tools[t.name] = t
        for t in memory.TOOLS:
            t.group = "memory"
            self.tools[t.name] = t
        for t in linked.TOOLS:
            t.group = "links"
            self.tools[t.name] = t
        self.groups: set = set(GROUPS)    # the groups this chat turned on

    def set_groups(self, groups: Iterable[str]) -> None:
        self.groups = {g for g in groups if g in GROUPS or g in SERVER_GROUPS}

    def refresh_server_tools(self) -> None:
        """Fetch the tool lists of the server groups that are on (network: call it from the worker thread)."""
        for remote in self.remotes.values():
            groups = set(remote.groups.values())
            if not groups & self.groups:
                continue
            if not self.server:
                remote.error = "this chat isn't connected to a server"
                continue
            url, token = self.server()
            if not token:
                remote.tools, remote.error = [], "no server tool token is set (Settings > General > Ubuntu server)"
                remote._key = None
            elif not remote.load(url, token):
                continue
            for name in [n for n, t in self.tools.items() if t.group in groups]:
                del self.tools[name]
            for t in remote.tools:
                if t.name not in self.tools:       # a PC tool of the same name wins
                    self.tools[t.name] = t

    def enabled(self, name: str) -> bool:
        tool = self.tools.get(name)
        if tool and tool.group == "memory":
            return self.memory_dir is not None
        if tool and tool.group == "links":
            return self.has_links()
        return bool(tool) and (tool.group in self.groups or (name in SHARED and bool(self.groups & set(GROUPS))))

    def active(self) -> List[Tool]:
        return [t for t in self.tools.values() if self.enabled(t.name)]

    # ---------------------------------------------------------------- paths

    def resolve(self, path: Optional[str]) -> Path:
        """Absolute path for `path`: ~ and %VARS% expanded, relative to the working folder."""
        text = os.path.expandvars(os.path.expanduser((path or ".").strip().strip('"')))
        p = Path(text)
        if not p.is_absolute():
            p = self.working_dir / p
        return Path(os.path.normpath(p))

    def set_folders(self, folders) -> None:
        """Link these folders; the working folder moves to the first one."""
        self.folders = []
        for f in folders:
            p = Path(os.path.normpath(Path(f).expanduser()))
            if p.is_dir() and p not in self.folders:
                self.folders.append(p)
        self.working_dir = self.folders[0] if self.folders else Path.home()

    def add_folder(self, folder) -> None:
        self._relink([*self.folders, folder])

    def remove_folder(self, folder) -> None:
        self._relink([f for f in self.folders if f != Path(folder)])

    def _relink(self, folders) -> None:
        current = self.working_dir
        self.set_folders(folders)
        if any(current == f or f in current.parents for f in self.folders):
            self.working_dir = current       # still inside a linked folder: stay there

    def set_repos(self, repos) -> None:
        self.repos = []
        for text in repos:
            self.add_repo(text)

    def add_repo(self, text) -> Optional[str]:
        """Link a repository; returns its link, or None if `text` isn't a GitHub repository."""
        url = repo_url(text)
        if url and url not in self.repos:
            self.repos.append(url)
        return url

    def remove_repo(self, url) -> None:
        self.repos = [r for r in self.repos if r != url]

    def set_working_dir(self, path: str) -> Path:
        p = self.resolve(path)
        if not p.is_dir():
            raise ToolError(f"{p} is not a folder")
        self.working_dir = p
        return p

    # ---------------------------------------------------------------- calls

    def schemas(self) -> List[Dict[str, Any]]:
        self.refresh_server_tools()
        return [t.schema() for t in self.active()]

    def kind(self, name: str) -> str:
        tool = self.tools.get(name)
        return tool.kind if tool else "read"

    def needs_approval(self, name: str) -> bool:
        return self.kind(name) != "read" and name not in self.always_allow

    def describe(self, name: str, args: Dict[str, Any]) -> str:
        """One readable line for the approval prompt and the chat log."""
        tool = self.tools.get(name)
        if tool and tool.summary:
            try:
                return tool.summary(args)
            except Exception:
                pass
        shown = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:120]}" for k, v in args.items())
        return f"{name}({shown})"

    def run(self, name: str, args: Dict[str, Any]) -> str:
        tool = self.tools.get(name)
        if tool is None or not self.enabled(name):
            available = ", ".join(t.name for t in self.active()) or "none"
            if tool is not None:
                return (f"Error: {name} is turned off for this chat (the user's {tool.group} tools are off). "
                        f"Available: {available}")
            return f"Error: there is no tool called {name}. Available: {available}"
        try:
            return clip(tool.func(self, **args) or "(done, no output)")
        except ToolError as e:
            return f"Error: {e}"
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except OSError as e:
            return f"Error: {e.strerror or e} ({getattr(e, 'filename', '') or ''})".replace(" ()", "")
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    def has_links(self) -> bool:
        return bool(self.link_scope and self.link_scope.chats)

    def shared_folders(self) -> Dict[str, str]:
        """Folders linked chats share with this one, and the chat each comes from."""
        linked = {str(f) for f in self.folders}
        return {f: c for f, c in (self.link_scope.folders if self.link_scope else {}).items() if f not in linked}

    def shared_repos(self) -> Dict[str, str]:
        """GitHub repositories linked chats share with this one, and the chat each comes from."""
        return {r: c for r, c in (self.link_scope.repos if self.link_scope else {}).items() if r not in self.repos}

    def memory_places(self) -> Dict[str, Path]:
        """The memory folders this chat uses, by the name the tools know them by; {} if memory is off."""
        if not self.memory_dir:
            return {}
        return {"chat": self.memory_dir, **(self.link_scope.places if self.link_scope else {})}

    def memory_note(self) -> str:
        """The notes of this chat, then its projects, then its personality, for every model.

        They share one limit, so the chat's own are the last to be cut. The chat's memory switch only
        covers its own notes: what comes with its project or personality always comes. "" if there are none."""
        from . import memory
        parts, room = [], memory.MAX_NOTE_CHARS
        places = {"chat": self.memory_dir} if self.memory_dir else {}
        places.update(self.link_scope.places if self.link_scope else {})
        for place, folder in places.items():
            if room <= 0:
                break
            heading = {} if place == "chat" else {
                "heading": f"Shared memory from {place}: context and rules saved earlier, for every chat linked to it."}
            note = memory.system_note(folder, limit=room, **heading)
            if note:
                parts.append(note)
                room -= len(note)
        return "\n\n".join(parts)

    def links_note(self, room: int, can_call: bool) -> str:
        """The list of chats this one links to; it gets what's left after the notes, so it shrinks first."""
        from . import linked
        return linked.list_note(self.link_scope, room, can_call)

    def system_note(self) -> str:
        """Tells the model what it can do and where it is."""
        note = self._pc_note() if self.groups & set(GROUPS) else ""
        server = self._server_note()
        if server:
            note += (" " if note else "") + server
        if self.memory_dir:
            note += (" " if note else "") + (
                "You can keep memory notes for this chat with save_memory: short Markdown files, one per topic, "
                "for lasting context and rules such as facts about the user, decisions and how they want things "
                "done. You see them at the start of every reply in this chat; other chats don't. Save what will "
                "still matter later, and update a note rather than starting another. The user approves each save.")
            others = [place for place in self.memory_places() if place != "chat"]
            if others:
                note += (" Notes for more than this chat go in a shared place, named in save_memory's place argument: "
                         f"{', '.join(others)}. Every chat linked to that place will read them.")
        return note

    def _server_note(self) -> str:
        """What the server's tools are for, or why they're missing. "" if none is on."""
        from . import rea, ubuntu
        on = [g for g in SERVER_GROUPS if g in self.groups]
        if not on:
            return ""
        self.refresh_server_tools()
        parts = ["The user's Ubuntu server is another machine than this Windows PC; some of your tools run there."]
        for remote in self.remotes.values():
            mine = [g for g in on if g in remote.groups.values()]
            if not mine:
                continue
            if not remote.tools:
                parts.append(f"The {remote.label} are unavailable right now ({remote.error}); tell the user "
                             "if they ask for them.")
            elif remote.prefix == "rea":
                parts.append(rea.NOTE)
            else:
                parts.append(ubuntu.note(remote.info.get("roots") or [],
                                         files="ubuntu_files" in mine, run="ubuntu_run" in mine))
        if not self.groups & set(GROUPS):
            parts.append("The user approves every change on the server, and may decline; if so, don't retry the "
                         "same thing. After using tools, tell the user briefly what you did and what you found.")
        return " ".join(parts)

    def _shared_note(self) -> str:
        """Paths only, as the model opens them when it needs them; a very long list stops at SHARED_LISTED."""
        def listed(items):
            names = sorted(items, key=str.lower)
            more = len(names) - SHARED_LISTED
            return "; ".join(names[:SHARED_LISTED]) + (f"; and {more} more (list_linked_chats has them all)"
                                                       if more > 0 else "")
        note = ""
        if self.shared_folders():
            note += f"Chats linked to this one also work in these folders, which you can use too: " \
                    f"{listed(self.shared_folders())}. "
        if self.shared_repos():
            note += f"They also use these GitHub repositories: {listed(self.shared_repos())}. "
        return note

    def _pc_note(self) -> str:
        can = [text for group, text in (
            ("files", "browse, read and edit files and folders"),
            ("git", "use git"),
            ("run", "run scripts, programs and terminal commands (PowerShell by default)"),
        ) if group in self.groups]
        off = [g for g in ("files", "git", "run") if g not in self.groups]
        names = {"files": "file browsing", "git": "git", "run": "running programs"}
        return (
            f"You can use tools on the user's own PC (Windows): {'; '.join(can)}. "
            + (f"The user turned off {' and '.join(names[g] for g in off)} for this chat; don't offer those. "
               if off and can else "")
            + (f"The user linked these folders for you to work in: {'; '.join(str(f) for f in self.folders)}. "
               if self.folders else "")
            + (f"The user linked these GitHub repositories to this chat: {'; '.join(self.repos)}. "
               + ("Clone one with git_clone before working on it, unless it's already in a linked folder. "
                  if "git" in self.groups else "You can't clone them while git is off. ")
               if self.repos else "")
            + self._shared_note() +
            f"The working folder is {self.working_dir}; relative paths start there and "
            "change_directory moves it. Look before you change anything: list or read first"
            + (", and use edit_file for small changes instead of rewriting whole files" if "files" in self.groups else "")
            + ". The user is asked to approve every change and command, and may decline; if so, don't retry "
            "the same thing. After using tools, tell the user briefly what you did and what you found.")
