"""Files and folders on this Ubuntu machine, for the chat model on a Windows client.

The same tools the client has for its own PC (Code/local_tools/files.py), run
here. Paths are Ubuntu paths, absolute or starting with ~, and must be inside
Config.TOOL_ROOTS (see server_tools.resolve). There is no working folder: each
call names its paths. Deleting moves to a .chatui-trash folder at the top of
the root the path is in (same disk, so it's quick and can be undone); nothing
is deleted for good.

Endpoints (token required):
  GET  /v1/ubuntu/tools   OpenAI `tools`, plus each one's kind and group
  POST /v1/ubuntu/call    {name, arguments} -> {"name", "output"}
"""

import fnmatch
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from . import server_tools
from .server_tools import ServerTool, ToolError, params, resolve

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".cache", ".chatui-trash"}
MAX_READ_BYTES = 2 * 1024 * 1024
TRASH = ".chatui-trash"


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _walk(root: Path, max_depth: int):
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            continue
        for p in entries:
            yield p, depth
            if p.is_dir() and not p.is_symlink() and p.name not in SKIP_DIRS and depth + 1 < max_depth:
                stack.append((p, depth + 1))


def _allowed(p: Path) -> bool:
    try:
        resolve(str(p))
        return True
    except ToolError:
        return False


# ---------------------------------------------------------------- read tools

def list_directory(path="~", pattern="*", recursive=False, max_entries=200):
    root = resolve(path)
    if not root.exists():
        raise ToolError(f"{root} does not exist")
    if not root.is_dir():
        raise ToolError(f"{root} is a file, not a folder; use ubuntu_read_file")
    lines, total = [], 0
    for p, _ in _walk(root, 6 if recursive else 1):
        if not fnmatch.fnmatch(p.name.lower(), pattern.lower()) and not (recursive and p.is_dir()):
            continue
        total += 1
        if len(lines) >= max_entries:
            continue
        rel = p.relative_to(root)
        try:
            st = p.stat()
        except OSError:
            lines.append(f"{rel}  (unreadable)")
            continue
        when = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{rel}/  {when}" if p.is_dir() else f"{rel}  {_size(st.st_size)}  {when}")
    head = f"{root}  ({total} entr{'y' if total == 1 else 'ies'}"
    head += f", showing {max_entries})" if total > max_entries else ")"
    return "\n".join([head, *lines]) if lines else f"{root} is empty" + (f" (no match for {pattern})" if pattern != "*" else "")


def read_file(path, start_line=1, max_lines=400):
    p = resolve(path)
    if not p.is_file():
        raise ToolError(f"{p} is not a file" if p.exists() else f"{p} does not exist")
    if _is_binary(p):
        raise ToolError(f"{p.name} is a binary file ({_size(p.stat().st_size)}); it can't be read as text")
    if p.stat().st_size > MAX_READ_BYTES and max_lines > 400:
        max_lines = 400
    lines = _read_text(p).splitlines()
    start = max(1, int(start_line))
    end = min(len(lines), start - 1 + max(1, int(max_lines)))
    body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n… lines {end + 1}-{len(lines)} not shown (use start_line={end + 1})" if end < len(lines) else ""
    return f"{p}  (lines {start}-{end} of {len(lines)})\n{body}{more}"


def find_files(pattern, path="~", max_results=200):
    root = resolve(path)
    if not root.is_dir():
        raise ToolError(f"{root} is not a folder")
    found = []
    for p, _ in _walk(root, 12):
        if fnmatch.fnmatch(p.name.lower(), pattern.lower()):
            found.append(str(p) + ("/" if p.is_dir() else ""))
            if len(found) > max_results:
                break
    if not found:
        return f"No matches for {pattern} under {root}"
    extra = "\n… more matches not shown" if len(found) > max_results else ""
    return "\n".join(found[:max_results]) + extra


def search_in_files(query, path="~", file_pattern="*", regex=False, ignore_case=True, max_results=100):
    root = resolve(path)
    if not root.exists():
        raise ToolError(f"{root} does not exist")
    try:
        rx = re.compile(query if regex else re.escape(query), re.I if ignore_case else 0)
    except re.error as e:
        raise ToolError(f"bad regular expression: {e}")
    files = [root] if root.is_file() else (p for p, _ in _walk(root, 12) if p.is_file())
    hits = []
    for p in files:
        if not fnmatch.fnmatch(p.name.lower(), file_pattern.lower()) or not _allowed(p):
            continue
        try:
            if p.stat().st_size > MAX_READ_BYTES or _is_binary(p):
                continue
            text = _read_text(p)
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p}:{n}: {line.strip()[:200]}")
                if len(hits) > max_results:
                    break
        if len(hits) > max_results:
            break
    if not hits:
        return f"No matches for {query!r} in {root}"
    extra = "\n… more matches not shown" if len(hits) > max_results else ""
    return "\n".join(hits[:max_results]) + extra


# --------------------------------------------------------------- write tools

def write_file(path, content, overwrite=False):
    p = resolve(path, write=True)
    if p.is_dir():
        raise ToolError(f"{p} is a folder")
    if p.exists() and not overwrite:
        raise ToolError(f"{p} already exists; pass overwrite=true to replace it, or use ubuntu_edit_file")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return f"Wrote {p} ({_size(p.stat().st_size)})"


def edit_file(path, old_text, new_text, replace_all=False):
    p = resolve(path, write=True)
    if not p.is_file():
        raise ToolError(f"{p} is not a file")
    text = _read_text(p)
    count = text.count(old_text) if old_text else 0
    if count == 0:
        raise ToolError(f"old_text was not found in {p.name}; read the file and copy the text exactly")
    if count > 1 and not replace_all:
        raise ToolError(f"old_text appears {count} times in {p.name}; add more surrounding lines or pass replace_all=true")
    text = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return f"Edited {p} ({count if replace_all else 1} replacement{'s' if replace_all and count > 1 else ''})"


def create_folder(path):
    p = resolve(path, write=True)
    p.mkdir(parents=True, exist_ok=True)
    return f"Folder {p} exists"


def _destination(src: Path, destination: str, overwrite: bool) -> Path:
    dst = resolve(destination, write=True)
    if dst.is_dir():
        dst = resolve(str(dst / src.name), write=True)
    if dst == src or src in dst.parents:
        raise ToolError(f"can't put {src} inside itself")
    if dst.exists() and not overwrite:
        raise ToolError(f"{dst} already exists; pass overwrite=true to replace it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst


def move_path(source, destination, overwrite=False):
    src = resolve(source, write=True)
    if not src.exists():
        raise ToolError(f"{src} does not exist")
    if src in server_tools.roots():
        raise ToolError(f"refusing to move {src}, one of the tool folders")
    dst = _destination(src, destination, overwrite)
    shutil.move(str(src), str(dst))
    return f"Moved {src} to {dst}"


def copy_path(source, destination, overwrite=False):
    src = resolve(source)
    if not src.exists():
        raise ToolError(f"{src} does not exist")
    dst = _destination(src, destination, overwrite)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    else:
        shutil.copy2(src, dst)
    return f"Copied {src} to {dst}"


def delete_path(path, recursive=False):
    p = resolve(path, write=True)
    if not p.exists():
        raise ToolError(f"{p} does not exist")
    root = server_tools.root_of(p)
    trash = root / TRASH
    if p == root or p == Path.home() or p == trash or trash in p.parents:
        raise ToolError(f"refusing to delete {p}")
    if p.is_dir() and any(p.iterdir()) and not recursive:
        raise ToolError(f"{p} is a folder with files in it; pass recursive=true to delete it all")
    where = trash / datetime.now().strftime("%Y%m%d-%H%M%S") / p.relative_to(root)
    n = 1
    while where.exists():
        where = where.with_name(f"{p.name}.{n}")
        n += 1
    where.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(where))
    return f"Moved {p} to the trash: {where}"


# --------------------------------------------------------------- the list

PATH = {"type": "string", "description": "Ubuntu path on the server: /home/... or ~/... (not a Windows path)"}

TOOLS = [
    ServerTool("ubuntu_list_directory", "List the files and folders in a folder on the Ubuntu server, with sizes "
                                        "and dates.",
               params([], path=PATH, pattern={"type": "string", "description": "Only names matching this wildcard"},
                      recursive={"type": "boolean", "description": "Include subfolders (up to 6 levels)"},
                      max_entries={"type": "integer"}),
               list_directory, group="files"),
    ServerTool("ubuntu_read_file", "Read a text file on the Ubuntu server with line numbers. Long files: read in "
                                   "parts with start_line.",
               params(["path"], path=PATH, start_line={"type": "integer"}, max_lines={"type": "integer"}),
               read_file, group="files"),
    ServerTool("ubuntu_find_files", "Find files or folders by name (wildcards) under a folder on the Ubuntu server.",
               params(["pattern"], pattern={"type": "string", "description": "e.g. *.dll or *Yotogi*"}, path=PATH,
                      max_results={"type": "integer"}),
               find_files, group="files"),
    ServerTool("ubuntu_search_in_files", "Search inside text files on the Ubuntu server for a word or regular "
                                         "expression (like grep). Returns path:line: text.",
               params(["query"], query={"type": "string"}, path=PATH,
                      file_pattern={"type": "string", "description": "Only files matching this wildcard, e.g. *.cs"},
                      regex={"type": "boolean"}, ignore_case={"type": "boolean"}, max_results={"type": "integer"}),
               search_in_files, group="files"),
    ServerTool("ubuntu_write_file", "Create a text file on the Ubuntu server, or replace one with overwrite=true.",
               params(["path", "content"], path=PATH, content={"type": "string"}, overwrite={"type": "boolean"}),
               write_file, kind="write", group="files"),
    ServerTool("ubuntu_edit_file", "Change part of a text file on the Ubuntu server: replaces old_text (copied "
                                   "exactly, unique unless replace_all) with new_text.",
               params(["path", "old_text", "new_text"], path=PATH, old_text={"type": "string"},
                      new_text={"type": "string"}, replace_all={"type": "boolean"}),
               edit_file, kind="write", group="files"),
    ServerTool("ubuntu_create_folder", "Create a folder (and missing parents) on the Ubuntu server.",
               params(["path"], path=PATH), create_folder, kind="write", group="files"),
    ServerTool("ubuntu_move_path", "Move or rename a file or folder on the Ubuntu server.",
               params(["source", "destination"], source=PATH, destination=PATH, overwrite={"type": "boolean"}),
               move_path, kind="write", group="files"),
    ServerTool("ubuntu_copy_path", "Copy a file or folder on the Ubuntu server.",
               params(["source", "destination"], source=PATH, destination=PATH, overwrite={"type": "boolean"}),
               copy_path, kind="write", group="files"),
    ServerTool("ubuntu_delete_path", "Delete a file or folder on the Ubuntu server (moved to a .chatui-trash "
                                     "folder, so it can be restored). Folders with files need recursive=true.",
               params(["path"], path=PATH, recursive={"type": "boolean"}),
               delete_path, kind="write", group="files"),
]


class CallRequest(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


router = APIRouter(prefix="/v1/ubuntu", dependencies=[Depends(server_tools.require_token)])


@router.get("/tools")
async def tools():
    return {**server_tools.listing(TOOLS), "roots": [str(r) for r in server_tools.roots()]}


@router.post("/call")
async def call(body: CallRequest):
    tool = next((t for t in TOOLS if t.name == body.name), None)
    return {"name": body.name, "output": await server_tools.call(tool, body.name, body.arguments, TOOLS)}
