"""Git tools. They call the git program installed on this PC (Git for Windows)."""

import os

from . import Tool, ToolError, params
from .terminal import run_process

REPO = {"type": "string", "description": "A folder inside the repository; default: the working folder"}


def git(box, args, repo=None, timeout=120, check=True) -> str:
    folder = box.resolve(repo)
    if not folder.is_dir():
        raise ToolError(f"{folder} is not a folder")
    # No terminal password prompt (it would hang); Git Credential Manager's window still works.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat"}
    code, out = run_process(["git", "-c", "color.ui=false", "-c", "core.quotepath=false", *args],
                            folder, timeout, env)
    if check and code != 0:
        raise ToolError(f"git {' '.join(args)} failed (exit {code}):\n{out.strip()}")
    return out.rstrip()


def _files(files):
    if isinstance(files, str):
        files = [files]
    return [str(f) for f in files or []]


# ---------------------------------------------------------------- read

def git_status(box, repo=None):
    return git(box, ["status", "--short", "--branch"], repo) or "clean"


def git_diff(box, repo=None, staged=False, file=None, revision=None):
    args = ["diff", "--stat", "--patch"]
    if staged:
        args.append("--cached")
    if revision:
        args.append(revision)
    if file:
        args += ["--", file]
    return git(box, args, repo) or "no differences"


def git_log(box, repo=None, count=15, file=None, all_branches=False):
    args = ["log", f"-n{max(1, min(int(count), 200))}", "--date=short",
            "--pretty=format:%h %ad %an%d  %s"]
    if all_branches:
        args.append("--all")
    if file:
        args += ["--", file]
    return git(box, args, repo) or "no commits"


def git_show(box, revision="HEAD", repo=None, file=None):
    args = ["show", "--stat", "--patch", revision]
    if file:
        args += ["--", file]
    return git(box, args, repo)


def git_branches(box, repo=None):
    return git(box, ["branch", "-a", "-vv"], repo)


# --------------------------------------------------------------- write

def git_add(box, files, repo=None):
    git(box, ["add", "--", *_files(files)], repo)
    return git_status(box, repo)


def git_commit(box, message, repo=None, all=False):
    args = ["commit", "-m", message]
    if all:
        args.insert(1, "-a")
    return git(box, args, repo)


def git_switch(box, branch, repo=None, create=False):
    return git(box, ["switch", "-c", branch] if create else ["switch", branch], repo) or f"on {branch}"


def git_pull(box, repo=None, remote=None, branch=None):
    return git(box, ["pull", *([remote] if remote else []), *([branch] if branch else [])], repo, timeout=300)


def git_push(box, repo=None, remote=None, branch=None, set_upstream=False):
    args = ["push"]
    if set_upstream:
        args.append("-u")
    if remote:
        args.append(remote)
    if branch:
        args.append(branch)
    return git(box, args, repo, timeout=300) or "pushed"


def git_clone(box, url, destination=None):
    args = ["clone", url]
    if destination:
        args.append(str(box.resolve(destination)))
    return git(box, args, None, timeout=600)


def git_command(box, args, repo=None):
    if isinstance(args, str):
        args = args.split()
    if args and args[0] == "git":
        args = args[1:]
    if not args:
        raise ToolError("no git arguments")
    return git(box, [str(a) for a in args], repo, timeout=300, check=False) or "(no output)"


def _where(a):
    return f" in {a['repo']}" if a.get("repo") else ""


TOOLS = [
    Tool("git_status", "Show the current branch and which files are changed, staged or untracked.",
         params([], repo=REPO), git_status, summary=lambda a: "git status" + _where(a)),
    Tool("git_diff", "Show changes: unstaged by default, staged with staged=true, or against a revision.",
         params([], repo=REPO, staged={"type": "boolean"}, file={"type": "string"},
                revision={"type": "string", "description": "e.g. HEAD~1 or main"}),
         git_diff, summary=lambda a: "git diff" + _where(a)),
    Tool("git_log", "List recent commits (hash, date, author, branch names, message).",
         params([], repo=REPO, count={"type": "integer"}, file={"type": "string"},
                all_branches={"type": "boolean"}),
         git_log, summary=lambda a: "git log" + _where(a)),
    Tool("git_show", "Show one commit's message and changes.",
         params([], revision={"type": "string"}, repo=REPO, file={"type": "string"}),
         git_show, summary=lambda a: f"git show {a.get('revision', 'HEAD')}" + _where(a)),
    Tool("git_branches", "List local and remote branches with their last commit and upstream.",
         params([], repo=REPO), git_branches, summary=lambda a: "git branch -a" + _where(a)),
    Tool("git_add", "Stage files for the next commit ('.' for everything).",
         params(["files"], files={"type": "array", "items": {"type": "string"}}, repo=REPO),
         git_add, kind="write", summary=lambda a: f"git add {' '.join(_files(a.get('files')))}" + _where(a)),
    Tool("git_commit", "Commit the staged changes (all=true also stages every tracked change first).",
         params(["message"], message={"type": "string"}, repo=REPO, all={"type": "boolean"}),
         git_commit, kind="write",
         summary=lambda a: f"git commit{' -a' if a.get('all') else ''}{_where(a)}:\n{a.get('message')}"),
    Tool("git_switch", "Switch to a branch, or create it with create=true.",
         params(["branch"], branch={"type": "string"}, repo=REPO, create={"type": "boolean"}),
         git_switch, kind="write",
         summary=lambda a: f"git switch {'-c ' if a.get('create') else ''}{a.get('branch')}" + _where(a)),
    Tool("git_pull", "Pull (fetch and merge) from the remote.",
         params([], repo=REPO, remote={"type": "string"}, branch={"type": "string"}),
         git_pull, kind="write", summary=lambda a: "git pull" + _where(a)),
    Tool("git_push", "Push commits to the remote (set_upstream=true for a new branch).",
         params([], repo=REPO, remote={"type": "string"}, branch={"type": "string"},
                set_upstream={"type": "boolean"}),
         git_push, kind="write",
         summary=lambda a: f"git push{' -u' if a.get('set_upstream') else ''} {a.get('remote') or ''} "
                           f"{a.get('branch') or ''}".rstrip() + _where(a)),
    Tool("git_clone", "Clone a repository into a folder.",
         params(["url"], url={"type": "string"},
                destination={"type": "string", "description": "Folder to clone into; default: a new folder "
                                                              "named after the repository, in the working folder"}),
         git_clone, kind="write", summary=lambda a: f"git clone {a.get('url')} {a.get('destination') or ''}".rstrip()),
    Tool("git_command", "Run any other git command, e.g. args=['stash', 'list'] or ['merge', 'main'].",
         params(["args"], args={"type": "array", "items": {"type": "string"}}, repo=REPO),
         git_command, kind="run",
         summary=lambda a: "git " + " ".join(a.get("args") if isinstance(a.get("args"), list) else [str(a.get("args"))])
                           + _where(a)),
]
