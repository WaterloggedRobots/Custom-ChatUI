"""Thin async client for Ollama's native /api/chat.

The native API is used instead of Ollama's /v1 shim because it carries the
separate `thinking` stream, `images` on messages, structured `format` output
and real token counts.
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import Config

log = logging.getLogger(__name__)


class OllamaError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _no_think_support(err: "OllamaError") -> bool:
    return err.status == 400 and "think" in err.detail.lower()


class Ollama:
    def __init__(self, base_url: str = Config.OLLAMA_HOST, transport: Optional[httpx.AsyncBaseTransport] = None):
        # One pooled client for the whole process; streaming replies can take
        # minutes on big models, so there is no read timeout.
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(connect=10, read=None, write=60, pool=None),
            transport=transport,
        )
        # Models that rejected `think`, so later requests skip the retry.
        self._no_think: set = set()

    async def close(self):
        await self.http.aclose()

    def _prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("model") in self._no_think:
            payload = {k: v for k, v in payload.items() if k != "think"}
        return payload

    async def _raise_for(self, resp: httpx.Response):
        if resp.status_code != 200:
            body = (await resp.aread()).decode("utf-8", "replace")
            try:
                body = json.loads(body).get("error", body)
            except ValueError:
                pass
            raise OllamaError(resp.status_code, body)

    async def stream_chat(self, payload: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Yield Ollama's streamed chunks. Retries once without `think` when
        the model doesn't support reasoning."""
        body = self._prepare({**payload, "stream": True})
        try:
            async for chunk in self._stream(body):
                yield chunk
        except OllamaError as e:
            if "think" in body and _no_think_support(e):
                log.info("%s has no thinking support; retrying without it", body["model"])
                self._no_think.add(body["model"])
                body.pop("think")
                async for chunk in self._stream(body):
                    yield chunk
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
        resp = await self.http.post("/api/chat", json=body)
        try:
            await self._raise_for(resp)
        except OllamaError as e:
            if "think" in body and _no_think_support(e):
                self._no_think.add(body["model"])
                body.pop("think")
                resp = await self.http.post("/api/chat", json=body)
                await self._raise_for(resp)
            else:
                raise
        return resp.json()

    async def tags(self) -> List[Dict[str, Any]]:
        resp = await self.http.get("/api/tags", timeout=10)
        await self._raise_for(resp)
        return resp.json().get("models", [])

    async def show(self, model: str) -> Dict[str, Any]:
        resp = await self.http.post("/api/show", json={"model": model}, timeout=30)
        await self._raise_for(resp)
        return resp.json()

    async def load(self, model: str, keep_alive: Any) -> None:
        resp = await self.http.post("/api/chat", json={"model": model, "messages": [], "keep_alive": keep_alive})
        await self._raise_for(resp)

    async def unload(self, model: str) -> None:
        await self.load(model, 0)
