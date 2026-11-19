"""Thin async client for Ollama's native /api/chat.

The native API is used instead of Ollama's /v1 shim because it carries the
separate `thinking` stream, `images` on messages, structured `format` output
and real token counts.
"""

import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import Config

log = logging.getLogger(__name__)


class OllamaError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _tagged(model: str) -> str:
    return model if ":" in model else model + ":latest"


def context_overrides(spec: str) -> Dict[str, int]:
    """NUM_CTX_MODELS ("name=4096, other:tag=8192") as {name:tag: size}."""
    out = {}
    for part in re.split(r"[,;\s]+", spec or ""):
        name, _, size = part.partition("=")
        if name and size.strip().isdigit():
            out[_tagged(name.strip())] = int(size)
        elif part:
            log.warning("NUM_CTX_MODELS: ignoring %r (want name=size)", part)
    return out


def _no_think_support(err: "OllamaError") -> bool:
    return err.status == 400 and "think" in err.detail.lower()


def _no_tool_support(err: "OllamaError") -> bool:
    return err.status == 400 and "tools" in err.detail.lower()


def _out_of_memory(err: "OllamaError") -> bool:
    return "out of memory" in err.detail.lower()


# Ollama only puts layers in system RAM when its estimate says the model won't
# fit. Parts it doesn't measure (gemma4's draft model for speculative decoding,
# the vision projector) can still run the GPU out at run time, and that's a
# crash, not a fallback. So after one, the model gets this many fewer layers on
# the GPU each time, and the rest run from system RAM.
OOM_LAYER_STEP = 6
OOM_RETRIES = 3


class Ollama:
    def __init__(self, base_url: str = Config.OLLAMA_URL, transport: Optional[httpx.AsyncBaseTransport] = None):
        # One pooled client for the whole process; streaming replies can take
        # minutes on big models, so there is no read timeout.
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(connect=10, read=None, write=60, pool=None),
            transport=transport,
        )
        # Models that rejected `think`, so later requests skip the retry.
        self._no_think: set = set()
        # model -> (num_ctx from its Modelfile or None, when it was read)
        self._model_ctx: Dict[str, tuple] = {}
        # model -> layers it may put on the GPU, once it ran out of GPU memory.
        # Kept for the life of the server, as a new value makes Ollama reload it.
        self._gpu_layers: Dict[str, int] = {}

    async def close(self):
        await self.http.aclose()

    def _prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("model") in self._no_think:
            payload = {k: v for k, v in payload.items() if k != "think"}
        return self._with_gpu_layers(payload)

    def _with_gpu_layers(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        layers = self._gpu_layers.get(payload.get("model"))
        if layers is None or "num_gpu" in (payload.get("options") or {}):
            return payload
        return {**payload, "options": {**(payload.get("options") or {}), "num_gpu": layers}}

    async def _layer_count(self, model: str) -> int:
        """The layers Ollama can put on the GPU: the model's blocks and its output layer."""
        try:
            info = (await self.show(model)).get("model_info") or {}
            blocks = next((v for k, v in info.items() if k.endswith(".block_count")), None)
            return int(blocks) + 1 if blocks else 0
        except Exception as e:
            log.debug("couldn't read %s's layer count: %s", model, e)
            return 0

    async def _fewer_gpu_layers(self, model: str) -> bool:
        """After the GPU ran out of memory: move more of the model to system RAM. False when none are left."""
        now = self._gpu_layers.get(model)
        if now is None:
            now = await self._layer_count(model)
            if not now:
                return False
        if now <= 0:
            return False
        self._gpu_layers[model] = max(now - OOM_LAYER_STEP, 0)
        log.warning("%s ran out of GPU memory; retrying with %d of its layers on the GPU, the rest in system RAM",
                    model, self._gpu_layers[model])
        return True

    async def _raise_for(self, resp: httpx.Response):
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", "replace")
            try:
                body = json.loads(body).get("error", body)
            except ValueError:
                pass
            raise OllamaError(resp.status_code, body)

    async def stream_chat(self, payload: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Yield Ollama's streamed chunks. Retries without `think` when the model
        doesn't support reasoning, and without `tools` when it can't call tools."""
        body = self._prepare({**payload, "stream": True})
        sent = False
        for _ in range(3 + OOM_RETRIES):
            try:
                async for chunk in self._stream(body):
                    sent = True
                    yield chunk
                return
            except OllamaError as e:
                if _out_of_memory(e) and "num_gpu" not in (payload.get("options") or {}):
                    # The next request gets fewer layers either way; this one only if nothing reached you yet.
                    if not await self._fewer_gpu_layers(body["model"]) or sent:
                        raise
                    body = self._with_gpu_layers({**body, "options": {
                        k: v for k, v in (body.get("options") or {}).items() if k != "num_gpu"}})
                elif "think" in body and _no_think_support(e):
                    log.info("%s has no thinking support; retrying without it", body["model"])
                    self._no_think.add(body["model"])
                    body.pop("think")
                elif "tools" in body and _no_tool_support(e):
                    log.info("%s can't call tools; retrying without them", body["model"])
                    body.pop("tools")
                else:
                    raise

    async def _stream(self, body):
        async with self.http.stream("POST", "/api/chat", json=body) as resp:
            await self._raise_for(resp)
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise OllamaError(500, chunk["error"])
                yield chunk

    async def chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """One non-streamed reply."""
        body = self._prepare({**payload, "stream": False})
        for _ in range(2 + OOM_RETRIES):
            resp = await self.http.post("/api/chat", json=body)
            try:
                await self._raise_for(resp)
                return resp.json()
            except OllamaError as e:
                if (_out_of_memory(e) and "num_gpu" not in (payload.get("options") or {})
                        and await self._fewer_gpu_layers(body["model"])):
                    body = self._with_gpu_layers({**body, "options": {
                        k: v for k, v in (body.get("options") or {}).items() if k != "num_gpu"}})
                elif "think" in body and _no_think_support(e):
                    self._no_think.add(body["model"])
                    body.pop("think")
                else:
                    raise
        await self._raise_for(resp)
        return resp.json()

    async def tags(self) -> List[Dict[str, Any]]:
        resp = await self.http.get("/api/tags", timeout=10)
        await self._raise_for(resp)
        return resp.json().get("models", [])

    async def ps(self) -> List[Dict[str, Any]]:
        """Models currently loaded in memory."""
        resp = await self.http.get("/api/ps", timeout=10)
        await self._raise_for(resp)
        return resp.json().get("models", [])

    async def show(self, model: str) -> Dict[str, Any]:
        resp = await self.http.post("/api/show", json={"model": model}, timeout=30)
        await self._raise_for(resp)
        return resp.json()

    async def context_for(self, model: str) -> int:
        """The context window to ask for with `model`: NUM_CTX_MODELS, else the
        num_ctx in the model's Modelfile, else NUM_CTX. Every request for a model
        must use the same value, or Ollama reloads it."""
        if not model:
            return Config.NUM_CTX
        own = {**context_overrides(Config.TESTED_CTX_MODELS),
               **context_overrides(Config.NUM_CTX_MODELS)}.get(_tagged(model))
        if own:
            return own
        cached = self._model_ctx.get(model)
        if cached is None or time.monotonic() - cached[1] > 300:     # notices a re-created model
            size = None
            try:
                params = (await self.show(model)).get("parameters") or ""
                m = re.search(r"^num_ctx\s+(\d+)", params, flags=re.M)
                size = int(m.group(1)) if m else None
            except Exception as e:
                log.debug("couldn't read %s's Modelfile: %s", model, e)
            cached = self._model_ctx[model] = (size, time.monotonic())
        return cached[0] or Config.NUM_CTX

    async def load(self, model: str, keep_alive: Any) -> None:
        resp = await self.http.post("/api/chat", json=self._with_gpu_layers(
            {"model": model, "messages": [], "keep_alive": keep_alive,
             "options": {"num_ctx": await self.context_for(model)}}))
        await self._raise_for(resp)

    async def unload(self, model: str) -> None:
        resp = await self.http.post("/api/chat", json={"model": model, "messages": [], "keep_alive": 0})
        await self._raise_for(resp)
