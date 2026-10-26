"""Image generation through ComfyUI, which runs on this same machine.

A workflow is a ComfyUI graph exported in API format (in ComfyUI: Workflow >
Export (API)) and saved as a .json in COMFYUI_WORKFLOW_DIR (Server/ollama/
workflows by default). Its file name without .json is the id a chat picks in
Chat Settings.

The prompts go into the workflow like this:
- If any text in it holds {{positive}} or {{negative}}, those are replaced
  (so "masterpiece, {{positive}}" keeps a fixed style prefix).
- Otherwise the sampler's positive and negative inputs are followed back to
  their text encoders (CLIPTextEncode and the like) and their text is set.
Every seed / noise_seed number is randomised, so each picture is new.

ComfyUI's API is plain HTTP: POST /prompt queues the graph, GET /history/<id>
says when it is done and where the pictures are, GET /view returns them.
"""

import asyncio
import base64
import copy
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .config import Config

log = logging.getLogger(__name__)
router = APIRouter()

PLACEHOLDER = re.compile(r"\{\{\s*(positive|negative)\s*\}\}", re.I)
TEXT_KEYS = ("text", "text_g", "text_l", "prompt", "positive_prompt", "clip_l", "t5xxl")
FOLLOW_KEYS = ("conditioning", "guider", "conditioning_to", "conditioning_1", "base_conditioning")
SEED_KEYS = ("seed", "noise_seed")


class ComfyError(Exception):
    pass


# --------------------------------------------------------------------------
# Workflows
# --------------------------------------------------------------------------

def workflow_dir() -> Path:
    return Path(Config.COMFYUI_WORKFLOW_DIR)


def _is_api_format(data: Any) -> bool:
    return isinstance(data, dict) and bool(data) and all(
        isinstance(n, dict) and "class_type" in n for n in data.values())


def list_workflows() -> List[Dict[str, Any]]:
    """Every .json in the workflow folder: {id, name, usable, error}."""
    out = []
    folder = workflow_dir()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        entry = {"id": path.stem, "name": path.stem.replace("_", " "), "usable": True, "error": ""}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            entry.update(usable=False, error=f"not valid JSON: {e}")
        else:
            if not _is_api_format(data):
                entry.update(usable=False, error="saved in the editor's format; in ComfyUI use "
                                                 "Workflow > Export (API) and save that instead")
            else:
                try:
                    fill(data, "test", "test")
                except ComfyError as e:
                    entry.update(usable=False, error=str(e))
        out.append(entry)
    return out


def load_workflow(workflow_id: str) -> Dict[str, Any]:
    if not workflow_id or not re.fullmatch(r"[\w .()+-]{1,120}", workflow_id) or workflow_id.startswith("."):
        raise ComfyError(f"no workflow called {workflow_id!r}")
    path = workflow_dir() / f"{workflow_id}.json"
    if not path.is_file():
        raise ComfyError(f"no workflow called {workflow_id!r} in {workflow_dir()}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ComfyError(f"can't read {path.name}: {e}")
    if not _is_api_format(data):
        raise ComfyError(f"{path.name} is in the editor's format; export it with Workflow > Export (API)")
    return data


def _link(value) -> Optional[str]:
    """A node input that points at another node is [node_id, output_index]."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
        return str(value[0])
    return None


def _text_node(wf: Dict[str, Any], node_id: Optional[str], role: str, seen=None) -> Optional[Tuple[str, str]]:
    """Follow conditioning links back from `node_id` to a node with a text input: (node id, input key)."""
    seen = seen if seen is not None else set()
    if node_id is None or node_id in seen or node_id not in wf:
        return None
    seen.add(node_id)
    inputs = wf[node_id].get("inputs") or {}
    for key in TEXT_KEYS:
        if isinstance(inputs.get(key), str):
            return node_id, key
    for key in (role, *FOLLOW_KEYS):
        found = _text_node(wf, _link(inputs.get(key)), role, seen)
        if found:
            return found
    return None


def _samplers(wf: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [n.get("inputs") or {} for n in wf.values()
            if _link((n.get("inputs") or {}).get("positive")) or _link((n.get("inputs") or {}).get("guider"))]


def fill(workflow: Dict[str, Any], positive: str, negative: str, seed: Optional[int] = None) -> Dict[str, Any]:
    """A copy of `workflow` with the prompts put in and fresh seeds."""
    wf = copy.deepcopy(workflow)
    rng = random.Random(seed)
    values = {"positive": positive, "negative": negative}

    placed = False
    for node in wf.values():
        inputs = node.get("inputs") or {}
        for key, value in inputs.items():
            if isinstance(value, str) and PLACEHOLDER.search(value):
                inputs[key] = PLACEHOLDER.sub(lambda m: values[m.group(1).lower()], value)
                placed = True
    if not placed:
        pos = neg = None
        for inputs in _samplers(wf):
            pos = pos or _text_node(wf, _link(inputs.get("positive")) or _link(inputs.get("guider")), "positive")
            neg = neg or _text_node(wf, _link(inputs.get("negative")), "negative")
        if pos is None:
            raise ComfyError("can't tell where the prompt goes; put {{positive}} (and {{negative}}) "
                             "in the prompt boxes before exporting")
        wf[pos[0]]["inputs"][pos[1]] = positive
        if neg and neg != pos:
            wf[neg[0]]["inputs"][neg[1]] = negative

    for node in wf.values():
        inputs = node.get("inputs") or {}
        for key in SEED_KEYS:
            if isinstance(inputs.get(key), int) and not isinstance(inputs.get(key), bool):
                inputs[key] = rng.randrange(2 ** 50)
    return wf


# --------------------------------------------------------------------------
# Talking to ComfyUI
# --------------------------------------------------------------------------

def make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=Config.COMFYUI_URL, transport=transport, timeout=30)


def _comfy_error(body: Any) -> str:
    """ComfyUI's validation errors, shortened to something readable."""
    if not isinstance(body, dict):
        return str(body)[:500]
    err = body.get("error")
    parts = [err.get("message", "") if isinstance(err, dict) else str(err or "")]
    for node_id, info in (body.get("node_errors") or {}).items():
        for e in info.get("errors") or []:
            parts.append(f"node {node_id} ({info.get('class_type', '?')}): {e.get('message', '')} {e.get('details', '')}".strip())
    return "; ".join(p for p in parts if p)[:800]


async def _get_json(comfy: httpx.AsyncClient, path: str) -> Any:
    r = await comfy.get(path)
    r.raise_for_status()
    return r.json()


async def generate(comfy: httpx.AsyncClient, workflow_id: str, positive: str, negative: str = "",
                   seed: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run a workflow and return its pictures: [{"filename", "mime", "data" (bytes)}]."""
    wf = fill(load_workflow(workflow_id), positive, negative, seed)
    try:
        r = await comfy.post("/prompt", json={"prompt": wf, "client_id": f"chatui-{uuid.uuid4().hex[:8]}"})
    except httpx.TransportError as e:
        raise ComfyError(f"ComfyUI isn't reachable at {Config.COMFYUI_URL} ({e or type(e).__name__}). Is it running?")
    if r.status_code != 200:
        try:
            body = r.json()
        except ValueError:
            body = r.text
        raise ComfyError(f"ComfyUI refused the workflow: {_comfy_error(body)}")
    prompt_id = r.json().get("prompt_id")
    if not prompt_id:
        raise ComfyError(f"ComfyUI didn't queue the workflow: {_comfy_error(r.json())}")

    started = time.monotonic()
    while True:
        history = await _get_json(comfy, f"/history/{prompt_id}")
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m[1].get("exception_message", "") for m in status.get("messages") or []
                        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error"]
                raise ComfyError("ComfyUI failed: " + ("; ".join(msgs) or "see the ComfyUI console"))
            if status.get("completed", True) or entry.get("outputs"):
                break
        if time.monotonic() - started > Config.COMFYUI_TIMEOUT:
            raise ComfyError(f"ComfyUI took longer than {Config.COMFYUI_TIMEOUT:.0f}s; the picture may still "
                             "appear in its output folder")
        await asyncio.sleep(Config.COMFYUI_POLL)

    files = []
    for node_out in (entry.get("outputs") or {}).values():
        for img in node_out.get("images") or []:
            files.append(img)
    saved = [f for f in files if f.get("type") == "output"]
    images = []
    for f in saved or files:                      # previews only if nothing was saved
        r = await comfy.get("/view", params={"filename": f.get("filename", ""), "subfolder": f.get("subfolder", ""),
                                             "type": f.get("type", "output")})
        r.raise_for_status()
        mime = r.headers.get("content-type", "image/png").split(";")[0]
        images.append({"filename": f.get("filename", "image.png"), "mime": mime, "data": r.content})
    if not images:
        raise ComfyError("the workflow finished without a picture; does it end in a Save Image node?")
    log.info("timing: ComfyUI %s %.1fs, %d image(s)", workflow_id, time.monotonic() - started, len(images))
    if Config.COMFYUI_FREE_AFTER:
        try:
            await comfy.post("/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError as e:
            log.info("ComfyUI /free failed: %s", e)
    return images


def as_json(images: List[Dict[str, Any]], positive: str, negative: str, workflow_id: str) -> List[Dict[str, Any]]:
    return [{"b64_json": base64.b64encode(i["data"]).decode("ascii"), "mime": i["mime"], "filename": i["filename"],
             "prompt": positive, "negative_prompt": negative, "workflow": workflow_id} for i in images]


# --------------------------------------------------------------------------
# HTTP endpoints
# --------------------------------------------------------------------------

@router.get("/v1/images/workflows")
async def workflows(request: Request):
    """The workflows a chat can pick, and whether ComfyUI answers."""
    try:
        await _get_json(request.app.state.comfy, "/system_stats")
        online, error = True, ""
    except (httpx.HTTPError, ValueError) as e:
        online, error = False, f"ComfyUI isn't reachable at {Config.COMFYUI_URL}: {e or type(e).__name__}"
    return {"data": list_workflows(), "folder": str(workflow_dir()), "comfyui": online, "error": error}


class ImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    negative_prompt: str = ""
    workflow: str = Field(..., min_length=1)
    seed: Optional[int] = None


@router.post("/v1/images/generations")
async def generations(body: ImageRequest, request: Request):
    """OpenAI-style: {"data": [{"b64_json", "mime", ...}]}."""
    try:
        images = await generate(request.app.state.comfy, body.workflow, body.prompt, body.negative_prompt, body.seed)
    except ComfyError as e:
        raise HTTPException(502, str(e))
    return {"created": int(time.time()), "data": as_json(images, body.prompt, body.negative_prompt, body.workflow)}
