"""Tools that run on the Ubuntu server instead of this PC (rea.py, ubuntu.py).

The server lists them (GET /v1/<prefix>/tools: OpenAI `tools` plus each one's
kind and group) and runs them (POST /v1/<prefix>/call). Every request carries
the token from Settings > General (X-ChatUI-Token); the server refuses them all
without it. Here they become ordinary Tools, so reads run at once and changes
go through the same Allow / Always allow / Deny box, with a summary that says
they act on the server.

The list is fetched the first time a chat that has the group on asks for its
tools, from the worker thread, and again when the server address or token
changes; after a failure it is tried again after RETRY_SECONDS.
"""

import time
from typing import Callable, Dict, List, Optional

import requests

from . import Tool, ToolError

TOKEN_HEADER = "X-ChatUI-Token"
RETRY_SECONDS = 30
KINDS = ("read", "write", "run")


class Remote:
    def __init__(self, prefix: str, groups: Dict[str, str], label: str,
                 summaries: Dict[str, Callable[[dict], str]], call_timeout: float):
        self.prefix = prefix              # rea -> /v1/rea/...
        self.groups = groups              # the server's group of a tool -> the ToolBox group it belongs to
        self.label = label                # what the user calls it, for errors
        self.summaries = summaries        # tool name -> one line for the approval box (the server is added)
        self.call_timeout = call_timeout
        self.tools: List[Tool] = []
        self.info: dict = {}              # the rest of the server's answer (e.g. "roots")
        self.error = ""                   # why the list couldn't be fetched; "" = it was
        self._key = None                  # (address, token) the list came from
        self._failed_at = 0.0

    def load(self, address: str, token: str) -> bool:
        """Fetch the list if it isn't fresh. True if it changed."""
        key = (address, token)
        if key == self._key and (not self.error or time.monotonic() - self._failed_at < RETRY_SECONDS):
            return False
        self._key = key
        try:
            body = self._request("get", "tools", address, token, timeout=(5, 200))
            self.tools = [self._tool(entry, body.get("meta") or {}, address, token)
                          for entry in body.get("tools") or [] if (entry.get("function") or {}).get("name")]
            self.info = {k: v for k, v in body.items() if k not in ("tools", "meta")}
            self.error = body.get("error") or ""
            if self.error:
                self._failed_at = time.monotonic()
        except ToolError as e:
            self.tools, self.info, self.error = [], {}, str(e)
            self._failed_at = time.monotonic()
        return True

    def _tool(self, entry, meta, address, token) -> Tool:
        fn = entry["function"]
        name = fn["name"]
        about = meta.get(name) or {}
        kind = about.get("kind") if about.get("kind") in KINDS else "run"   # unknown: ask first
        detail = self.summaries.get(name)

        def summary(args):
            return f"On the Ubuntu server: {detail(args) if detail else name}"

        def call(box, **args):
            return self._request("post", "call", address, token, json={"name": name, "arguments": args},
                                 timeout=(10, self.call_timeout)).get("output", "")

        return Tool(name, fn.get("description", ""), fn.get("parameters") or {"type": "object", "properties": {}},
                    call, kind=kind, summary=summary,
                    group=self.groups.get(about.get("group"), next(iter(self.groups.values()))))

    def _request(self, method, what, address, token, **kw) -> dict:
        url = f"{address}/v1/{self.prefix}/{what}"
        try:
            r = requests.request(method, url, headers={TOKEN_HEADER: token or ""}, **kw)
        except requests.Timeout:
            raise ToolError(f"the Ubuntu server took too long to answer ({self.label})")
        except requests.RequestException as e:
            raise ToolError(f"can't reach the Ubuntu server at {address}: {e}")
        if r.status_code == 404:
            raise ToolError(f"the server at {address} is older than this app and has no {self.label}; update it")
        if r.status_code != 200:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:300]
            raise ToolError(f"the server refused ({r.status_code}): {msg}")
        try:
            return r.json()
        except ValueError:
            raise ToolError("the server's answer was not JSON")


def first_line(text: Optional[str], limit: int = 200) -> str:
    text = (text or "").strip().splitlines()
    return (text[0][:limit] + ("…" if len(text[0]) > limit or len(text) > 1 else "")) if text else ""
