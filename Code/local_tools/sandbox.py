"""Sandbox tools: run code in an Ubuntu container on the LLM server, not on this PC.

The server keeps one container per chat (Server/ollama/chat_server/sandbox.py), so
files written in one run are still there in the next until reset_sandbox. It has
no network by default and is thrown away after a while, so these tools change
nothing here and don't ask first. Files and folders from this PC can be copied in
(copy_from_pc); nothing comes back out.
"""

import base64
import io
import tarfile

import requests

from . import Tool, ToolError, params
from .files import SKIP_DIRS

MAX_FILE_BYTES = 5 * 1024 * 1024        # bigger files are left out of a copy
MAX_COPY_BYTES = 50 * 1024 * 1024       # all copied files together, before compression
LANGUAGES = ["python", "bash", "javascript", "c", "cpp"]


def pack(box, paths) -> bytes:
    """A .tar.gz of these files and folders, each under its own name, without .git, venvs and the like."""
    buf = io.BytesIO()
    total, skipped = 0, []
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for raw in paths:
            src = box.resolve(raw)
            if not src.exists():
                raise ToolError(f"{src} doesn't exist")
            items = [src] if src.is_file() else sorted(
                p for p in src.rglob("*") if p.is_file() and not p.is_symlink()
                and not SKIP_DIRS.intersection(p.relative_to(src).parts[:-1]))
            for f in items:
                size = f.stat().st_size
                if size > MAX_FILE_BYTES:
                    skipped.append(f.name)
                    continue
                total += size
                if total > MAX_COPY_BYTES:
                    raise ToolError(f"more than {MAX_COPY_BYTES // (1024 * 1024)} MB to copy; "
                                    "copy a smaller folder or only the files the test needs")
                name = src.name if f == src else f"{src.name}/{f.relative_to(src).as_posix()}"
                tar.add(str(f), arcname=name, recursive=False)
    box.sandbox_skipped = skipped
    return buf.getvalue()


def _post(box, path, body, timeout):
    if not box.server_url:
        raise ToolError("the sandbox needs the LLM server, and none is set")
    try:
        r = requests.post(f"{box.server_url()}{path}", json=body, timeout=timeout)
    except requests.RequestException as e:
        raise ToolError(f"can't reach the LLM server for the sandbox: {e}")
    if r.status_code != 200:
        try:
            detail = (r.json().get("error") or {}).get("message") or r.text
        except (ValueError, AttributeError):
            detail = r.text
        raise ToolError(f"sandbox: {str(detail)[:600]}")
    return r.json()


def run_in_sandbox(box, code=None, language="python", command=None, files=None, copy_from_pc=None, timeout=60):
    body = {"session": box.sandbox_session, "language": language, "code": code, "command": command,
            "files": files or None, "timeout": timeout}
    box.sandbox_skipped = []
    if copy_from_pc:
        body["archive"] = base64.b64encode(pack(box, copy_from_pc)).decode("ascii")
    seconds = int(timeout or 60)
    out = _post(box, "/v1/sandbox/run", body, timeout=seconds + 120)
    head = f"exit code {out['exit_code']} after {out['seconds']}s  (Ubuntu sandbox, /work: {out['command']})"
    if out.get("timed_out"):
        head += f"\nStopped: it ran longer than {out['timeout']}s."
    if box.sandbox_skipped:
        head += f"\nNot copied (over {MAX_FILE_BYTES // (1024 * 1024)} MB): {', '.join(box.sandbox_skipped)}"
    return f"{head}\n{out['output'].rstrip() or '(no output)'}"


def reset_sandbox(box):
    _post(box, "/v1/sandbox/reset", {"session": box.sandbox_session}, timeout=60)
    return "The sandbox was reset; /work is empty again."


def _summary(a):
    what = a.get("command") or next(iter((a.get("code") or "").strip().splitlines()), "")
    parts = ([a.get("language") or "python"] if a.get("code") else []) + \
            ([f"copying {', '.join(a['copy_from_pc'])}"] if a.get("copy_from_pc") else [])
    shown = f" ({', '.join(parts)})" if parts else ""
    return f"Run in the Ubuntu sandbox{shown}:\n{what}"


TOOLS = [
    Tool("run_in_sandbox",
         "Run code in a throwaway Ubuntu 24.04 sandbox on the server to test it before the user runs it for "
         "real. Has python3 (pytest, numpy, requests), node, gcc/g++, make and git; no internet. Give `code` "
         "and `language` to save it as main.<ext> in /work and run it, or `command` for any bash command in "
         "/work (e.g. 'pytest -q', 'make test'). `files` writes more files first ({path: text}); "
         "`copy_from_pc` copies files or folders from the user's PC into /work under their own names. Files "
         "stay in /work for later runs in this chat until reset_sandbox, but programs left running in the "
         "background are stopped when each run ends. Linux, not the user's Windows PC: "
         "Windows-only code (paths like C:\\, PowerShell, winreg) won't work here.",
         params([], code={"type": "string", "description": "Source code to save and run"},
                language={"type": "string", "enum": LANGUAGES, "description": "Language of code (default python)"},
                command={"type": "string", "description": "Bash command to run in /work instead of the default"},
                files={"type": "object", "additionalProperties": {"type": "string"},
                       "description": "More files to write first: relative path -> text"},
                copy_from_pc={"type": "array", "items": {"type": "string"},
                              "description": "Files or folders on the PC to copy into /work first"},
                timeout={"type": "integer", "description": "Seconds, default 60, at most 300"}),
         run_in_sandbox, kind="sandbox", summary=_summary),
    Tool("reset_sandbox", "Throw away this chat's sandbox and start the next run with an empty /work.",
         params(), reset_sandbox, kind="sandbox", summary=lambda a: "Reset the Ubuntu sandbox"),
]
