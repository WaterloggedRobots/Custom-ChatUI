"""Signing git in to GitHub on this PC, for the git tools' pull, push and clone.

git keeps the login itself, in its credential helper; this module only talks to
it, so a login made here also works in a terminal and the other way round.
  - Browser: Git Credential Manager (part of Git for Windows) opens GitHub's
    sign-in page and stores the token it gets.
  - Token: a personal access token you make on github.com. It's checked
    against the GitHub API, then handed to git with `git credential approve`.
Nothing here keeps a copy of the token, and the token is never shown again.
The commit name and email are git's global user.name and user.email.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

HOST = "github.com"
TOKEN_PAGE = "https://github.com/settings/tokens/new?scopes=repo,workflow&description=ChatUI%20git"
API_USER = "https://api.github.com/user"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
QUIET = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}   # never ask, just answer


class LoginError(Exception):
    pass


def _git(args, stdin="", timeout=30, quiet=True):
    """(exit code, stdout, stderr) of a git command."""
    env = {**os.environ, **(QUIET if quiet else {})}
    try:
        p = subprocess.run(["git", *args], input=stdin, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise LoginError("git isn't installed (or not on PATH). Install Git for Windows: https://git-scm.com")
    except subprocess.TimeoutExpired:
        raise LoginError(f"git {' '.join(args)} took longer than {timeout}s")
    return p.returncode, p.stdout, p.stderr


def _credential(**fields) -> str:
    return "".join(f"{k}={v}\n" for k, v in {"protocol": "https", "host": HOST, **fields}.items()) + "\n"


def _parse(text) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def has_credential_manager() -> bool:
    try:
        return _git(["credential-manager", "--version"], timeout=15)[0] == 0
    except LoginError:
        return False


def helper() -> str:
    """git's credential.helper ("" if none is set)."""
    return _git(["config", "--get", "credential.helper"])[1].strip()


def signed_in_user():
    """The GitHub user git has a login for, or None. Asks nothing and opens no window."""
    code, out, _ = _git(["credential", "fill"], _credential(), timeout=20)
    if code != 0:
        return None
    found = _parse(out)
    return found.get("username") if found.get("password") else None


def browser_login(timeout=600) -> str:
    """Sign in through Git Credential Manager's window or browser page; returns the user name."""
    if not has_credential_manager():
        raise LoginError("Git Credential Manager isn't installed, so use a token instead. "
                         "(It comes with Git for Windows: https://git-scm.com)")
    code, out, err = _git(["credential-manager", "github", "login"], timeout=timeout, quiet=False)
    if code != 0:
        raise LoginError((err or out).strip() or "the sign-in was cancelled")
    return signed_in_user() or ""


def check_token(token: str, timeout=20) -> str:
    """The GitHub user name the token belongs to. Raises LoginError if GitHub refuses it."""
    req = urllib.request.Request(API_USER, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "ChatUI"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))["login"]
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise LoginError("GitHub doesn't accept this token (wrong, expired or revoked)")
        raise LoginError(f"GitHub answered {e.code} {e.reason}")
    except (urllib.error.URLError, OSError) as e:
        raise LoginError(f"can't reach GitHub: {getattr(e, 'reason', e)}")
    except (ValueError, KeyError):
        raise LoginError("GitHub's answer didn't name a user")


def token_login(token: str) -> str:
    """Check the token, store it in git's credential helper, and return the user name.

    With no helper set, it sets one first: Git Credential Manager if it's there,
    else git's plain-text store (~/.git-credentials)."""
    token = token.strip()
    if not token:
        raise LoginError("paste a token first")
    user = check_token(token)
    if not helper():
        chosen = "manager" if has_credential_manager() else "store"
        _git(["config", "--global", "credential.helper", chosen])
    old = signed_in_user()
    if old:                                  # replace the old login, or git keeps using it
        _git(["credential", "reject"], _credential(username=old))
    code, out, err = _git(["credential", "approve"], _credential(username=user, password=token))
    if code != 0:
        raise LoginError((err or out).strip() or "git couldn't store the token")
    if signed_in_user() != user:
        raise LoginError(f"git's credential helper ({helper()}) didn't keep the token")
    return user


def sign_out() -> None:
    """Forget git's GitHub login."""
    user = signed_in_user()
    if user and has_credential_manager():
        _git(["credential-manager", "github", "logout", user])
    _git(["credential", "reject"], _credential(**({"username": user} if user else {})))


def identity() -> tuple:
    """(name, email) that commits are made with."""
    return (_git(["config", "--global", "--get", "user.name"])[1].strip(),
            _git(["config", "--global", "--get", "user.email"])[1].strip())


def set_identity(name: str, email: str) -> None:
    for key, value in (("user.name", name.strip()), ("user.email", email.strip())):
        if value:
            code, out, err = _git(["config", "--global", key, value])
            if code != 0:
                raise LoginError((err or out).strip())
