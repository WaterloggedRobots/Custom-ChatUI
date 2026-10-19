"""HTTP layer: OpenAI-style endpoints on top of the chat pipeline."""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import search as web
from . import speech
from .config import Config
from .documents import AttachmentError, load_attachment
from .ollama import Ollama, OllamaError
from .pipeline import ChatJob, prepare, run_chat

log = logging.getLogger("chat_server")


def create_app(ollama_transport: Optional[httpx.AsyncBaseTransport] = None,
               web_transport: Optional[httpx.AsyncBaseTransport] = None) -> FastAPI:
    """Transports are injectable so the tests can stand in for Ollama and the web."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ollama = Ollama(transport=ollama_transport)
        app.state.http = httpx.AsyncClient(transport=web_transport, headers={"User-Agent": Config.USER_AGENT},
                                           limits=httpx.Limits(max_connections=20))
        try:
            names = [m["name"] for m in await app.state.ollama.tags()]
            log.info("Ollama at %s has: %s", Config.OLLAMA_URL, ", ".join(names) or "(no models)")
        except Exception as e:
            log.warning("Ollama not reachable at %s yet: %s", Config.OLLAMA_URL, e)
        log.info("Speech to text: %s", speech.ENGINE or "off")
        if Config.STT_PRELOAD:
            asyncio.get_running_loop().run_in_executor(None, speech.preload)
        yield
        await app.state.ollama.close()
        await app.state.http.aclose()

    app = FastAPI(title="ChatUI server", version="3.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(OllamaError)
    async def _ollama_error(_, e: OllamaError):
        return JSONResponse(status_code=e.status if 400 <= e.status < 600 else 502,
                            content={"error": {"message": e.detail, "type": "ollama_error"}})

    @app.exception_handler(httpx.TransportError)
    async def _unreachable(_, e: httpx.TransportError):
        return JSONResponse(status_code=503, content={"error": {
            "message": f"Ollama not reachable at {Config.OLLAMA_URL}: {e or type(e).__name__}", "type": "ollama_unreachable"}})

    @app.exception_handler(HTTPException)
    async def _http_error(_, e: HTTPException):
        return JSONResponse(status_code=e.status_code, content={"error": {"message": str(e.detail), "type": "request_error"}})

    @app.exception_handler(AttachmentError)
    async def _attachment_error(_, e: AttachmentError):
        return JSONResponse(status_code=400, content={"error": {"message": str(e), "type": "attachment_error"}})

    app.include_router(speech.router)
    if Config.ENABLE_EXTRAS:
        from . import extras
        app.include_router(extras.router)
        extras_available = dict(extras.AVAILABLE)
    else:
        extras_available = {}
    extras_available["stt"] = speech.ENGINE is not None

    # ------------------------------------------------------------------ info

    @app.get("/")
    async def root():
        return {
            "service": "ChatUI server",
            "version": app.version,
            "endpoints": {
                "chat": "POST /v1/chat/completions (OpenAI-style; extras: think, web_search, file parts, tools)",
                "models": "GET /v1/models?detailed=true",
                "web_search": "POST /web_search",
                "browse": "POST /v1/web/browse",
                "extract": "POST /v1/files/extract",
                "speech_to_text": "POST /v1/audio/transcriptions (multipart: file, language); GET /v1/audio/stt",
                "health": "GET /health",
            },
        }

    @app.get("/type")
    async def llm_type():
        return {"type": "ollama"}

    @app.get("/health")
    @app.get("/v1/health")
    async def health():
        try:
            await app.state.ollama.tags()
            ok = True
        except Exception:
            ok = False
        return {"status": "healthy" if ok else "degraded", "ollama": ok, "ollama_url": Config.OLLAMA_URL,
                "default_model": Config.DEFAULT_MODEL, "features": extras_available}

    # ---------------------------------------------------------------- models

    show_cache: Dict[str, Dict[str, Any]] = {}   # digest -> {capabilities, context_length}

    @app.get("/v1/models")
    async def list_models(detailed: bool = False):
        """The models Ollama has right now (no saved list). With detailed=true each entry also
        has capabilities (completion, vision, thinking, tools, embedding), context_length,
        size details and whether it is loaded in memory."""
        models = await app.state.ollama.tags()
        data = []
        for m in models:
            details = m.get("details") or {}
            data.append({"id": m["name"], "object": "model", "owned_by": "ollama", "created": int(time.time()),
                         "size": m.get("size"), "parameter_size": details.get("parameter_size"),
                         "quantization": details.get("quantization_level"), "family": details.get("family")})
        if detailed:
            async def caps(entry, digest):
                cached = show_cache.get(digest) if digest else None
                if cached is None:
                    try:
                        info = await app.state.ollama.show(entry["id"])
                        cached = {"capabilities": info.get("capabilities", []), "context_length": next(
                            (v for k, v in info.get("model_info", {}).items() if k.endswith(".context_length")), None)}
                        if digest:
                            show_cache[digest] = cached
                    except Exception as e:
                        log.info("could not inspect %s: %s", entry["id"], e)
                        cached = {"capabilities": [], "context_length": None}
                entry.update(cached)

            async def loaded():
                try:
                    return {m.get("name") or m.get("model") for m in await app.state.ollama.ps()}
                except Exception:
                    return set()
            *_, running = await asyncio.gather(*(caps(d, m.get("digest")) for d, m in zip(data, models)), loaded())
            for d in data:
                d["loaded"] = d["id"] in running
        return {"object": "list", "data": data, "default": Config.DEFAULT_MODEL}

    @app.post("/v1/models/load")
    async def load_model(model: str = Query(...), keep_alive: str = Query(Config.KEEP_ALIVE)):
        await app.state.ollama.load(model, int(keep_alive) if keep_alive.lstrip("-").isdigit() else keep_alive)
        return {"status": "loaded", "model": model, "keep_alive": keep_alive}

    @app.post("/v1/models/unload/{model:path}")
    async def unload_model(model: str):
        await app.state.ollama.unload(model)
        return {"status": "unloaded", "model": model}

    # ------------------------------------------------------------------ chat

    class ChatRequest(BaseModel):
        model_config = ConfigDict(extra="allow")

        model: Optional[str] = None
        messages: List[Dict[str, Any]]
        stream: bool = False
        temperature: Optional[float] = None
        top_p: Optional[float] = None
        max_tokens: Optional[int] = None
        max_completion_tokens: Optional[int] = None
        stop: Optional[Union[str, List[str]]] = None
        seed: Optional[int] = None
        presence_penalty: Optional[float] = None
        frequency_penalty: Optional[float] = None
        response_format: Optional[Dict[str, Any]] = None
        # Extensions
        think: Optional[Union[bool, str]] = Field(None, description="true/false, or low|medium|high for gpt-oss")
        reasoning_effort: Optional[str] = None
        web_search: Optional[Union[bool, str]] = Field(None, description="off | auto | on")
        num_ctx: Optional[int] = None
        options: Optional[Dict[str, Any]] = Field(None, description="raw Ollama options")
        keep_alive: Optional[Union[int, str]] = None
        tools: Optional[List[Dict[str, Any]]] = Field(None, description="OpenAI-style function tools; the client runs them")

    def make_job(req: ChatRequest) -> ChatJob:
        opts: Dict[str, Any] = {}
        for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("seed", "seed"),
                         ("presence_penalty", "presence_penalty"), ("frequency_penalty", "frequency_penalty"),
                         ("num_ctx", "num_ctx")):
            v = getattr(req, src)
            if v is not None:
                opts[dst] = v
        n = req.max_completion_tokens or req.max_tokens
        if n:
            opts["num_predict"] = n
        if req.stop:
            opts["stop"] = [req.stop] if isinstance(req.stop, str) else req.stop
        opts.update(req.options or {})

        fmt = None
        if req.response_format:
            kind = req.response_format.get("type")
            if kind == "json_object":
                fmt = "json"
            elif kind == "json_schema":
                fmt = (req.response_format.get("json_schema") or {}).get("schema") or "json"

        mode = req.web_search
        if isinstance(mode, bool) or mode is None:
            mode = "on" if mode else "off"
        if mode not in ("off", "auto", "on"):
            raise HTTPException(400, "web_search must be off, auto or on")

        think = req.think if req.think is not None else req.reasoning_effort
        return ChatJob(model=req.model or Config.DEFAULT_MODEL, messages=req.messages, think=think,
                       web_search=mode, options=opts, format=fmt, keep_alive=req.keep_alive,
                       tools=req.tools or None)

    def chunk(cid, model, delta=None, finish=None, **extra):
        body = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
        body.update(extra)
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    async def stream_events(job: ChatJob, cid: str):
        yield chunk(cid, job.model, {"role": "assistant", "content": ""})
        try:
            async for ev in run_chat(job, app.state.ollama, app.state.http):
                if ev.kind == "content":
                    yield chunk(cid, job.model, {"content": ev.data})
                elif ev.kind == "thinking":
                    yield chunk(cid, job.model, {"reasoning_content": ev.data})
                elif ev.kind == "status":
                    yield chunk(cid, job.model, status=ev.data)
                elif ev.kind == "sources":
                    yield chunk(cid, job.model, sources=ev.data)
                elif ev.kind == "tool_calls":
                    yield chunk(cid, job.model, {"tool_calls": ev.data})
                elif ev.kind == "done":
                    yield chunk(cid, job.model, finish=ev.data["finish_reason"],
                                usage=ev.data["usage"], timings=ev.data["timings"])
        except (OllamaError, AttachmentError, ValueError) as e:
            yield f"data: {json.dumps({'error': {'message': getattr(e, 'detail', str(e))}})}\n\n"
        except Exception as e:
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"
        yield "data: [DONE]\n\n"

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatRequest):
        job = make_job(req)
        try:
            prepare(job)
        except AttachmentError:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        if req.stream:
            return StreamingResponse(stream_events(job, cid), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        content, thinking, sources, calls, done = [], [], None, [], {}
        async for ev in run_chat(job, app.state.ollama, app.state.http):
            if ev.kind == "content":
                content.append(ev.data)
            elif ev.kind == "thinking":
                thinking.append(ev.data)
            elif ev.kind == "sources":
                sources = ev.data
            elif ev.kind == "tool_calls":
                calls += [{k: v for k, v in c.items() if k != "index"} for c in ev.data]
            elif ev.kind == "done":
                done = ev.data
        message = {"role": "assistant", "content": "".join(content)}
        if thinking:
            message["reasoning_content"] = "".join(thinking)
        if calls:
            message["tool_calls"] = calls
        body = {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": job.model,
                "choices": [{"index": 0, "message": message, "finish_reason": done.get("finish_reason", "stop")}],
                "usage": done.get("usage", {}), "timings": done.get("timings", {})}
        if sources is not None:
            body["sources"] = sources
        return body

    # ------------------------------------------------------------------- web

    class SearchRequest(BaseModel):
        query: str = Field(..., min_length=1)
        num_results: int = Field(5, ge=1, le=10)
        read_pages: int = Field(0, ge=0, le=5, description="also fetch the text of the top N results")

    @app.post("/web_search")
    @app.post("/v1/web/search")
    async def web_search(body: SearchRequest):
        hits = await web.search(app.state.http, body.query.strip(), body.num_results)
        if body.read_pages:
            await web.fetch_pages(app.state.http, hits, body.read_pages)
        results = [{"title": h.title, "link": h.link, "snippet": h.snippet, **({"text": h.text} if h.text else {})}
                   for h in hits]
        return {"query": body.query, "results": results, "source": hits[0].source if hits else "none"}

    class BrowseRequest(BaseModel):
        url: str
        max_chars: int = Field(20000, ge=100, le=200000)

    @app.post("/v1/web/browse")
    @app.post("/web/browse")
    async def browse(body: BrowseRequest):
        try:
            return await web.fetch_page(app.state.http, body.url, body.max_chars)
        except httpx.HTTPStatusError as e:
            raise HTTPException(502, f"{body.url} returned {e.response.status_code}")
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"could not read {body.url}: {e}")

    # ----------------------------------------------------------------- files

    @app.post("/v1/files/extract")
    async def extract(file: UploadFile = File(...)):
        """Return the text the model would see for an uploaded file."""
        att = load_attachment(file.filename or "file", await file.read(), file.content_type)
        if att.kind == "image":
            return {"filename": att.name, "kind": "image", "bytes_base64": len(att.data)}
        return {"filename": att.name, "kind": "text", "text": att.data, "truncated": att.truncated}

    return app
