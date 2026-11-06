"""The Ubuntu sandbox (chat_server/sandbox.py), against a fake Docker.

The last test runs real code in a real container, only when Docker and the
chatui-sandbox image are there (SANDBOX_LIVE=1 to insist).

Run from Server/ollama:  python -m pytest -q tests
"""

import base64
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import sandbox  # noqa: E402
from chat_server.app import create_app  # noqa: E402
from chat_server.config import Config  # noqa: E402


class FakeDocker:
    """Answers the docker calls sandbox.py makes, and records them."""

    def __init__(self, running=False, run_error="", exec_reply=(0, "hello\n", False, False)):
        self.calls = []
        self.running = running
        self.run_error = run_error
        self.exec_reply = exec_reply

    async def __call__(self, args, stdin=None, timeout=60, limit=1_000_000):
        self.calls.append({"args": args, "stdin": stdin, "timeout": timeout, "limit": limit})
        cmd = args[0]
        if cmd == "inspect":
            return (0, "true\n", False, False) if self.running else (1, "Error: No such object", False, False)
        if cmd == "run":
            if self.run_error:
                return 125, self.run_error, False, False
            self.running = True
            return 0, "abc123\n", False, False
        if cmd == "exec" and "timeout" in args:
            return self.exec_reply
        return 0, "", False, False

    def named(self, cmd):
        return [c for c in self.calls if c["args"][0] == cmd]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(Config, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(sandbox, "docker_installed", lambda: True)
    monkeypatch.setattr(sandbox, "SANDBOXES", sandbox.Sandboxes())
    d = FakeDocker()
    monkeypatch.setattr(sandbox, "docker", d)
    return d


def client():
    return TestClient(create_app())


def test_status_reports_off_and_missing_docker(monkeypatch):
    monkeypatch.setattr(Config, "SANDBOX_ENABLED", False)
    assert sandbox.status()["available"] is False
    monkeypatch.setattr(Config, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(sandbox, "docker_installed", lambda: False)
    assert "Docker" in sandbox.status()["reason"]
    with TestClient(create_app()) as c:
        r = c.post("/v1/sandbox/run", json={"session": "s1", "code": "print(1)"})
    assert r.status_code == 503


def test_python_code_starts_a_locked_down_container_and_runs(fake):
    with client() as c:
        r = c.post("/v1/sandbox/run", json={"session": "chat1", "code": "print('hello')", "timeout": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0 and body["output"] == "hello\n" and body["timed_out"] is False
    assert body["command"] == "python3 main.py"

    run = fake.named("run")[0]["args"]
    for flag in ("--read-only", "--init", "--pull", "--cap-drop", "--pids-limit", "--memory"):
        assert flag in run
    assert run[run.index("--network") + 1] == "none"
    assert run[run.index("--name") + 1] == "chatui-sandbox-chat1"
    assert not any(a in ("-v", "--volume", "--mount", "--privileged") for a in run)

    write = next(c for c in fake.named("exec") if "python3" in c["args"] and "-c" in c["args"])
    assert json.loads(write["stdin"]) == {"main.py": "print('hello')"}

    execd = [c for c in fake.named("exec") if "timeout" in c["args"]][0]
    args = execd["args"]
    assert args[args.index("timeout") + 1:args.index("timeout") + 4] == ["-k", "2", "5"]
    assert args[-2:] == ["-c", "python3 main.py"]
    assert execd["limit"] == Config.SANDBOX_OUTPUT_CHARS
    assert [*sandbox.EXEC, "chatui-sandbox-chat1", "bash", "-c", "kill -9 -1 2>/dev/null; true"] in \
        [c["args"] for c in fake.calls]           # what the run left behind is stopped
    assert all(c["args"][:3] == ["exec", "-u", "sandbox"] for c in fake.named("exec"))


def test_running_container_is_reused(fake):
    fake.running = True
    with client() as c:
        c.post("/v1/sandbox/run", json={"session": "chat1", "command": "ls"})
    assert not fake.named("run")


def test_c_and_custom_command_and_extra_files(fake):
    files, cmd = sandbox.plan_run("c", "int main(){return 0;}", {"lib.h": "#pragma once"}, None)
    assert set(files) == {"main.c", "lib.h"} and cmd.startswith("gcc")
    files, cmd = sandbox.plan_run("python", "x = 1", {"test_x.py": "def test(): pass"}, "pytest -q")
    assert cmd == "pytest -q"
    with pytest.raises(sandbox.SandboxError):
        sandbox.plan_run(None, None, {}, None)
    with pytest.raises(sandbox.SandboxError):
        sandbox.plan_run("cobol", "x", {}, None)


def test_archive_is_unpacked_into_work(fake):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"print('hi')"
        info = tarfile.TarInfo("proj/app.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with client() as c:
        r = c.post("/v1/sandbox/run", json={"session": "chat1", "command": "python3 proj/app.py",
                                            "archive": base64.b64encode(buf.getvalue()).decode()})
    assert r.status_code == 200
    untar = next(c for c in fake.named("exec") if "tar" in c["args"])
    assert untar["stdin"] == buf.getvalue()


def test_bad_requests(fake):
    with client() as c:
        assert c.post("/v1/sandbox/run", json={"session": "../etc", "code": "x"}).status_code == 400
        assert c.post("/v1/sandbox/run", json={"session": "a", "archive": "%%%", "command": "ls"}).status_code == 400
        assert c.post("/v1/sandbox/run", json={"session": "a"}).status_code == 400


def test_missing_image_says_how_to_build(fake):
    fake.run_error = "docker: Error response from daemon: No such image: chatui-sandbox:latest."
    with client() as c:
        r = c.post("/v1/sandbox/run", json={"session": "a", "code": "print(1)"})
    assert r.status_code == 400 and "docker build -t chatui-sandbox" in r.json()["error"]["message"]


def test_timeout_is_reported(fake):
    fake.exec_reply = (124, "partial\n", False, False)
    with client() as c:
        body = c.post("/v1/sandbox/run", json={"session": "a", "command": "sleep 99", "timeout": 1}).json()
    assert body["timed_out"] is True and body["exit_code"] == 124


def test_timeout_is_capped(fake, monkeypatch):
    monkeypatch.setattr(Config, "SANDBOX_MAX_TIMEOUT", 10)
    with client() as c:
        body = c.post("/v1/sandbox/run", json={"session": "a", "command": "true", "timeout": 9999}).json()
    assert body["timeout"] == 10


def test_reset_and_idle_containers_are_removed(fake, monkeypatch):
    with client() as c:
        assert c.post("/v1/sandbox/reset", json={"session": "a"}).json() == {"status": "reset"}
    assert ["rm", "-f", "chatui-sandbox-a"] in [c["args"] for c in fake.calls]

    import asyncio
    boxes = sandbox.SANDBOXES
    monkeypatch.setattr(Config, "SANDBOX_MAX_SESSIONS", 2)
    boxes.last_used = {"old": 0.0, "b": 1e12, "c": 1e12 + 1, "d": 1e12 + 2}
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: 1e12 + 3)
    fake.calls.clear()
    asyncio.run(boxes.tidy(keep="d"))
    removed = {c["args"][-1] for c in fake.named("rm")}
    assert removed == {"chatui-sandbox-old", "chatui-sandbox-b"}


def test_docker_output_is_capped():
    """The real reader keeps the start and end of a flood of output."""
    import asyncio
    if not sandbox.docker_installed():
        pytest.skip("docker not installed")
    code, out, cut, timed_out = asyncio.run(sandbox.docker(["version", "--format", "{{.Client.Version}}"],
                                                           limit=3))
    assert code == 0 and cut and "[output cut]" in out


def _live():
    if not sandbox.docker_installed():
        return False
    r = subprocess.run(["docker", "image", "inspect", Config.SANDBOX_IMAGE], capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not (_live() or os.getenv("SANDBOX_LIVE")), reason="docker or the sandbox image is missing")
def test_live_sandbox(monkeypatch):
    import asyncio
    monkeypatch.setattr(sandbox, "SANDBOXES", sandbox.Sandboxes())
    session = "pytest-live"

    async def go():
        try:
            ok = await sandbox.run(session, "python", "import sys; print(sys.version_info[:2])")
            files = await sandbox.run(session, command="ls /work && whoami")
            net = await sandbox.run(session, "python",
                                    "import socket\ntry:\n socket.create_connection(('1.1.1.1', 53), 3)\n print('online')\n"
                                    "except OSError as e:\n print('offline', e)")
            ro = await sandbox.run(session, command="touch /usr/x 2>&1; echo rc=$?")
            slow = await sandbox.run(session, command="sleep 30", timeout=2)
            c = await sandbox.run(session, "c", '#include <stdio.h>\nint main(){puts("from c");return 3;}')
            await sandbox.run(session, command="sleep 300 & sleep 300 & echo started")
            left = await sandbox.run(session, command="ps -eo comm | grep -c '^sleep$' || true")
            return ok, files, net, ro, slow, c, left
        finally:
            await sandbox.SANDBOXES.remove(session)

    ok, files, net, ro, slow, c, left = asyncio.run(go())
    assert ok["exit_code"] == 0 and "(3, 12)" in ok["output"]
    assert "main.py" in files["output"] and "sandbox" in files["output"]
    assert "offline" in net["output"]
    assert "Read-only" in ro["output"] or "rc=1" in ro["output"]
    assert slow["timed_out"] and slow["seconds"] < 10
    assert c["exit_code"] == 3 and "from c" in c["output"]
    assert left["output"].strip() == "1"          # only the container's own sleep; background ones stopped
