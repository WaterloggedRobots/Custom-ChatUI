"""The chat workflow.

One request goes through at most three steps:

1. Attachments in the newest messages are unpacked: images go to the model
   natively (Ollama's `images`), documents become text blocks.
2. Optional web search. In "auto" mode one short structured-output call asks
   the model whether the question needs the web and which queries to run;
   "on" always searches; "off" never does. Queries run in parallel, the top
   pages are read in parallel, and the results are given to the model as
   numbered sources it can cite.
3. The answer is streamed. Reasoning comes from the model's native thinking
   channel (`think`), kept separate from the answer, instead of a prompted
   chain of thought mixed into the reply.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

import httpx

from . import search as web
from .config import Config
from .documents import Attachment, AttachmentError, attachment_block, decode_data_url, load_attachment
from .ollama import Ollama

log = logging.getLogger(__name__)

WebMode = str  # "off" | "auto" | "on"


# --------------------------------------------------------------------------
# Events streamed back to the HTTP layer
# --------------------------------------------------------------------------

@dataclass
class Event:
    kind: str            # status | sources | thinking | content | done
    data: Any = None


@dataclass
class ChatJob:
    model: str
    messages: List[Dict[str, Any]]
    think: Optional[Union[bool, str]] = None
    web_search: WebMode = "off"
    options: Dict[str, Any] = field(default_factory=dict)
    format: Optional[Union[str, Dict[str, Any]]] = None
    keep_alive: Optional[Union[int, str]] = None
    prepared: Optional[List[Dict[str, Any]]] = None   # Ollama-shape messages, see prepare()


# --------------------------------------------------------------------------
# Messages: OpenAI shape -> Ollama shape
# --------------------------------------------------------------------------

def _part_text(part: Dict[str, Any]) -> Optional[str]:
    if part.get("type") in ("text", "input_text"):
        return part.get("text", "")
    return None


def _part_attachment(part: Dict[str, Any]) -> Optional[Attachment]:
    kind = part.get("type")
    if kind in ("image_url", "input_image"):
        url = part.get("image_url")
        url = url.get("url") if isinstance(url, dict) else url
        if not url:
            return None
        if not url.startswith("data:"):
            raise AttachmentError("images must be sent inline as data: URLs")
        mime, raw = decode_data_url(url)
        return load_attachment(part.get("name") or "image", raw, mime)
    if kind in ("file", "input_file"):
        f = part.get("file", part)
        data = f.get("file_data") or f.get("data")
        if not data:
            raise AttachmentError("file parts need file_data (a base64 data: URL)")
        mime, raw = decode_data_url(data)
        return load_attachment(f.get("filename") or "file", raw, mime)
    return None


def to_ollama_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in messages:
        role = m.get("role", "user")
        if role == "developer":
            role = "system"
        if role not in ("system", "user", "assistant", "tool"):
            continue
        content = m.get("content")
        msg: Dict[str, Any] = {"role": role}
        if isinstance(content, list):
            texts, images, files = [], [], []
            for part in content:
                t = _part_text(part)
                if t is not None:
                    texts.append(t)
                    continue
                att = _part_attachment(part)
                if att is None:
                    continue
                (images if att.kind == "image" else files).append(att)
            body = "\n\n".join(attachment_block(f) for f in files)
            text = "\n".join(texts)
            msg["content"] = f"{body}\n\n{text}".strip() if body else text
            if images:
                msg["images"] = [a.data for a in images]
        else:
            msg["content"] = content or ""
        if role == "assistant":
            # Earlier turns' reasoning is not sent back; the answer is enough.
            msg["content"] = strip_think_tags(msg["content"])
        if m.get("images") and role == "user":   # Ollama-style images pass straight through
            msg.setdefault("images", []).extend(m["images"])
        out.append(msg)
    return out


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_think_tags(text: str) -> str:
    return _THINK_RE.sub("", text or "")


def last_user_text(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            # Without the attached documents, which the planner doesn't need.
            return re.sub(r"<file name=.*?</file>\s*", "", m.get("content", ""), flags=re.S).strip()
    return ""


# --------------------------------------------------------------------------
# Context budget
# --------------------------------------------------------------------------

IMAGE_TOKENS = 800


def estimate_tokens(msg: Dict[str, Any]) -> int:
    return len(msg.get("content", "")) // 3 + 8 + IMAGE_TOKENS * len(msg.get("images", []))


def fit_context(messages: List[Dict[str, Any]], num_ctx: int, reserve: int) -> List[Dict[str, Any]]:
    """Drop the oldest non-system turns until the prompt fits, so Ollama never
    silently cuts the system prompt off the front."""
    budget = max(num_ctx - reserve, 1024)
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    used = sum(estimate_tokens(m) for m in system)
    kept: List[Dict[str, Any]] = []
    for m in reversed(rest):
        cost = estimate_tokens(m)
        if kept and used + cost > budget:
            break
        kept.append(m)
        used += cost
    kept.reverse()
    # Never start the conversation with an orphaned assistant turn.
    while len(kept) > 1 and kept[0]["role"] != "user":
        kept.pop(0)
    if len(kept) < len(rest):
        log.info("context: dropped %d old messages to fit num_ctx=%d", len(rest) - len(kept), num_ctx)
    return system + kept


# --------------------------------------------------------------------------
# Web search step
# --------------------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "search": {"type": "boolean"},
        "queries": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["search", "queries"],
}

PLAN_PROMPT = """You decide whether answering the user's latest message needs a live web search.
Today is {today}.
Search when the answer depends on recent or changing facts (news, prices, releases, schedules,
weather, versions), niche facts you are unsure of, or when the user asks you to look something up.
Do not search for greetings, opinions, creative writing, maths, coding help, or questions about
attached files or earlier messages.
If searching, give 1 to 3 short, specific web queries (3 to 8 words each, no filler).
Reply only with JSON: {{"search": true|false, "queries": [...]}}"""


def _plan_context(messages: List[Dict[str, Any]], limit: int = 6) -> str:
    lines = []
    for m in [m for m in messages if m["role"] in ("user", "assistant")][-limit:-1]:
        text = re.sub(r"<file name=.*?</file>\s*", "[file] ", m.get("content", ""), flags=re.S)
        lines.append(f"{m['role']}: {text[:800]}")
    return "\n".join(lines)


async def plan_search(ollama: Ollama, model: str, messages: List[Dict[str, Any]], mode: WebMode,
                      num_ctx: int = Config.NUM_CTX) -> List[str]:
    question = last_user_text(messages)
    if not question:
        return []
    history = _plan_context(messages)
    prompt = (f"Conversation so far:\n{history}\n\n" if history else "") + f"Latest message:\n{question[:2000]}"
    try:
        reply = await ollama.chat({
            "model": Config.PLANNER_MODEL or model,
            "messages": [
                {"role": "system", "content": PLAN_PROMPT.format(today=date.today().isoformat())},
                {"role": "user", "content": prompt},
            ],
            "format": PLAN_SCHEMA,
            "think": False,
            "keep_alive": Config.KEEP_ALIVE,
            # Same num_ctx as the answer: a different value makes Ollama reload the model.
            "options": {"temperature": 0, "num_predict": 200, "num_ctx": num_ctx},
        })
        plan = json.loads(reply.get("message", {}).get("content") or "{}")
        queries = [str(q).strip() for q in plan.get("queries", []) if str(q).strip()][:3]
        wants = bool(plan.get("search"))
    except Exception as e:
        log.warning("search planner failed: %s", e)
        queries, wants = [], False
    if mode == "on":
        return queries or [question[:200]]
    return queries if wants else []


def sources_block(hits: List[web.Hit]) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        body = h.text or h.snippet
        parts.append(f"[{i}] {h.title}\nURL: {h.link}\n{body}")
    return (
        f"<web_results retrieved=\"{date.today().isoformat()}\">\n"
        + "\n\n".join(parts)
        + "\n</web_results>\n"
        "Use the web results above where they help, and cite them inline like [1] or [2]. "
        "If they don't cover the question, say so and answer from what you know."
    )


def attach_to_last_user(messages: List[Dict[str, Any]], block: str) -> None:
    for m in reversed(messages):
        if m["role"] == "user":
            m["content"] = f"{block}\n\n{m.get('content', '')}"
            return


# --------------------------------------------------------------------------
# Streaming split of inline <think> tags (models that don't use the native
# thinking channel)
# --------------------------------------------------------------------------

class ThinkSplitter:
    def __init__(self):
        self.mode = "start"     # start | think | answer
        self.buf = ""

    def feed(self, text: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        self.buf += text
        while self.buf:
            if self.mode == "start":
                stripped = self.buf.lstrip()
                if stripped.startswith("<think>"):
                    self.buf = stripped[len("<think>"):]
                    self.mode = "think"
                elif "<think>".startswith(stripped):
                    break                      # may still become <think>
                else:
                    self.mode = "answer"
            elif self.mode == "think":
                end = self.buf.find("</think>")
                if end >= 0:
                    if end:
                        out.append(("thinking", self.buf[:end]))
                    self.buf = self.buf[end + len("</think>"):].lstrip()
                    self.mode = "answer"
                else:
                    keep = len("</think>") - 1
                    if len(self.buf) > keep:
                        out.append(("thinking", self.buf[:-keep]))
                        self.buf = self.buf[-keep:]
                    break
            else:
                out.append(("content", self.buf))
                self.buf = ""
        return out

    def flush(self) -> List[Tuple[str, str]]:
        if not self.buf:
            return []
        kind = "thinking" if self.mode == "think" else "content"
        text, self.buf = self.buf, ""
        return [(kind, text)]


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------

def _think_value(model: str, think: Optional[Union[bool, str]]) -> Optional[Union[bool, str]]:
    if isinstance(think, str):
        level = think.lower()
        if level in ("none", "off", "false", "0"):
            return False
        if level in ("on", "true", "1"):
            return True
        # Only gpt-oss takes effort levels; other thinking models take a bool.
        return level if "gpt-oss" in model else True
    return think


def prepare(job: ChatJob) -> ChatJob:
    """Unpack attachments and validate, so bad input fails before a stream starts."""
    if job.prepared is None:
        job.prepared = to_ollama_messages(job.messages)
        if not any(m["role"] == "user" for m in job.prepared):
            raise ValueError("messages must include a user message")
    return job


async def run_chat(job: ChatJob, ollama: Ollama, http: httpx.AsyncClient) -> AsyncIterator[Event]:
    messages = prepare(job).prepared

    options = dict(job.options)
    options.setdefault("num_ctx", Config.NUM_CTX)

    if job.web_search in ("auto", "on"):
        yield Event("status", "Checking whether a web search would help…" if job.web_search == "auto" else "Planning the search…")
        queries = await plan_search(ollama, job.model, messages, job.web_search, options["num_ctx"])
        if queries:
            yield Event("status", "Searching: " + " · ".join(queries))
            hits = await web.search_many(http, queries)
            if hits:
                n = min(len(hits), Config.SEARCH_FETCH_PAGES)
                yield Event("status", f"Reading {n} page{'s' if n != 1 else ''}…")
                await web.fetch_pages(http, hits)
                attach_to_last_user(messages, sources_block(hits))
                yield Event("sources", [{"n": i, "title": h.title, "url": h.link} for i, h in enumerate(hits, 1)])
            else:
                yield Event("status", "The search returned nothing; answering without it.")
        yield Event("status", "")

    reserve = options.get("num_predict") if (options.get("num_predict") or 0) > 0 else 4096
    messages = fit_context(messages, options["num_ctx"], reserve)

    payload: Dict[str, Any] = {
        "model": job.model,
        "messages": messages,
        "options": options,
        "keep_alive": job.keep_alive if job.keep_alive is not None else Config.KEEP_ALIVE,
    }
    think = _think_value(job.model, job.think)
    if think is not None:
        payload["think"] = think
    if job.format is not None:
        payload["format"] = job.format

    splitter = ThinkSplitter()
    final: Dict[str, Any] = {}
    async for chunk in ollama.stream_chat(payload):
        msg = chunk.get("message") or {}
        if msg.get("thinking"):
            yield Event("thinking", msg["thinking"])
        if msg.get("content"):
            for kind, text in splitter.feed(msg["content"]):
                yield Event(kind, text)
        if chunk.get("done"):
            final = chunk
    for kind, text in splitter.flush():
        yield Event(kind, text)

    prompt_tokens = final.get("prompt_eval_count", 0)
    completion_tokens = final.get("eval_count", 0)
    yield Event("done", {
        "finish_reason": "length" if final.get("done_reason") == "length" else "stop",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "timings": {
            "total_s": round(final.get("total_duration", 0) / 1e9, 2),
            "load_s": round(final.get("load_duration", 0) / 1e9, 2),
            "tokens_per_s": round(completion_tokens / (final["eval_duration"] / 1e9), 1)
            if final.get("eval_duration") else None,
        },
    })
