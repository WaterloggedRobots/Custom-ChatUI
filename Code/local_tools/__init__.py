"""Tools the chat model can call on this PC: files and folders, git, and a terminal.

They come in three groups that each chat turns on separately (Chat Settings):
  files  browse, read and edit files and folders   (files.py)
  git    git in the user's repositories              (git.py; github_login.py signs in)
  run    run scripts, programs and shell commands    (terminal.py)
change_directory belongs to all three, since each of them starts in the working folder.

They run here, in the client, because the files are on this PC and the LLM
server is another machine. The server only passes the tool list to Ollama and
streams the model's calls back (see Server/ollama/chat_server/pipeline.py);
llmClient runs them and sends the results in the next request.

Each tool has a kind:
  read   looks at something and changes nothing. Runs without asking.
  write  changes files, folders or a repository. Asks you first.
  run    runs a program or shell command. Asks you first.

ToolBox holds the state one chat needs: the folders you linked to the chat
(the model is told about them; the first is where it starts), the working
folder that relative paths start from, and the tools you chose to "always
allow" for this session.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

MAX_OUTPUT_CHARS = 20000      # what one tool result may add to the conversation
GROUPS = ("files", "git", "run")
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


class ToolBox:
    def __init__(self, working_dir: Optional[str] = None,
                 extract_text: Optional[Callable[[Path], str]] = None):
        self.working_dir = Path(working_dir).expanduser() if working_dir else Path.home()
        self.folders: List[Path] = []     # linked in the GUI
        self.always_allow: set = set()
        # Turns a PDF or Word file into text (llmClient uses the server's /v1/files/extract).
        self.extract_text = extract_text
        from . import files, git, terminal
        self.tools: Dict[str, Tool] = {}
        for group, module in zip(GROUPS, (files, git, terminal)):
            for t in module.TOOLS:
                t.group = group
                self.tools[t.name] = t
        self.groups: set = set(GROUPS)    # the groups this chat turned on

    def set_groups(self, groups: Iterable[str]) -> None:
        self.groups = {g for g in groups if g in GROUPS}

    def enabled(self, name: str) -> bool:
        tool = self.tools.get(name)
        return bool(tool) and (tool.group in self.groups or (name in SHARED and bool(self.groups)))

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

    def set_working_dir(self, path: str) -> Path:
        p = self.resolve(path)
        if not p.is_dir():
            raise ToolError(f"{p} is not a folder")
        self.working_dir = p
        return p

    # ---------------------------------------------------------------- calls

    def schemas(self) -> List[Dict[str, Any]]:
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

    def system_note(self) -> str:
        """Tells the model what it can do and where it is."""
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
               if self.folders else "") +
            f"The working folder is {self.working_dir}; relative paths start there and "
            "change_directory moves it. Look before you change anything: list or read first"
            + (", and use edit_file for small changes instead of rewriting whole files" if "files" in self.groups else "")
            + ". The user is asked to approve every change and command, and may decline; if so, don't retry "
            "the same thing. After using tools, tell the user briefly what you did and what you found.")
