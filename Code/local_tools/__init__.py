"""Tools the chat model can call on this PC: files and folders, git, and a terminal.

They run here, in the client, because the files are on this PC and the LLM
server is another machine. The server only passes the tool list to Ollama and
streams the model's calls back (see Server/ollama/chat_server/pipeline.py);
llmClient runs them and sends the results in the next request.

Each tool has a kind:
  read   looks at something and changes nothing. Runs without asking.
  write  changes files, folders or a repository. Asks you first.
  run    runs a program or shell command. Asks you first.

ToolBox holds the state one chat needs: the working folder that relative
paths start from, and the tools you chose to "always allow" for this session.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

MAX_OUTPUT_CHARS = 20000      # what one tool result may add to the conversation


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]       # JSON schema of the arguments
    func: Callable[..., str]         # func(ctx, **args) -> text for the model
    kind: str = "read"               # read | write | run
    summary: Optional[Callable[[Dict[str, Any]], str]] = None   # one line for the approval prompt

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
        self.always_allow: set = set()
        # Turns a PDF or Word file into text (llmClient uses the server's /v1/files/extract).
        self.extract_text = extract_text
        from . import files, git, terminal
        self.tools: Dict[str, Tool] = {t.name: t for t in (*files.TOOLS, *git.TOOLS, *terminal.TOOLS)}

    # ---------------------------------------------------------------- paths

    def resolve(self, path: Optional[str]) -> Path:
        """Absolute path for `path`: ~ and %VARS% expanded, relative to the working folder."""
        text = os.path.expandvars(os.path.expanduser((path or ".").strip().strip('"')))
        p = Path(text)
        if not p.is_absolute():
            p = self.working_dir / p
        return Path(os.path.normpath(p))

    def set_working_dir(self, path: str) -> Path:
        p = self.resolve(path)
        if not p.is_dir():
            raise ToolError(f"{p} is not a folder")
        self.working_dir = p
        return p

    # ---------------------------------------------------------------- calls

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

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
        if tool is None:
            return f"Error: there is no tool called {name}. Available: {', '.join(self.tools)}"
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
        return (
            "You can use tools on the user's own PC (Windows): browse and read folders and files, "
            "edit files, use git, and run terminal commands (PowerShell by default). "
            f"The working folder is {self.working_dir}; relative paths start there and "
            "change_directory moves it. Look before you change anything: list or read first, and use "
            "edit_file for small changes instead of rewriting whole files. The user is asked to approve "
            "every change and command, and may decline; if so, don't retry the same thing. After using "
            "tools, tell the user briefly what you did and what you found.")
