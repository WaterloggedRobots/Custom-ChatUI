"""Pictures through ComfyUI (chat_server/comfy.py), against a fake ComfyUI and a fake Ollama.

Run from Server/ollama:  python -m pytest -q tests
"""

import base64
import json
import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_server import comfy  # noqa: E402
from chat_server.app import create_app  # noqa: E402
from chat_server.config import Config  # noqa: E402
from chat_server.pipeline import ImageSniffer, parse_image_request  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

DEFAULT_GRAPH = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 5, "steps": 20, "model": ["4", 0], "positive": ["6", 0],
                                                "negative": ["7", 0], "latent_image": ["5", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "old positive", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "old negative", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ChatUI", "images": ["8", 0]}},
}

FLUX_GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["9", 0]}},
    "2": {"class_type": "FluxGuidance", "inputs": {"guidance": 3.5, "conditioning": ["1", 0]}},
    "3": {"class_type": "BasicGuider", "inputs": {"model": ["8", 0], "conditioning": ["2", 0]}},
    "4": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
    "5": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["4", 0], "guider": ["3", 0]}},
    "8": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux.safetensors"}},
    "9": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": "a", "clip_name2": "b"}},
}


class FakeComfy:
    def __init__(self):
        self.prompts = []
        self.polls = 0
        self.fail = None          # "refuse" | "error"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3"}})
        if path == "/prompt":
            body = json.loads(request.content)
            if self.fail == "refuse":
                return httpx.Response(400, json={"error": {"message": "Prompt outputs failed validation"},
                                                 "node_errors": {"4": {"class_type": "CheckpointLoaderSimple",
                                                                       "errors": [{"message": "Value not in list",
                                                                                   "details": "ckpt_name"}]}}})
            self.prompts.append(body["prompt"])
            return httpx.Response(200, json={"prompt_id": f"p{len(self.prompts)}", "number": 1, "node_errors": {}})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            self.polls += 1
            if self.polls % 2:                       # first poll: still running
                return httpx.Response(200, json={})
            if self.fail == "error":
                return httpx.Response(200, json={pid: {"outputs": {}, "status": {
                    "status_str": "error", "completed": False,
                    "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}}})
            return httpx.Response(200, json={pid: {"outputs": {
                "9": {"images": [{"filename": "ChatUI_00001_.png", "subfolder": "", "type": "output"}]},
                "10": {"images": [{"filename": "preview.png", "subfolder": "", "type": "temp"}]}},
                "status": {"status_str": "success", "completed": True}}})
        if path == "/view":
            assert request.url.params["type"] == "output"
            return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)
        return httpx.Response(404)


class FakeOllama:
    def __init__(self):
        self.requests = []
        self.reply = ["Here is the café. <ima", "ge>\npositive: a cosy café at night, warm light\n",
                      "negative: blurry</image>\nEnjoy!"]
        self.unloaded = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        body = json.loads(request.content)
        if not body.get("messages"):
            self.unloaded.append(body["model"])
            return httpx.Response(200, json={"done": True})
        self.requests.append(body)
        lines = [{"message": {"content": c}, "done": False} for c in self.reply]
        lines.append({"message": {"content": ""}, "done": True, "prompt_eval_count": 3, "eval_count": 4})
        return httpx.Response(200, content="\n".join(json.dumps(ln) for ln in lines).encode())


@pytest.fixture
def workflows(tmp_path, monkeypatch):
    (tmp_path / "default.json").write_text(json.dumps(DEFAULT_GRAPH))
    (tmp_path / "flux.json").write_text(json.dumps(FLUX_GRAPH))
    (tmp_path / "editor format.json").write_text(json.dumps({"nodes": [], "links": []}))
    monkeypatch.setattr(Config, "COMFYUI_WORKFLOW_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "COMFYUI_POLL", 0)
    return tmp_path


@pytest.fixture
def env(workflows):
    fake_ollama, fake_comfy = FakeOllama(), FakeComfy()
    app = create_app(ollama_transport=httpx.MockTransport(fake_ollama),
                     comfy_transport=httpx.MockTransport(fake_comfy))
    with TestClient(app) as client:
        yield client, fake_ollama, fake_comfy


def sse(resp):
    return [json.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ") and ln != "data: [DONE]"]


# ---------------------------------------------------------------- workflows

def test_fill_finds_the_text_boxes_and_new_seeds():
    wf = comfy.fill(DEFAULT_GRAPH, "a red fox", "blurry", seed=1)
    assert wf["6"]["inputs"]["text"] == "a red fox"
    assert wf["7"]["inputs"]["text"] == "blurry"
    assert wf["3"]["inputs"]["seed"] != 5
    assert DEFAULT_GRAPH["6"]["inputs"]["text"] == "old positive"     # the original is left alone


def test_fill_follows_flux_guider_and_has_no_negative():
    wf = comfy.fill(FLUX_GRAPH, "a red fox", "blurry", seed=1)
    assert wf["1"]["inputs"]["text"] == "a red fox"
    assert wf["4"]["inputs"]["noise_seed"] != 1


def test_fill_prefers_placeholders():
    graph = json.loads(json.dumps(DEFAULT_GRAPH))
    graph["6"]["inputs"]["text"] = "masterpiece, {{ positive }}"
    graph["7"]["inputs"]["text"] = "lowres, {{negative}}"
    wf = comfy.fill(graph, "a red fox", "blurry")
    assert wf["6"]["inputs"]["text"] == "masterpiece, a red fox"
    assert wf["7"]["inputs"]["text"] == "lowres, blurry"


def test_fill_without_a_sampler_says_where_to_put_the_prompt():
    with pytest.raises(comfy.ComfyError, match="positive"):
        comfy.fill({"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}, "x", "y")


def test_workflow_list_marks_the_editor_format(env):
    client, _, _ = env
    body = client.get("/v1/images/workflows").json()
    assert body["comfyui"] is True
    by_id = {w["id"]: w for w in body["data"]}
    assert by_id["default"]["usable"] and by_id["flux"]["usable"]
    assert not by_id["editor format"]["usable"] and "Export (API)" in by_id["editor format"]["error"]


def test_workflow_names_cannot_leave_the_folder(workflows):
    for bad in ("../secret", "", ".hidden", "a/b"):
        with pytest.raises(comfy.ComfyError):
            comfy.load_workflow(bad)


# ------------------------------------------------------------ the parsing

def test_sniffer_takes_the_block_out_wherever_it_is():
    s = ImageSniffer()
    out = "".join(s.feed(c) for c in ["A <", "b>bold</b> start <im", "age>a fox</image>", " end"]) + s.flush()
    assert out == "A <b>bold</b> start  end"
    assert s.requests == [("a fox", "")]


def test_sniffer_keeps_an_unfinished_block_at_the_end():
    s = ImageSniffer()
    out = s.feed("Look: <image>positive: a fox") + s.flush()
    assert out == "Look: "
    assert s.requests == [("a fox", "")]


def test_parse_labels():
    assert parse_image_request("**Positive:** a fox,\n in snow\nNegative prompt: blurry") == ("a fox, in snow", "blurry")
    assert parse_image_request("just a fox") == ("just a fox", "")


# ------------------------------------------------------------------- chat

def test_chat_draws_what_the_model_asks_for(env):
    client, fake_ollama, fake_comfy = env
    with client.stream("POST", "/v1/chat/completions", json={
        "model": "m", "stream": True, "image_workflow": "default",
        "messages": [{"role": "system", "content": "You are Mia."}, {"role": "user", "content": "Show me the café"}]}) as r:
        events = sse(r)
    content = "".join(e["choices"][0]["delta"].get("content", "") for e in events if e.get("choices"))
    assert content == "Here is the café. Enjoy!"
    statuses = [e["status"] for e in events if "status" in e]
    assert any(s.startswith("Drawing: a cosy café") for s in statuses)
    images = [i for e in events if "images" in e for i in e["images"]]
    assert len(images) == 1 and base64.b64decode(images[0]["b64_json"]) == PNG
    assert images[0]["prompt"] == "a cosy café at night, warm light" and images[0]["negative_prompt"] == "blurry"
    queued = fake_comfy.prompts[0]
    assert queued["6"]["inputs"]["text"] == "a cosy café at night, warm light"
    sent = fake_ollama.requests[0]["messages"]
    assert sent[0]["content"] == "You are Mia." and "<image>" in sent[1]["content"]
    assert events[-1]["choices"][0]["finish_reason"] == "stop"


def test_chat_without_a_workflow_shows_the_block_as_text(env):
    client, fake_ollama, fake_comfy = env
    body = client.post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "hi"}]}).json()
    assert "<image>" in body["choices"][0]["message"]["content"]
    assert "images" not in body and not fake_comfy.prompts
    assert not any("<image>" in m["content"] for m in fake_ollama.requests[0]["messages"] if m["role"] == "system")


def test_chat_reports_a_comfyui_failure_and_still_answers(env):
    client, _, fake_comfy = env
    fake_comfy.fail = "error"
    body = client.post("/v1/chat/completions", json={"model": "m", "image_workflow": "default",
                                                       "messages": [{"role": "user", "content": "draw"}]}).json()
    assert body["choices"][0]["message"]["content"] == "Here is the café. Enjoy!"
    assert "CUDA out of memory" in body["images"][0]["error"]


def test_unload_ollama_before_drawing(env, monkeypatch):
    client, fake_ollama, _ = env
    monkeypatch.setattr(Config, "COMFYUI_UNLOAD_OLLAMA", True)
    client.post("/v1/chat/completions", json={"model": "m", "image_workflow": "default",
                                              "messages": [{"role": "user", "content": "draw"}]})
    assert fake_ollama.unloaded == ["m"]


def test_bad_image_mode_is_refused(env):
    client, _, _ = env
    r = client.post("/v1/chat/completions", json={"model": "m", "image_workflow": "default", "image_mode": "always",
                                                   "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400


# --------------------------------------------------------------- endpoint

def test_generations_endpoint(env):
    client, _, fake_comfy = env
    r = client.post("/v1/images/generations", json={"prompt": "a fox", "workflow": "flux"})
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["data"][0]["b64_json"]) == PNG
    assert fake_comfy.prompts[0]["1"]["inputs"]["text"] == "a fox"


def test_generations_explains_a_refused_workflow(env):
    client, _, fake_comfy = env
    fake_comfy.fail = "refuse"
    r = client.post("/v1/images/generations", json={"prompt": "a fox", "workflow": "default"})
    assert r.status_code == 502
    assert "ckpt_name" in r.json()["error"]["message"]


def test_generations_when_comfyui_is_down(workflows):
    def down(request):
        raise httpx.ConnectError("refused")
    app = create_app(ollama_transport=httpx.MockTransport(FakeOllama()), comfy_transport=httpx.MockTransport(down))
    with TestClient(app) as client:
        assert client.get("/v1/images/workflows").json()["comfyui"] is False
        r = client.post("/v1/images/generations", json={"prompt": "a fox", "workflow": "default"})
        assert r.status_code == 502 and "Is it running?" in r.json()["error"]["message"]
