"""Folder and file tools: browse, read, search, write, edit, move, delete."""

import fnmatch
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import Tool, ToolError, params

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vs", "build", "dist"}
MAX_READ_BYTES = 2 * 1024 * 1024      # text files larger than this are read in parts only
EXTRACT_EXT = {".pdf", ".docx"}       # turned into text by the server


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _is_binary(path: Path) -> bool:
    with open(path, "rb") as f:
        return b"\0" in f.read(8192)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("cp1252" if sys.platform == "win32" else "latin-1", errors="replace")


def _walk(root: Path, max_depth: int):
    """Yield (path, depth) below root, skipping build and VCS folders."""
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


# ---------------------------------------------------------------- read tools

def list_directory(box, path=".", pattern="*", recursive=False, max_entries=200):
    root = box.resolve(path)
    if not root.exists():
        raise ToolError(f"{root} does not exist")
    if not root.is_dir():
        raise ToolError(f"{root} is a file, not a folder; use read_file")
    lines, total = [], 0
    for p, depth in _walk(root, 6 if recursive else 1):
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
        if p.is_dir():
            lines.append(f"{rel}{os.sep}  {when}")
        else:
            lines.append(f"{rel}  {_size(st.st_size)}  {when}")
    head = f"{root}  ({total} entr{'y' if total == 1 else 'ies'}"
    head += f", showing {max_entries})" if total > max_entries else ")"
    return "\n".join([head, *lines]) if lines else f"{root} is empty" + (f" (no match for {pattern})" if pattern != "*" else "")


def read_file(box, path, start_line=1, max_lines=400):
    p = box.resolve(path)
    if not p.is_file():
        raise ToolError(f"{p} is not a file" if p.exists() else f"{p} does not exist")
    if p.suffix.lower() in EXTRACT_EXT:
        if not box.extract_text:
            raise ToolError(f"{p.name} is a {p.suffix} document and no text extractor is available")
        text = box.extract_text(p)
    else:
        if _is_binary(p):
            raise ToolError(f"{p.name} is a binary file ({_size(p.stat().st_size)}); it can't be read as text")
        if p.stat().st_size > MAX_READ_BYTES and start_line == 1 and max_lines > 400:
            max_lines = 400
        text = _read_text(p)
    lines = text.splitlines()
    start = max(1, int(start_line))
    end = min(len(lines), start - 1 + max(1, int(max_lines)))
    body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n… lines {end + 1}-{len(lines)} not shown (use start_line={end + 1})" if end < len(lines) else ""
    return f"{p}  (lines {start}-{end} of {len(lines)})\n{body}{more}"


def find_files(box, pattern, path=".", max_results=200):
    root = box.resolve(path)
    if not root.is_dir():
        raise ToolError(f"{root} is not a folder")
    found = []
    for p, _ in _walk(root, 12):
        if fnmatch.fnmatch(p.name.lower(), pattern.lower()):
            found.append(str(p.relative_to(root)) + (os.sep if p.is_dir() else ""))
            if len(found) > max_results:
                break
    if not found:
        return f"No matches for {pattern} under {root}"
    extra = "\n… more matches not shown" if len(found) > max_results else ""
    return f"{root}\n" + "\n".join(found[:max_results]) + extra


def search_in_files(box, query, path=".", file_pattern="*", regex=False, ignore_case=True, max_results=100):
    root = box.resolve(path)
    if not root.exists():
        raise ToolError(f"{root} does not exist")
    flags = re.I if ignore_case else 0
    try:
        rx = re.compile(query if regex else re.escape(query), flags)
    except re.error as e:
        raise ToolError(f"bad regular expression: {e}")
    files = [root] if root.is_file() else (p for p, _ in _walk(root, 12) if p.is_file())
    hits = []
    for p in files:
        if not fnmatch.fnmatch(p.name.lower(), file_pattern.lower()):
            continue
        try:
            if p.stat().st_size > MAX_READ_BYTES or _is_binary(p):
                continue
            text = _read_text(p)
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                rel = p.name if root.is_file() else p.relative_to(root)
                hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                if len(hits) > max_results:
                    break
        if len(hits) > max_results:
            break
    if not hits:
        return f"No matches for {query!r} in {root}"
    extra = "\n… more matches not shown" if len(hits) > max_results else ""
    return "\n".join(hits[:max_results]) + extra


def change_directory(box, path):
    return f"Working folder is now {box.set_working_dir(path)}"


# --------------------------------------------------------------- write tools

def write_file(box, path, content, overwrite=False):
    p = box.resolve(path)
    if p.is_dir():
        raise ToolError(f"{p} is a folder")
    if p.exists() and not overwrite:
        raise ToolError(f"{p} already exists; pass overwrite=true to replace it, or use edit_file")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return f"Wrote {p} ({_size(p.stat().st_size)})"


def edit_file(box, path, old_text, new_text, replace_all=False):
    p = box.resolve(path)
    if not p.is_file():
        raise ToolError(f"{p} is not a file")
    text = _read_text(p)
    crlf = "\r\n" in text
    if crlf:                                  # the model writes \n; match and keep the file's line endings
        text = text.replace("\r\n", "\n")
        old_text, new_text = old_text.replace("\r\n", "\n"), new_text.replace("\r\n", "\n")
    count = text.count(old_text)
    if not old_text or count == 0:
        raise ToolError(f"old_text was not found in {p.name}; read the file and copy the text exactly")
    if count > 1 and not replace_all:
        raise ToolError(f"old_text appears {count} times in {p.name}; add more surrounding lines or pass replace_all=true")
    text = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return f"Edited {p} ({count if replace_all else 1} replacement{'s' if replace_all and count > 1 else ''})"


def create_folder(box, path):
    p = box.resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Folder {p} exists"


def move_path(box, source, destination, overwrite=False):
    src, dst = box.resolve(source), box.resolve(destination)
    if not src.exists():
        raise ToolError(f"{src} does not exist")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists() and not overwrite:
        raise ToolError(f"{dst} already exists; pass overwrite=true to replace it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved {src} to {dst}"


def copy_path(box, source, destination, overwrite=False):
    src, dst = box.resolve(source), box.resolve(destination)
    if not src.exists():
        raise ToolError(f"{src} does not exist")
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists() and not overwrite:
        raise ToolError(f"{dst} already exists; pass overwrite=true to replace it")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    else:
        shutil.copy2(src, dst)
    return f"Copied {src} to {dst}"


def _recycle(path: Path) -> bool:
    """Send to the Windows Recycle Bin. False where that isn't possible."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not path.exists()


def delete_path(box, path, recursive=False):
    p = box.resolve(path)
    if not p.exists():
        raise ToolError(f"{p} does not exist")
    if p == Path(p.anchor) or p == Path.home():
        raise ToolError(f"refusing to delete {p}")
    if p.is_dir() and any(p.iterdir()) and not recursive:
        raise ToolError(f"{p} is a folder with files in it; pass recursive=true to delete it all")
    if _recycle(p):
        return f"Moved {p} to the Recycle Bin"
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return f"Deleted {p}"


# --------------------------------------------------------------- the list

PATH = {"type": "string", "description": "Absolute (C:\\Users\\me\\x.txt) or relative to the working folder"}

TOOLS = [
    Tool("list_directory", "List the files and folders in a folder on the user's PC, with sizes and dates.",
         params([], path=PATH,
                pattern={"type": "string", "description": "Only names matching this wildcard, e.g. *.py"},
                recursive={"type": "boolean", "description": "Include subfolders (up to 6 levels)"},
                max_entries={"type": "integer"}),
         list_directory, summary=lambda a: f"List {a.get('path', '.')}"),
    Tool("read_file", "Read a text file (code, notes, logs, CSV...) with line numbers. PDF and Word "
                      "files are converted to text. Long files: read in parts with start_line.",
         params(["path"], path=PATH, start_line={"type": "integer"}, max_lines={"type": "integer"}),
         read_file, summary=lambda a: f"Read {a.get('path')}"),
    Tool("find_files", "Find files or folders by name (wildcards) under a folder, searching subfolders.",
         params(["pattern"], pattern={"type": "string", "description": "e.g. *.json or *report*"}, path=PATH,
                max_results={"type": "integer"}),
         find_files, summary=lambda a: f"Find {a.get('pattern')} in {a.get('path', '.')}"),
    Tool("search_in_files", "Search inside text files for a word or regular expression (like grep). "
                            "Returns file:line: text.",
         params(["query"], query={"type": "string"}, path=PATH,
                file_pattern={"type": "string", "description": "Only files matching this wildcard, e.g. *.py"},
                regex={"type": "boolean"}, ignore_case={"type": "boolean"}, max_results={"type": "integer"}),
         search_in_files, summary=lambda a: f"Search for {a.get('query')!r} in {a.get('path', '.')}"),
    Tool("change_directory", "Change the working folder that relative paths, git and commands start from.",
         params(["path"], path=PATH), change_directory, summary=lambda a: f"Go to {a.get('path')}"),
    Tool("write_file", "Create a text file, or replace one completely with overwrite=true.",
         params(["path", "content"], path=PATH, content={"type": "string"}, overwrite={"type": "boolean"}),
         write_file, kind="write",
         summary=lambda a: f"{'Overwrite' if a.get('overwrite') else 'Create'} {a.get('path')} "
                           f"({len(a.get('content', '')):,} characters)"),
    Tool("edit_file", "Change part of a text file: replaces old_text (copied exactly from the file, "
                      "unique unless replace_all) with new_text.",
         params(["path", "old_text", "new_text"], path=PATH, old_text={"type": "string"},
                new_text={"type": "string"}, replace_all={"type": "boolean"}),
         edit_file, kind="write", summary=lambda a: f"Edit {a.get('path')}"),
    Tool("create_folder", "Create a folder (and any missing parent folders).",
         params(["path"], path=PATH), create_folder, kind="write", summary=lambda a: f"Create folder {a.get('path')}"),
    Tool("move_path", "Move or rename a file or folder.",
         params(["source", "destination"], source=PATH, destination=PATH, overwrite={"type": "boolean"}),
         move_path, kind="write", summary=lambda a: f"Move {a.get('source')} to {a.get('destination')}"),
    Tool("copy_path", "Copy a file or folder.",
         params(["source", "destination"], source=PATH, destination=PATH, overwrite={"type": "boolean"}),
         copy_path, kind="write", summary=lambda a: f"Copy {a.get('source')} to {a.get('destination')}"),
    Tool("delete_path", "Delete a file or folder (to the Recycle Bin on Windows). Folders with files "
                        "need recursive=true.",
         params(["path"], path=PATH, recursive={"type": "boolean"}),
         delete_path, kind="write",
         summary=lambda a: f"Delete {a.get('path')}{' and everything in it' if a.get('recursive') else ''}"),
]
