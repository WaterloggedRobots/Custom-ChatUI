"""Terminal tool: run a PowerShell or cmd command on this PC (bash elsewhere)."""

import os
import subprocess
import sys

from . import Tool, ToolError, params

WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)     # no console flashing up behind the GUI
MAX_TIMEOUT = 600


def _kill_tree(proc: subprocess.Popen):
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()


def run_process(argv, cwd, timeout=60, env=None) -> tuple:
    """(exit code, combined output). Raises ToolError on timeout or a missing program."""
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise ToolError(f"{argv[0]} was not found; is it installed and on PATH?")
    except NotADirectoryError:
        raise ToolError(f"{cwd} is not a folder")
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        text = out.decode("utf-8", errors="replace")
        raise ToolError(f"stopped after {timeout}s without finishing. Output so far:\n{text}")
    return proc.returncode, out.decode("utf-8", errors="replace")


def shell_argv(command: str, shell: str):
    if not WINDOWS:
        return ["bash", "-lc", command]
    if shell == "cmd":
        return ["cmd.exe", "/d", "/s", "/c", f"chcp 65001>nul & {command}"]
    # Windows PowerShell 5 writes in the console code page unless told otherwise.
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
                        + command]


def run_command(box, command, shell="powershell", cwd=None, timeout=60):
    if not command.strip():
        raise ToolError("empty command")
    folder = box.resolve(cwd) if cwd else box.working_dir
    if not folder.is_dir():
        raise ToolError(f"{folder} is not a folder")
    timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    code, out = run_process(shell_argv(command, shell), folder, timeout, env)
    return f"exit code {code}  (in {folder})\n{out.rstrip() or '(no output)'}"


TOOLS = [
    Tool("run_command",
         "Run a command in a terminal on the user's Windows PC and return its output and exit code. "
         "PowerShell by default (shell='cmd' for cmd.exe). Not interactive: commands that wait for input "
         "fail. Starts in the working folder unless cwd is given.",
         params(["command"], command={"type": "string"},
                shell={"type": "string", "enum": ["powershell", "cmd"]},
                cwd={"type": "string", "description": "Folder to run in"},
                timeout={"type": "integer", "description": f"Seconds, default 60, at most {MAX_TIMEOUT}"}),
         run_command, kind="run",
         summary=lambda a: f"Run in {a.get('shell') or 'powershell'}"
                           f"{' in ' + a['cwd'] if a.get('cwd') else ''}:\n{a.get('command')}"),
]
