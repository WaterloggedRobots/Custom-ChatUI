"""End-to-end tests of the chat server against a fake Ollama and a fake web.

Run from Server/ollama:  python -m pytest -q tests
"""

import base64
import io
import json
import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import search as web  # noqa: E402
from chat_server.app import create_app  # noqa: E402
from chat_server.pipeline import ThinkSplitter, fit_context  # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class FakeOllama:
    def __init__(self):
        self.requests = []
        self.plan = {"search": False, "queries": []}
        self.inline_think = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:31b", "size": 1}, {"name": "plain:7b", "size": 2}]})
        if path == "/api/show":
            name = json.loads(request.content)["model"]
            caps = ["completion", "vision", "thinking"] if name.startswith("gemma") else ["completion"]
            return httpx.Response(200, json={"capabilities": caps, "model_info": {"gemma3.context_length": 131072}})
        if path == "/api/chat":
            body = json.loads(request.content)
            self.requests.append(body)
            if "format" in body and isinstance(body["format"], dict):
                return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(self.plan)}, "done": True})
            if body.get("think") and body["model"].startswith("plain"):
                return httpx.Response(400, json={"error": f"\"{body['model']}\" does not support thinking"})
            lines = []
            if body.get("think"):
                lines += [{"message": {"role": "assistant", "content": "", "thinking": "Let me think. "}, "done": False},
                          {"message": {"role": "assistant", "content": "", "thinking": "Done."}, "done": False}]
            if self.inline_think:
                lines += [{"message": {"content": "<thi"}, "done": False},
                          {"message": {"content": "nk>hidden</th"}, "done": False},
                          {"message": {"content": "ink>\nVisible"}, "done": False}]
            else:
                lines += [{"message": {"role": "assistant", "content": "Hello "}, "done": False},
                          {"message": {"role": "assistant", "content": "world [1]"}, "done": False}]
            lines.append({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                          "prompt_eval_count": 42, "eval_count": 7, "eval_duration": 1_000_000_000,
                          "total_duration": 2_000_000_000})
            if not body.get("stream", True):
                text = "".join(ln["message"].get("content", "") for ln in lines)
                return httpx.Response(200, json={**lines[-1], "message": {"role": "assistant", "content": text}})
            return httpx.Response(200, content="\n".join(json.dumps(ln) for ln in lines).encode())
        return httpx.Response(404, json={"error": "not found"})


def fake_web(request: httpx.Request) -> httpx.Response:
    if "fail" in request.url.host:
        return httpx.Response(500)
    html = f"<html><head><title>Page {request.url.host}</title></head><body><nav>menu</nav>" \
           f"<article><p>Fact from {request.url.host}.</p></article><script>x()</script></body></html>"
    return httpx.Response(200, headers={"content-type": "text/html"}, text=html)


@pytest.fixture
def env(monkeypatch):
    fake = FakeOllama()
    searched = []

    async def fake_ddg(query, n):
        searched.append(query)
        return [web.Hit(f"Result {i} for {query}", f"https://site{i}.example/{query.replace(' ', '-')}", f"snippet {i}")
                for i in range(2)] + [web.Hit("Broken", "https://fail.example/", "snippet broken")]

    monkeypatch.setattr(web, "duckduckgo", fake_ddg)
    app = create_app(ollama_transport=httpx.MockTransport(fake), web_transport=httpx.MockTransport(fake_web))
    with TestClient(app) as client:
        yield client, fake, searched


def sse(resp):
    events = []
    for line in resp.iter_lines():
        if line.startswith("data: "):
            data = line[6:]
            events.append(data if data == "[DONE]" else json.loads(data))
    return events


def test_models_and_health(env):
    client, _, _ = env
    r = client.get("/v1/models", params={"detailed": True}).json()
    ids = {m["id"]: m for m in r["data"]}
    assert set(ids) == {"gemma4:31b", "plain:7b"}
    assert "vision" in ids["gemma4:31b"]["capabilities"]
    assert ids["gemma4:31b"]["context_length"] == 131072
    assert client.get("/health").json()["ollama"] is True


def test_stream_separates_thinking_and_reports_usage(env):
    client, fake, _ = env
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "gemma4:31b", "stream": True, "think": True, "temperature": 0.2, "max_tokens": 100,
        "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}]}) as r:
        events = sse(r)
    assert events[-1] == "[DONE]"
    deltas = [e["choices"][0]["delta"] for e in events[:-1]]
    assert "".join(d.get("reasoning_content", "") for d in deltas) == "Let me think. Done."
    assert "".join(d.get("content", "") for d in deltas) == "Hello world [1]"
    final = events[-2]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["usage"] == {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49}
    sent = fake.requests[-1]
    assert sent["think"] is True
    assert sent["options"]["temperature"] == 0.2 and sent["options"]["num_predict"] == 100
    assert sent["options"]["num_ctx"] >= 8192
    assert not any("format" in r for r in fake.requests), "web search off must not call the planner"


def test_model_without_thinking_is_retried(env):
    client, fake, _ = env
    r = client.post("/v1/chat/completions", json={"model": "plain:7b", "think": True,
                                                    "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "Hello world [1]"
    assert "think" not in fake.requests[-1]
    # Second call skips the failing attempt.
    n = len(fake.requests)
    client.post("/v1/chat/completions", json={"model": "plain:7b", "think": True, "messages": [{"role": "user", "content": "x"}]})
    assert len(fake.requests) == n + 1


def test_inline_think_tags_are_split_out(env):
    client, fake, _ = env
    fake.inline_think = True
    body = client.post("/v1/chat/completions", json={"model": "plain:7b",
                                                       "messages": [{"role": "user", "content": "hi"}]}).json()
    msg = body["choices"][0]["message"]
    assert msg["content"] == "Visible"
    assert msg["reasoning_content"] == "hidden"


def test_auto_search_runs_planner_and_cites_sources(env):
    client, fake, searched = env
    fake.plan = {"search": True, "queries": ["hsi close today", "hang seng index"]}
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "gemma4:31b", "stream": True, "web_search": "auto",
        "messages": [{"role": "user", "content": "Where did the Hang Seng close today?"}]}) as r:
        events = sse(r)
    statuses = [e["status"] for e in events if isinstance(e, dict) and "status" in e]
    assert any(s.startswith("Searching: hsi close today") for s in statuses)
    sources = next(e["sources"] for e in events if isinstance(e, dict) and "sources" in e)
    assert [s["n"] for s in sources] == [1, 2, 3, 4, 5]
    assert sorted(searched) == ["hang seng index", "hsi close today"]
    planner, answer = fake.requests[-2], fake.requests[-1]
    assert planner["think"] is False and planner["format"]["required"] == ["search", "queries"]
    assert planner["options"]["num_ctx"] == answer["options"]["num_ctx"]
    user = answer["messages"][-1]["content"]
    assert "<web_results" in user and user.rstrip().endswith("Where did the Hang Seng close today?")
    assert "Fact from site0.example." in user          # page text was read
    assert "menu" not in user                          # navigation was dropped
    assert "snippet broken" in user                    # a failed page keeps its snippet


def test_auto_search_skips_when_planner_says_no(env):
    client, fake, searched = env
    fake.plan = {"search": False, "queries": []}
    client.post("/v1/chat/completions", json={"model": "gemma4:31b", "web_search": "auto",
                                               "messages": [{"role": "user", "content": "Write a haiku"}]})
    assert searched == []
    assert "<web_results" not in fake.requests[-1]["messages"][-1]["content"]


def test_search_on_falls_back_to_the_question(env):
    client, fake, searched = env
    fake.plan = {"search": False, "queries": []}
    client.post("/v1/chat/completions", json={"model": "gemma4:31b", "web_search": "on",
                                               "messages": [{"role": "user", "content": "ollama latest version"}]})
    assert searched == ["ollama latest version"]


def test_images_and_files_reach_the_model(env):
    client, fake, _ = env
    png = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
    txt = "data:text/plain;base64," + base64.b64encode(b"quarterly revenue: 42").decode()
    import docx
    buf = io.BytesIO()
    d = docx.Document()
    d.add_paragraph("Board minutes")
    d.save(buf)
    docx_url = "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64," + \
        base64.b64encode(buf.getvalue()).decode()
    r = client.post("/v1/chat/completions", json={"model": "gemma4:31b", "messages": [
        {"role": "user", "content": "old", "images": []},
        {"role": "assistant", "content": "<think>old reasoning</think>Old answer"},
        {"role": "user", "content": [
            {"type": "text", "text": "What is in these?"},
            {"type": "image_url", "image_url": {"url": png}},
            {"type": "file", "file": {"filename": "notes.txt", "file_data": txt}},
            {"type": "file", "file": {"filename": "minutes.docx", "file_data": docx_url}},
        ]}]})
    assert r.status_code == 200, r.text
    msgs = fake.requests[-1]["messages"]
    assert msgs[1]["content"] == "Old answer"
    last = msgs[-1]
    assert last["images"] == [base64.b64encode(PNG_1PX).decode()]
    assert '<file name="notes.txt">\nquarterly revenue: 42\n</file>' in last["content"]
    assert "Board minutes" in last["content"]
    assert last["content"].endswith("What is in these?")


def test_bad_attachment_is_a_400(env):
    client, _, _ = env
    r = client.post("/v1/chat/completions", json={"model": "gemma4:31b", "messages": [{"role": "user", "content": [
        {"type": "file", "file": {"filename": "x.bin", "file_data": "data:application/octet-stream;base64," +
                                   base64.b64encode(bytes(range(256))).decode()}}]}]})
    assert r.status_code == 400
    assert "not supported" in r.json()["error"]["message"]


def test_response_format_maps_to_ollama_format(env):
    client, fake, _ = env
    schema = {"type": "object", "properties": {"signal": {"type": "string"}}}
    client.post("/v1/chat/completions", json={"model": "gemma4:31b",
                                               "response_format": {"type": "json_schema", "json_schema": {"schema": schema}},
                                               "messages": [{"role": "user", "content": "x"}]})
    assert fake.requests[-1]["format"] == schema


def test_web_search_and_browse_endpoints(env):
    client, _, _ = env
    r = client.post("/web_search", json={"query": "futu opend", "num_results": 3, "read_pages": 1}).json()
    assert r["source"] == "duckduckgo" and len(r["results"]) == 3
    assert r["results"][0]["text"].startswith("Fact from site0.example")
    page = client.post("/v1/web/browse", json={"url": "https://news.example/a"}).json()
    assert page["title"] == "Page news.example" and "Fact from news.example." in page["text"]
    assert client.post("/v1/web/browse", json={"url": "file:///etc/passwd"}).status_code == 502


def test_extract_endpoint(env):
    client, _, _ = env
    r = client.post("/v1/files/extract", files={"file": ("a.md", b"# Title\n\nbody", "text/markdown")}).json()
    assert r["kind"] == "text" and r["text"] == "# Title\n\nbody"


def test_think_splitter_handles_split_tags():
    s = ThinkSplitter()
    out = []
    for piece in ["  <th", "ink>abc", "</thi", "nk>  Answer", " more"]:
        out += s.feed(piece)
    out += s.flush()
    thinking = "".join(t for k, t in out if k == "thinking")
    content = "".join(t for k, t in out if k == "content")
    assert thinking == "abc" and content == "Answer more"


def test_fit_context_keeps_system_and_newest():
    msgs = [{"role": "system", "content": "S"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3000} for i in range(10)]
    kept = fit_context(msgs, num_ctx=4096, reserve=1024)
    assert kept[0]["role"] == "system"
    assert kept[1]["role"] == "user"
    assert kept[-1] is msgs[-1]
    assert len(kept) < len(msgs)
