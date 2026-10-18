"""Endpoints kept from the previous server that the chat workflow doesn't use:
text-to-speech, speech-to-text, and the experimental tool-calling routes
(gemma_four.py). Each part switches itself off when its package is missing.
Function calling is left as it was, to be reworked later."""

import logging
import os
import tempfile
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import gemma_four as functiongemma
except Exception as e:      # needs the `ollama` package; optional
    functiongemma = None
    log.info("tool-calling endpoints disabled: %s", e)

AVAILABLE = {"tts": edge_tts is not None, "stt": sr is not None, "tools": functiongemma is not None}


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


@router.post("/v1/audio/transcriptions")
@router.post("/stt")
async def speech_to_text(file: UploadFile = File(...), language: str = Form("en-US")):
    if sr is None:
        raise HTTPException(503, "STT not available; pip install SpeechRecognition")
    import asyncio
    data = await file.read()
    fd, path = tempfile.mkstemp(suffix=os.path.splitext(file.filename or "")[1] or ".wav")
    with os.fdopen(fd, "wb") as f:
        f.write(data)

    def _recognise():
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)
        return r.recognize_google(audio, language=language)

    try:
        return {"text": await asyncio.to_thread(_recognise), "language": language}
    except sr.UnknownValueError:
        raise HTTPException(400, "Could not understand audio")
    except sr.RequestError as e:
        raise HTTPException(503, f"STT service error: {e}")
    finally:
        os.unlink(path)


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
