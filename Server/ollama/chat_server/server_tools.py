"""What the tools that act on this machine share: the token, the folders they may touch, and their list.

Two groups of them run here rather than on the client: REA (rea.py) and the
Ubuntu file and run tools (ubuntu.py). Port 8100 is open to the LAN, so:

- every request needs the shared token in the X-ChatUI-Token header
  (Config.TOOL_TOKEN or the first line of Config.TOOL_TOKEN_FILE); without a
  token configured here the endpoints refuse everything;
- every path must resolve (symlinks followed) inside one of Config.TOOL_ROOTS,
  never inside Config.TOOL_DENY (keys and passwords), and is only read inside
  Config.TOOL_READ_ONLY (the game copies).

The client asks the user before every change or command; that check is the
client's, as it is for its own tools. This module only refuses what is never allowed.
"""

import asyncio
import hmac
import inspect
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import Header, HTTPException

from .config import Config

TOKEN_HEADER = "X-ChatUI-Token"
log = logging.getLogger("chat_server.tools")


class ToolError(Exception):
    """A failure the model should read (bad path, refused, ...), not a crash."""


@dataclass
class ServerTool:
    name: str
    description: str
    parameters: Dict[str, Any]       # JSON schema of the arguments
    func: Callable[..., Any]         # func(**args) -> text; may be async
    kind: str = "read"               # read | write | run, as the client's tools
    group: str = ""                  # rea | files | run

    def schema(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def params(required=(), **props) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required)}


def listing(tools: List[ServerTool]) -> Dict[str, Any]:
    """GET .../tools: OpenAI `tools`, and each one's kind and group for the client's approval box."""
    return {"tools": [t.schema() for t in tools],
            "meta": {t.name: {"kind": t.kind, "group": t.group} for t in tools}}


def clip(text: str, limit: Optional[int] = None) -> str:
    limit = limit or Config.TOOL_OUTPUT_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [cut: {len(text) - limit:,} more characters. Ask for a smaller part.]"


# ------------------------------------------------------------------ token

def configured_token() -> str:
    if Config.TOOL_TOKEN:
        return Config.TOOL_TOKEN.strip()
    try:
        lines = Path(Config.TOOL_TOKEN_FILE).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return lines[0].strip() if lines else ""


async def require_token(x_chatui_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency for every /v1/rea and /v1/ubuntu endpoint."""
    expected = configured_token()
    if not expected:
        raise HTTPException(503, "Server tools are off: set TOOL_TOKEN in Server/ollama/.env, or write a token "
                                 f"to {Config.TOOL_TOKEN_FILE}, then restart the server")
    if not x_chatui_token or not hmac.compare_digest(x_chatui_token.strip(), expected):
        raise HTTPException(401, f"Wrong or missing {TOKEN_HEADER}; copy the server's token into "
                                 "Settings > General > Ubuntu server")


# ------------------------------------------------------------------ paths

def _paths(text: str) -> List[Path]:
    return [Path(os.path.realpath(os.path.expanduser(p))) for p in text.split(":") if p.strip()]


def roots() -> List[Path]:
    return _paths(Config.TOOL_ROOTS)


def _inside(p: Path, folder: Path) -> bool:
    return p == folder or folder in p.parents


def resolve(path: Optional[str], write: bool = False) -> Path:
    """The real absolute path for `path`, or ToolError if the tools may not touch it.

    Paths are Ubuntu paths: absolute, or starting with ~. Symlinks are followed
    before checking, so a link can't lead outside the roots."""
    text = (path or "").strip().strip('"')
    if not text:
        raise ToolError("no path given")
    if "\\" in text or (len(text) > 1 and text[1] == ":"):
        raise ToolError(f"{text} looks like a Windows path; these tools work on the Ubuntu server, "
                        "where paths look like /home/... or ~/...")
    text = os.path.expanduser(text)
    if not os.path.isabs(text):
        raise ToolError(f"{text} is relative; give a full Ubuntu path (/home/... or ~/...)")
    p = Path(os.path.realpath(text))
    allowed = roots()
    if not any(_inside(p, r) for r in allowed):
        raise ToolError(f"{p} is outside the folders these tools may use: {', '.join(map(str, allowed))}")
    if any(_inside(p, d) for d in _paths(Config.TOOL_DENY)):
        raise ToolError(f"{p} holds private keys or passwords; these tools may not touch it")
    if write and any(_inside(p, d) for d in _paths(Config.TOOL_READ_ONLY)):
        raise ToolError(f"{p} is read-only for these tools (game copies). Copy what you need first")
    return p


def root_of(p: Path) -> Path:
    """The allowed root a resolved path is in (the innermost one)."""
    return max((r for r in roots() if _inside(p, r)), key=lambda r: len(r.parts))


async def call(tool: Optional[ServerTool], name: str, arguments: Dict[str, Any], tools: List[ServerTool]) -> str:
    """Run one tool (blocking ones on a thread) and turn every failure into text for the model."""
    if tool is None:
        return f"Error: there is no tool called {name}. Available: {', '.join(t.name for t in tools)}"
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    try:
        if inspect.iscoroutinefunction(tool.func):
            out = await tool.func(**arguments)
        else:
            out = await asyncio.to_thread(tool.func, **arguments)
        return clip(str(out) if out else "(done, no output)")
    except ToolError as e:
        return f"Error: {e}"
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"
    except OSError as e:
        return f"Error: {e.strerror or e} ({getattr(e, 'filename', '') or ''})".replace(" ()", "")
    except Exception as e:
        log.exception("tool %s failed", name)
        return f"Error: {type(e).__name__}: {e}"
