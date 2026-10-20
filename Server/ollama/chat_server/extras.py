"""Endpoints kept from the previous server that the chat workflow doesn't use:
the experimental tool-calling routes (gemma_four.py), off when it can't be
imported. Speech to text is in speech.py and text to speech in tts.py.
Function calling is left as it was, to be reworked later."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

log = logging.getLogger(__name__)
router = APIRouter()

try:
    import gemma_four as functiongemma
except Exception as e:      # needs the `ollama` package; optional
    functiongemma = None
    log.info("tool-calling endpoints disabled: %s", e)

AVAILABLE = {"tools": functiongemma is not None}


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
