"""An Ubuntu sandbox for the chat model to test code in, before it goes near a real machine.

Each chat gets its own Docker container (from the image in Server/ollama/sandbox),
started on its first run and kept while the chat uses it, so files written in one
run are there for the next. The container is locked down:
- no network unless SANDBOX_NETWORK says otherwise (then pip/npm work)
- capped memory, CPUs and process count; no Linux capabilities; a normal user
- a read-only system; only /work, /tmp and the home folder can be written, and
  they live in memory, so nothing the model does is left on the server's disk
- every command has a time limit, and the output kept is capped; anything it
  left running in the background is stopped when it ends
Idle containers are removed after SANDBOX_IDLE_MINUTES, the oldest when there
are more than SANDBOX_MAX_SESSIONS, and all of them when the server stops.

The client's run_in_sandbox tool (Code/local_tools/sandbox.py) calls this; the
model never reaches Docker itself. Nothing is ever mounted from the server.
Like the other tools that act on this machine, every request needs the shared
token (X-ChatUI-Token, see server_tools.py).

  POST /v1/sandbox/run    {session, language?, code?, files?, archive?, command?, timeout?}
  POST /v1/sandbox/reset  {session}
  GET  /v1/sandbox/status
"""

import asyncio
import base64
import binascii
import json
import logging
import re
import shutil
import time
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import server_tools
from .config import Config

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(server_tools.require_token)])

LABEL = "chatui-sandbox"
NAME_PREFIX = "chatui-sandbox-"
BUILD_HINT = "docker build -t {image} Server/ollama/sandbox"
# Everything runs as the image's normal user; only the container's own init and sleep are root,
# so the clean-up after a run (kill -1 as that user) can't stop the container itself.
EXEC = ["exec", "-u", "sandbox", "-w", "/work"]

# language -> (file the code is saved as, command that runs it)
LANGUAGES = {
    "python": ("main.py", "python3 main.py"),
    "bash": ("main.sh", "bash main.sh"),
    "javascript": ("main.js", "node main.js"),
    "c": ("main.c", "gcc -O2 -Wall -o main main.c -lm && ./main"),
    "cpp": ("main.cpp", "g++ -O2 -Wall -std=c++20 -o main main.cpp && ./main"),
}

# Runs inside the container: writes {path: text} under /work, refusing paths that leave it.
WRITER = r"""
import json, os, sys
root = os.path.realpath("/work")
for rel, text in json.load(sys.stdin).items():
    path = os.path.realpath(os.path.join(root, rel))
    if path != root and not path.startswith(root + os.sep):
        sys.exit(f"{rel} is outside /work")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
"""


class SandboxError(Exception):
    pass


# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------

async def docker(args: List[str], stdin: Optional[bytes] = None, timeout: float = 60,
                 limit: int = 1_000_000) -> Tuple[int, str, bool, bool]:
    """Runs `docker <args>`: (exit code, output, output was cut, timed out).

    Output is read as it comes and at most `limit` bytes are kept (the start and
    the end), so a program printing without end can't fill the server's memory."""
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    head_max, tail_max = limit * 2 // 3, limit // 3
    head, tail, cut = bytearray(), bytearray(), False

    async def feed():
        if stdin is not None:
            proc.stdin.write(stdin)
            await proc.stdin.drain()
            proc.stdin.close()

    async def read():
        nonlocal tail, cut
        while True:
            block = await proc.stdout.read(65536)
            if not block:
                break
            room = head_max - len(head)
            if room > 0:
                head.extend(block[:room])
                block = block[room:]
            if block:
                tail.extend(block)
                if len(tail) > tail_max:
                    cut = True
                    tail = tail[-tail_max:]

    timed_out = False
    try:
        await asyncio.wait_for(asyncio.gather(feed(), read(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        await proc.wait()
    except (BrokenPipeError, ConnectionResetError):
        await proc.wait()
    text = head.decode("utf-8", "replace")
    if cut:
        text += "\n… [output cut] …\n"
    text += tail.decode("utf-8", "replace")
    return proc.returncode if proc.returncode is not None else -1, text, cut, timed_out


def docker_installed() -> bool:
    return shutil.which("docker") is not None


def container_name(session: str) -> str:
    return NAME_PREFIX + session


def run_args(name: str) -> List[str]:
    """`docker run` arguments for a new sandbox container."""
    disk = Config.SANDBOX_DISK
    return [
        "run", "-d", "--init", "--pull", "never", "--name", name, "--label", LABEL, "--hostname", "sandbox", "--user", "0",
        "--network", Config.SANDBOX_NETWORK,
        "--memory", Config.SANDBOX_MEMORY, "--memory-swap", Config.SANDBOX_MEMORY,
        "--cpus", str(Config.SANDBOX_CPUS), "--pids-limit", str(Config.SANDBOX_PIDS),
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", f"/work:rw,exec,size={disk},uid=1000,gid=1000,mode=755",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--tmpfs", "/home/sandbox:rw,exec,size=512m,uid=1000,gid=1000,mode=755",
        Config.SANDBOX_IMAGE, "sleep", "infinity",
    ]


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

class Sandboxes:
    """The containers, one per chat session, and when each was last used."""

    def __init__(self):
        self.last_used: Dict[str, float] = {}
        self.locks: Dict[str, asyncio.Lock] = {}

    def lock(self, session: str) -> asyncio.Lock:
        return self.locks.setdefault(session, asyncio.Lock())

    async def remove(self, session: str):
        self.last_used.pop(session, None)
        await docker(["rm", "-f", container_name(session)], timeout=30)

    async def remove_all(self):
        """Every sandbox container, including ones left by an earlier run of the server."""
        self.last_used.clear()
        code, out, _, _ = await docker(["ps", "-aq", "--filter", f"label={LABEL}"], timeout=30)
        ids = out.split() if code == 0 else []
        if ids:
            await docker(["rm", "-f", *ids], timeout=60)
            log.info("removed %d sandbox container(s)", len(ids))

    async def tidy(self, keep: str = ""):
        """Removes idle containers, and the least recently used past the maximum."""
        now = time.monotonic()
        idle = Config.SANDBOX_IDLE_MINUTES * 60
        old = [s for s, t in self.last_used.items() if s != keep and now - t > idle]
        by_age = sorted((s for s in self.last_used if s != keep and s not in old), key=self.last_used.get)
        extra = len(self.last_used) - len(old) - Config.SANDBOX_MAX_SESSIONS
        for session in old + by_age[:max(0, extra)]:
            if not self.lock(session).locked():
                log.info("removing sandbox %s (idle or over the limit)", session)
                await self.remove(session)

    async def ensure(self, session: str):
        """Starts this session's container unless it is already running."""
        name = container_name(session)
        code, out, _, _ = await docker(["inspect", "-f", "{{.State.Running}}", name], timeout=30)
        if code == 0 and out.strip() == "true":
            return
        if code == 0:                                  # exists but stopped (e.g. after a reboot)
            await docker(["rm", "-f", name], timeout=30)
        await self.tidy(keep=session)
        code, out, _, _ = await docker(run_args(name), timeout=60)
        if code != 0:
            if "No such image" in out or "not found" in out.lower():
                raise SandboxError(f"the sandbox image {Config.SANDBOX_IMAGE} isn't built on the server; "
                                   f"run: {BUILD_HINT.format(image=Config.SANDBOX_IMAGE)}")
            raise SandboxError(f"could not start the sandbox: {out.strip()[:500]}")
        log.info("started sandbox %s", name)


SANDBOXES = Sandboxes()


# --------------------------------------------------------------------------
# Running code
# --------------------------------------------------------------------------

def plan_run(language: Optional[str], code: Optional[str], files: Dict[str, str],
             command: Optional[str]) -> Tuple[Dict[str, str], str]:
    """(files to write, command to run) for one request."""
    files = dict(files or {})
    if code:
        lang = (language or "python").lower()
        if lang not in LANGUAGES:
            raise SandboxError(f"language must be one of {', '.join(LANGUAGES)} (or give a command)")
        filename, default_command = LANGUAGES[lang]
        files[filename] = code
        command = command or default_command
    if not (command or "").strip():
        raise SandboxError("nothing to run: give code, or a command")
    return files, command


async def run(session: str, language: Optional[str] = None, code: Optional[str] = None,
              files: Optional[Dict[str, str]] = None, archive: Optional[bytes] = None,
              command: Optional[str] = None, timeout: Optional[int] = None) -> Dict:
    files, command = plan_run(language, code, files or {}, command)
    timeout = max(1, min(int(timeout or Config.SANDBOX_TIMEOUT), Config.SANDBOX_MAX_TIMEOUT))
    name = container_name(session)
    async with SANDBOXES.lock(session):
        await SANDBOXES.ensure(session)
        SANDBOXES.last_used[session] = time.monotonic()
        if archive:
            rc, out, _, _ = await docker([*EXEC, "-i", name, "tar", "-xzf", "-", "--no-same-owner"],
                                         stdin=archive, timeout=120)
            if rc != 0:
                raise SandboxError(f"could not unpack the copied files: {out.strip()[:500]}")
        if files:
            rc, out, _, _ = await docker([*EXEC, "-i", name, "python3", "-c", WRITER],
                                         stdin=json.dumps(files).encode("utf-8"), timeout=60)
            if rc != 0:
                raise SandboxError(f"could not write the files: {out.strip()[:500]}")
        start = time.monotonic()
        # `timeout` inside the container stops the program; the outer limit is for a stuck docker.
        rc, out, cut, stuck = await docker(
            [*EXEC, name, "timeout", "-k", "2", str(timeout), "bash", "-c", command],
            timeout=timeout + 15, limit=Config.SANDBOX_OUTPUT_CHARS)
        seconds = round(time.monotonic() - start, 2)
        # Stop what the run left in the background (kill -1 spares the caller and init), so a
        # stray server or fork bomb doesn't hold the process limit; if even that fails, start clean.
        if stuck or (await docker([*EXEC, name, "bash", "-c", "kill -9 -1 2>/dev/null; true"], timeout=30))[0]:
            await SANDBOXES.remove(session)
        SANDBOXES.last_used[session] = time.monotonic()
    timed_out = stuck or rc == 124 or (rc == 137 and seconds >= timeout)
    return {"exit_code": rc, "output": out, "timed_out": timed_out, "truncated": cut, "seconds": seconds,
            "command": command, "timeout": timeout}


def status() -> Dict:
    if not Config.SANDBOX_ENABLED:
        return {"available": False, "reason": "turned off on the server (SANDBOX_ENABLED=0)"}
    if not docker_installed():
        return {"available": False, "reason": "Docker isn't installed on the server"}
    return {"available": True, "image": Config.SANDBOX_IMAGE, "network": Config.SANDBOX_NETWORK,
            "memory": Config.SANDBOX_MEMORY, "cpus": Config.SANDBOX_CPUS, "languages": list(LANGUAGES),
            "timeout": Config.SANDBOX_TIMEOUT, "max_timeout": Config.SANDBOX_MAX_TIMEOUT,
            "sessions": len(SANDBOXES.last_used)}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

SESSION_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def _session(text: str) -> str:
    session = (text or "").strip().lower()
    if not SESSION_RE.match(session):
        raise HTTPException(400, "session must be 1-48 of a-z 0-9 _ -")
    return session


def _require():
    s = status()
    if not s["available"]:
        raise HTTPException(503, f"sandbox not available: {s['reason']}")


class RunRequest(BaseModel):
    session: str = Field(..., description="one per chat; the same session keeps its files between runs")
    language: Optional[str] = Field(None, description=" | ".join(LANGUAGES))
    code: Optional[str] = Field(None, description="saved as main.<ext> in /work and run")
    files: Optional[Dict[str, str]] = Field(None, description="more files to write first, path under /work -> text")
    archive: Optional[str] = Field(None, description="base64 .tar.gz unpacked into /work first")
    command: Optional[str] = Field(None, description="bash command to run in /work instead of the default")
    timeout: Optional[int] = Field(None, description="seconds")


class SessionRequest(BaseModel):
    session: str


@router.get("/v1/sandbox/status")
async def sandbox_status():
    return status()


@router.post("/v1/sandbox/run")
async def sandbox_run(req: RunRequest):
    _require()
    session = _session(req.session)
    archive = None
    if req.archive:
        try:
            archive = base64.b64decode(req.archive, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "archive must be base64")
        if len(archive) > Config.SANDBOX_MAX_UPLOAD:
            raise HTTPException(413, f"archive is over {Config.SANDBOX_MAX_UPLOAD // (1024 * 1024)} MB")
    try:
        return await run(session, req.language, req.code, req.files, archive, req.command, req.timeout)
    except SandboxError as e:
        raise HTTPException(400, str(e))


@router.post("/v1/sandbox/reset")
async def sandbox_reset(req: SessionRequest):
    _require()
    session = _session(req.session)
    async with SANDBOXES.lock(session):
        await SANDBOXES.remove(session)
    return {"status": "reset"}
