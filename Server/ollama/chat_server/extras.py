"""Endpoints kept from the previous server that the chat workflow doesn't use:
text-to-speech and the experimental tool-calling routes (gemma_four.py).
Speech to text moved to speech.py. Each part switches itself off when its package is missing.
Function calling is left as it was, to be reworked later."""

import logging
import os
import tempfile
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import gemma_four as functiongemma
except Exception as e:      # needs the `ollama` package; optional
    functiongemma = None
    log.info("tool-calling endpoints disabled: %s", e)

AVAILABLE = {"tts": edge_tts is not None, "tools": functiongemma is not None}


class TTSRequest(BaseModel):
    text: str
    voice: str = os.getenv("TTS_VOICE", "en-US-JennyNeural")
    rate: str = "+0%"
    volume: str = "+0%"


@router.post("/v1/audio/speech")
@router.post("/tts")
async def text_to_speech(req: TTSRequest, background: BackgroundTasks):
    if edge_tts is None:
        raise HTTPException(503, "TTS not available; pip install edge-tts")
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    await edge_tts.Communicate(req.text, req.voice, rate=req.rate, volume=req.volume).save(path)
    background.add_task(os.unlink, path)
    return FileResponse(path, media_type="audio/mpeg", filename="speech.mp3")


@router.get("/v1/audio/voices")
async def list_voices():
    if edge_tts is None:
        raise HTTPException(503, "TTS not available; pip install edge-tts")
    return {"voices": await edge_tts.list_voices()}


# ---- tool calling (unchanged behaviour, to be modernised later) ----

def _tools():
    if functiongemma is None:
        raise HTTPException(503, "tool calling not available (gemma_four could not be imported)")
    return functiongemma


@router.get("/v1/tools/list")
async def list_tools():
    return {"data": _tools().tools}


@router.post("/v1/tools/add")
async def add_tool(tool: Dict[str, Any] = Body(...)):
    _tools().ToolsAdd(tool)
    return {"status": "success"}


@router.post("/v1/tools/remove")
async def remove_tool(tool: Dict[str, Any] = Body(...)):
    result = _tools().ToolsRemove(tool)
    return {"status": "success" if result is True else str(result)}


@router.post("/v1/tools/clear")
async def clear_tools():
    _tools().ToolsReset()
    return {"status": "success"}


@router.post("/v1/tools/call")
async def call_tool(prompt: str = Body(...)):
    import asyncio
    return {"tools": await asyncio.to_thread(_tools().ToolCall, prompt)}
