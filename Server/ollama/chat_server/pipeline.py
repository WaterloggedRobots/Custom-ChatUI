"""The chat workflow.

One request goes through at most three steps:

1. Attachments in the newest messages are unpacked: images go to the model
   natively (Ollama's `images`), documents become text blocks.
2. Optional web search. In "auto" mode the model decides itself: a fixed
   system note lets it reply with only `<search>query | query</search>`, in
   which case the answer is stopped, the searches run and the model is asked
   again with the results. No extra model call is spent on questions that
   don't need the web, and Ollama's prompt cache stays valid between turns.
   "on" always searches (one short call picks the queries); "off" never does.
   Queries run in parallel, the top pages are read in parallel, and the
   results are given to the model as numbered sources it can cite.
3. The answer is streamed. Reasoning comes from the model's native thinking
   channel (`think`), kept separate from the answer, instead of a prompted
   chain of thought mixed into the reply.

Tools (OpenAI `tools`) are passed through to Ollama. The server does not run
them: when the model calls one, the calls are streamed back and the client
runs them (they act on the client's PC) and sends the results in the next
request as `role: tool` messages.
"""

import json
import logging
import re
import time
import uuid
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
    tools: Optional[List[Dict[str, Any]]] = None
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
            if m.get("tool_calls"):
                msg["tool_calls"] = [_ollama_tool_call(c) for c in m["tool_calls"]]
        if role == "tool":
            name = m.get("name") or m.get("tool_name")
            if name:
                msg["tool_name"] = name
        if m.get("images") and role == "user":   # Ollama-style images pass straight through
            msg.setdefault("images", []).extend(m["images"])
        out.append(msg)
    return out


def _ollama_tool_call(call: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI tool call (arguments as a JSON string) -> Ollama (arguments as an object)."""
    fn = call.get("function") or {}
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            args = {}
    return {"function": {"name": fn.get("name", ""), "arguments": args}}


def openai_tool_calls(calls: List[Dict[str, Any]], start: int = 0) -> List[Dict[str, Any]]:
    """Ollama tool calls -> OpenAI shape, with ids so the client can match the results."""
    out = []
    for i, c in enumerate(calls, start):
        fn = c.get("function") or {}
        out.append({"index": i, "id": c.get("id") or f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                    "function": {"name": fn.get("name", ""),
                                 "arguments": json.dumps(fn.get("arguments") or {}, ensure_ascii=False)}})
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


DROP_STEP = 8   # old messages are dropped in blocks, so the prompt's start (and Ollama's cache) stays put


def fit_context(messages: List[Dict[str, Any]], num_ctx: int, reserve: int) -> List[Dict[str, Any]]:
    """Drop the oldest non-system turns until the prompt fits, so Ollama never
    silently cuts the system prompt off the front.

    Messages go in blocks of DROP_STEP counted from the start of the list, not
    one per turn: while a chat grows, the first kept message stays the same for
    several turns, so Ollama can reuse its cached prompt instead of re-reading
    the whole history on every message."""
    budget = max(num_ctx - reserve, 1024)
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    used = sum(estimate_tokens(m) for m in system)
    costs = [estimate_tokens(m) for m in rest]
    drop, total = 0, used + sum(costs)
    while drop < len(rest) - 1 and total > budget:
        total -= costs[drop]
        drop += 1
    if drop:
        drop = min(-(-drop // DROP_STEP) * DROP_STEP, len(rest) - 1)
    # Never start the conversation with an orphaned assistant turn.
    while drop < len(rest) - 1 and rest[drop]["role"] != "user":
        drop += 1
    if drop:
        log.info("context: dropped %d old messages to fit num_ctx=%d", drop, num_ctx)
    return system + rest[drop:]


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
    started = time.monotonic()
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
        log.info("timing: search planner %.1fs (prompt %s tokens), search=%s", time.monotonic() - started,
                 reply.get("prompt_eval_count", "?"), wants)
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
        "If they don't cover the question, say so and answer from what you know. "
        "Answer now; don't search again."
    )


def attach_to_last_user(messages: List[Dict[str, Any]], block: str) -> None:
    for m in reversed(messages):
        if m["role"] == "user":
            m["content"] = f"{block}\n\n{m.get('content', '')}"
            return


SEARCH_HINT = """You can search the web. If a good answer needs current or changing facts (news, prices,
releases, schedules, weather, versions), niche facts you are unsure of, or the user asks you to look
something up, reply with only this and nothing else:
<search>short query 1 | short query 2</search>
(1 to 3 specific queries of 3 to 8 words.) You will then get the results and answer. Otherwise,
answer directly. Don't search for greetings, opinions, creative writing, maths, coding help, or
questions about attached files or earlier messages."""


def add_search_hint(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Put the fixed search note after the system messages. It is the same text on
    every turn, so it doesn't disturb Ollama's prompt cache."""
    n = 0
    while n < len(messages) and messages[n]["role"] == "system":
        n += 1
    return messages[:n] + [{"role": "system", "content": SEARCH_HINT}] + messages[n:]


_SEARCH_RE = re.compile(r"^\s*(?:`{1,3}\w*\s*)?<search>(.*?)</search>", re.S)


class SearchSniffer:
    """Holds back the start of the answer until it is clear whether the model
    asked for a search (`<search>…</search>`) or is answering."""

    def __init__(self):
        self.buf = ""
        self.mode = "start"     # start | search | answer
        self.queries: List[str] = []

    def feed(self, text: str) -> str:
        """Returns answer text that can be shown now."""
        if self.mode == "answer":
            return text
        self.buf += text
        m = _SEARCH_RE.match(self.buf)
        if m:
            self.mode = "search"
            self.queries = [q.strip(" \"'") for q in re.split(r"[|\n]", m.group(1)) if q.strip(" \"'")][:3]
            return ""
        probe = re.sub(r"^`{1,3}\w*\s*", "", self.buf.lstrip())   # the tag may come inside a code fence
        if probe and not ("<search>".startswith(probe) or probe.startswith("<search>")):
            self.mode = "answer"
            text, self.buf = self.buf, ""
            return text
        if len(self.buf) > 600:            # an unclosed tag: give up and show it
            self.mode = "answer"
            text, self.buf = self.buf, ""
            return text
        return ""

    def flush(self) -> str:
        text, self.buf = ("" if self.mode == "search" else self.buf), ""
        return text


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


def log_timing(final: Dict[str, Any]) -> None:
    """One line per answer showing where the time went (Ollama reports nanoseconds)."""
    def secs(key):
        return final.get(key, 0) / 1e9
    p_n, p_s = final.get("prompt_eval_count", 0), secs("prompt_eval_duration")
    e_n, e_s = final.get("eval_count", 0), secs("eval_duration")
    log.info("timing: total %.1fs | model load %.1fs | read prompt %d tokens in %.1fs (%.0f tok/s) | "
             "wrote %d tokens (thinking + answer) in %.1fs (%.1f tok/s)",
             secs("total_duration"), secs("load_duration"), p_n, p_s, p_n / p_s if p_s else 0,
             e_n, e_s, e_n / e_s if e_s else 0)


def prepare(job: ChatJob) -> ChatJob:
    """Unpack attachments and validate, so bad input fails before a stream starts."""
    if job.prepared is None:
        job.prepared = to_ollama_messages(job.messages)
        if not any(m["role"] == "user" for m in job.prepared):
            raise ValueError("messages must include a user message")
    return job


async def _search_and_attach(http: httpx.AsyncClient, messages: List[Dict[str, Any]],
                             queries: List[str]) -> AsyncIterator[Event]:
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


async def run_chat(job: ChatJob, ollama: Ollama, http: httpx.AsyncClient) -> AsyncIterator[Event]:
    messages = prepare(job).prepared

    options = dict(job.options)
    options.setdefault("num_ctx", Config.NUM_CTX)

    if job.web_search == "on":
        yield Event("status", "Planning the search…")
        queries = await plan_search(ollama, job.model, messages, "on", options["num_ctx"])
        async for ev in _search_and_attach(http, messages, queries):
            yield ev
        yield Event("status", "")

    reserve = options.get("num_predict") if (options.get("num_predict") or 0) > 0 else 4096
    messages = fit_context(messages, options["num_ctx"], reserve)
    model_decides = job.web_search == "auto" and job.format is None
    if model_decides:
        messages = add_search_hint(messages)

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
    if job.tools:
        payload["tools"] = job.tools

    used = {"prompt": 0, "completion": 0}
    final: Dict[str, Any] = {}
    thought = False
    tool_calls: List[Dict[str, Any]] = []
    for attempt in (1, 2):
        splitter = ThinkSplitter()
        sniffer = SearchSniffer() if model_decides and attempt == 1 else None
        stream = ollama.stream_chat(payload)
        try:
            async for chunk in stream:
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    if attempt == 2 and thought is True:
                        thought = "separated"
                        yield Event("thinking", "\n\n— after searching the web —\n\n")
                    thought = thought or True
                    yield Event("thinking", msg["thinking"])
                if msg.get("content"):
                    for kind, text in splitter.feed(msg["content"]):
                        if kind == "content" and sniffer:
                            text = sniffer.feed(text)
                        if text:
                            yield Event(kind, text)
                    if sniffer and sniffer.mode == "search":
                        break                      # stop the answer; closing the stream stops Ollama
                if msg.get("tool_calls"):
                    calls = openai_tool_calls(msg["tool_calls"], len(tool_calls))
                    tool_calls += calls
                    yield Event("tool_calls", calls)
                if chunk.get("done"):
                    final = chunk
        finally:
            await stream.aclose()
        for kind, text in splitter.flush():
            if kind == "content" and sniffer:
                text = sniffer.feed(text) + sniffer.flush()
            if text:
                yield Event(kind, text)
        if final:
            log_timing(final)
            used["prompt"] += final.get("prompt_eval_count", 0)
            used["completion"] += final.get("eval_count", 0)

        if not (sniffer and sniffer.mode == "search" and sniffer.queries):
            break
        # The model asked for a search: run it and ask again with the results.
        log.info("model asked to search: %s", " | ".join(sniffer.queries))
        messages = [dict(m) for m in messages]
        async for ev in _search_and_attach(http, messages, sniffer.queries):
            yield ev
        yield Event("status", "")
        payload["messages"] = messages
        final = {}

    prompt_tokens, completion_tokens = used["prompt"], used["completion"]
    yield Event("done", {
        "finish_reason": "tool_calls" if tool_calls else "length" if final.get("done_reason") == "length" else "stop",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "timings": {
            "total_s": round(final.get("total_duration", 0) / 1e9, 2),
            "load_s": round(final.get("load_duration", 0) / 1e9, 2),
            "tokens_per_s": round(final.get("eval_count", 0) / (final["eval_duration"] / 1e9), 1)
            if final.get("eval_duration") else None,
        },
    })
