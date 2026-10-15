#!/usr/bin/env python3
"""
Ollama Chatbot API Server
Connects to Ollama backend with TTS, STT, file reading, web browsing, and image processing
"""

import os
import sys
import json
import asyncio
import aiohttp
import requests
from typing import Optional, List, Dict, Any, AsyncGenerator
from datetime import datetime
import logging
from pathlib import Path

# FastAPI for API endpoints
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Pydantic for data validation
from pydantic import BaseModel, Field

# OpenAI client for Ollama compatibility
from openai import OpenAI, AsyncOpenAI

# For file processing
import PyPDF2
import docx
import markdown
from bs4 import BeautifulSoup
import aiofiles

# For web browsing
import httpx
from urllib.parse import urlparse

# For TTS/STT (choose your preferred backend)
import edge_tts  # Microsoft Edge TTS (free, high quality)
import speech_recognition as sr  # For STT

# For image processing
from PIL import Image
import io
import base64

# ============================================
# Configuration
# ============================================

class Config:
    """Configuration settings"""
    # Ollama settings
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "llama3.2:70b")
    
    # Server settings
    HOST = os.getenv("API_HOST", "0.0.0.0")
    PORT = int(os.getenv("API_PORT", "8000"))
    
    # TTS settings
    TTS_VOICE = os.getenv("TTS_VOICE", "en-US-JennyNeural")  # Microsoft Edge TTS voice
    TTS_RATE = os.getenv("TTS_RATE", "+0%")
    TTS_VOLUME = os.getenv("TTS_VOLUME", "+0%")
    
    # File upload settings
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    UPLOAD_DIR.mkdir(exist_ok=True)
    
    # Web browsing settings
    USER_AGENT = "Mozilla/5.0 (compatible; OllamaChatbot/1.0; +http://localhost)"
    REQUEST_TIMEOUT = 30
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
    SEARXNG_TIMEOUT = int(os.getenv("SEARXNG_TIMEOUT", 30))
    SEARXNG_MAX_RESULTS = int(os.getenv("SEARXNG_MAX_RESULTS", 10))

# ============================================
# Initialize Clients
# ============================================

# Async OpenAI client for Ollama
aclient = AsyncOpenAI(
    base_url=f"{Config.OLLAMA_HOST}/v1",
    api_key="dummy"  # Ollama ignores this
)

# Sync client for simple requests
client = OpenAI(
    base_url=f"{Config.OLLAMA_HOST}/v1",
    api_key="dummy"
)

# ============================================
# FastAPI App
# ============================================

app = FastAPI(
    title="Ollama Chatbot API",
    description="Multi-feature chatbot with TTS, STT, file processing, web browsing",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup logging
logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)

# ============================================
# Pydantic Models
# ============================================

class ChatMessage(BaseModel):
    role: str = Field(..., description="user, assistant, or system")
    content: str = Field(..., description="Message content")

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = Config.DEFAULT_MODEL
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048
    stream: Optional[bool] = False
    functions: Optional[List[Dict]] = None

class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict]
    usage: Dict

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = Config.TTS_VOICE
    rate: Optional[str] = Config.TTS_RATE
    volume: Optional[str] = Config.TTS_VOLUME

class WebRequest(BaseModel):
    url: str
    extract_text: Optional[bool] = True
    summarize: Optional[bool] = False

class ImageRequest(BaseModel):
    prompt: str
    model: Optional[str] = Config.DEFAULT_MODEL
    size: Optional[str] = "1024x1024"

# ============================================
# Core Chat Functions
# ============================================

@app.get("/")
async def root():
    return {
        "service": "Ollama Chatbot API",
        "version": "1.0.0",
        "endpoints": {
            "chat": "/v1/chat/completions (OpenAI compatible)",
            "models": "/v1/models",
            "tts": "/tts - Text to Speech",
            "stt": "/stt - Speech to Text",
            "file": "/file/upload - Upload and process files",
            "web": "/web/browse - Browse and scrape websites",
            "image": "/image/generate - Generate images",
            "memory": "/memory - Manage conversation memory",
            "health": "/health - Service health"
        }
    }

@app.get("/health")
async def health_check():
    """Check if Ollama is reachable"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                if resp.status == 200:
                    return {"status": "healthy", "ollama": "connected"}
    except:
        pass
    return {"status": "degraded", "ollama": "disconnected"}

@app.get("/v1/models")
async def list_models():
    """List available models (OpenAI compatible)"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                data = await resp.json()
                models = [{
                    "id": model["name"],
                    "object": "model",
                    "created": int(datetime.now().timestamp()),
                    "owned_by": "ollama"
                } for model in data.get("models", [])]
                
                return {
                    "object": "list",
                    "data": models
                }
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/chat/completions")
async def chat_completion(request: ChatRequest):
    """OpenAI-compatible chat completion endpoint"""
    try:
        # Convert messages to OpenAI format
        messages = [msg.dict() for msg in request.messages]
        
        if request.stream:
            return StreamingResponse(
                stream_chat_response(messages, request),
                media_type="text/event-stream"
            )
        
        # Non-streaming response
        response = await aclient.chat.completions.create(
            model=request.model,
            messages=messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def stream_chat_response(messages: List[Dict], request: ChatRequest):
    """Stream chat responses"""
    try:
        stream = await aclient.chat.completions.create(
            model=request.model,
            messages=messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True
        )
        
        async for chunk in stream:
            yield f"data: {chunk.json()}\n\n"
        
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        logger.error(f"Stream error: {e}")
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

# ============================================
# Tool Calling
# ============================================
@app.post("/v1/tools")
def ToolList():
    #Get list of available tools
    return functiongemma.

# ============================================
# Text to Speech (TTS)
# ============================================

@app.post("/tts")
async def text_to_speech(request: TTSRequest):
    """Convert text to speech using Edge TTS"""
    try:
        # Create communicate object
        communicate = edge_tts.Communicate(
            request.text,
            request.voice,
            rate=request.rate,
            volume=request.volume
        )
        
        # Generate audio
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        
        # Return audio file
        return Response(
            content=bytes(audio_data),
            media_type="audio/mpeg",
            headers={"Content-Disposition": "attachment; filename=speech.mp3"}
        )
        
    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/tts/stream")
async def tts_stream(websocket: WebSocket):
    """Stream TTS audio over WebSocket"""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)
            
            communicate = edge_tts.Communicate(
                request.get("text", ""),
                request.get("voice", Config.TTS_VOICE)
            )
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    await websocket.send_bytes(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    await websocket.send_text(json.dumps({
                        "type": "word",
                        "text": chunk["text"],
                        "offset": chunk["offset"]
                    }))
            
            await websocket.send_text(json.dumps({"type": "end"}))
            
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"TTS stream error: {e}")

# ============================================
# Speech to Text (STT)
# ============================================

@app.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    language: str = Form("en-US")
):
    """Convert speech to text"""
    try:
        # Save uploaded audio temporarily
        temp_path = Config.UPLOAD_DIR / f"temp_audio_{datetime.now().timestamp()}.wav"
        
        async with aiofiles.open(temp_path, 'wb') as f:
            content = await audio.read()
            await f.write(content)
        
        # Use speech recognition
        recognizer = sr.Recognizer()
        with sr.AudioFile(str(temp_path)) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language=language)
        
        # Clean up
        os.unlink(temp_path)
        
        return {"text": text, "language": language}
        
    except sr.UnknownValueError:
        raise HTTPException(status_code=400, detail="Could not understand audio")
    except sr.RequestError as e:
        raise HTTPException(status_code=503, detail=f"STT service error: {e}")
    except Exception as e:
        logger.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# File Processing
# ============================================

@app.post("/file/upload")
async def upload_file(
    file: UploadFile = File(...),
    process: bool = Form(True),
    model: str = Form(Config.DEFAULT_MODEL)
):
    """Upload and process various file types"""
    try:
        # Save file
        file_path = Config.UPLOAD_DIR / file.filename
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        # Extract text based on file type
        text_content = ""
        file_ext = Path(file.filename).suffix.lower()
        
        if file_ext == '.txt':
            text_content = content.decode('utf-8')
            
        elif file_ext == '.pdf':
            with open(file_path, 'rb') as f:
                pdf_reader = PyPDF2.PdfReader(f)
                for page in pdf_reader.pages:
                    text_content += page.extract_text()
                    
        elif file_ext == '.docx':
            doc = docx.Document(file_path)
            text_content = '\n'.join([para.text for para in doc.paragraphs])
            
        elif file_ext == '.md':
            with open(file_path, 'r') as f:
                md_content = f.read()
                html = markdown.markdown(md_content)
                soup = BeautifulSoup(html, 'html.parser')
                text_content = soup.get_text()
        
        elif file_ext in ['.jpg', '.jpeg', '.png', '.gif']:
            # Process image with vision model
            return await process_image(file_path, model)
        
        else:
            text_content = f"File uploaded: {file.filename} (type not supported for text extraction)"
        
        # Optionally process with LLM
        if process and text_content:
            response = await aclient.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Summarize the following content:"},
                    {"role": "user", "content": text_content[:5000]}  # Limit length
                ],
                temperature=0.3,
                max_tokens=1000
            )
            
            return {
                "filename": file.filename,
                "type": file_ext,
                "content": text_content[:1000] + "..." if len(text_content) > 1000 else text_content,
                "summary": response.choices[0].message.content,
                "full_content": text_content if len(text_content) < 10000 else "Content too large"
            }
        
        return {
            "filename": file.filename,
            "type": file_ext,
            "content": text_content[:2000] + "..." if len(text_content) > 2000 else text_content
        }
        
    except Exception as e:
        logger.error(f"File processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# Web Browsing
# ============================================

@app.post("/web/browse")
async def browse_website(request: WebRequest):
    """Browse and scrape website content"""
    try:
        headers = {"User-Agent": Config.USER_AGENT}
        
        async with httpx.AsyncClient(timeout=Config.REQUEST_TIMEOUT) as client:
            response = await client.get(request.url, headers=headers)
            response.raise_for_status()
            
            if request.extract_text:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.decompose()
                
                # Get text
                text = soup.get_text()
                
                # Clean up whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)
                
                # Limit text length
                text = text[:10000] + "..." if len(text) > 10000 else text
                
                result = {
                    "url": request.url,
                    "title": soup.title.string if soup.title else "No title",
                    "text": text
                }
                
                if request.summarize:
                    # Summarize with LLM
                    summary_response = await aclient.chat.completions.create(
                        model=Config.DEFAULT_MODEL,
                        messages=[
                            {"role": "system", "content": "Summarize the following web content concisely:"},
                            {"role": "user", "content": text}
                        ],
                        temperature=0.3,
                        max_tokens=500
                    )
                    result["summary"] = summary_response.choices[0].message.content
                
                return result
            
            return {
                "url": request.url,
                "status": response.status_code,
                "content_type": response.headers.get("content-type", "unknown"),
                "content": response.text[:5000] + "..." if len(response.text) > 5000 else response.text
            }
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"HTTP error: {e}")
    except Exception as e:
        logger.error(f"Web browsing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/web/search")
@app.post("/web/search")
async def web_search(
        query: str = Form(...),
        num_results: int = Form(5),
        format: str = Form("json")
):
    """
    Perform web search using local SearXNG instance

    Args:
        query: Search query string
        num_results: Number of results to return (max 20)
        format: Response format (json or html)
    """
    if not SCRAPING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Web search not available")

    try:
        # Build search URL
        search_url = f"{Config.SEARXNG_URL}/search"

        # Prepare parameters
        params = {
            "q": query,
            "format": "json",
            "categories": "general",
            "engines": "google,bing,brave,duckduckgo",  # Multiple engines for better results
            "language": "en",
            "safesearch": "0"
        }

        async with httpx.AsyncClient(timeout=Config.SEARXNG_TIMEOUT) as client:
            response = await client.get(search_url, params=params)

            if response.status_code != 200:
                logger.error(f"SearXNG error: {response.status_code}")
                raise HTTPException(status_code=502, detail="Search engine error")

            data = response.json()

            # Parse results
            results = []
            for item in data.get("results", [])[:min(num_results, Config.SEARXNG_MAX_RESULTS)]:
                results.append({
                    "title": item.get("title", "No title"),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", item.get("snippet", "No description")),
                    "engine": item.get("engine", "unknown"),
                    "score": item.get("score", 0)
                })

            return {
                "query": query,
                "results": results,
                "total": len(results),
                "source": "searxng",
                "response_time": response.elapsed.total_seconds()
            }

    except httpx.TimeoutException:
        logger.error("SearXNG request timeout")
        raise HTTPException(status_code=504, detail="Search request timeout")
    except Exception as e:
        logger.error(f"Web search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/web/search")
async def web_search_get(
        q: str = Query(..., description="Search query"),
        num_results: int = Query(5, ge=1, le=20)
):
    """GET endpoint for web search (simpler for testing)"""
    return await web_search(query=q, num_results=num_results)


@app.get("/v1/web/engines")
async def get_search_engines():
    """Get available search engines from SearXNG"""
    try:
        search_url = f"{Config.SEARXNG_URL}/config"

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(search_url)

            if response.status_code == 200:
                data = response.json()
                engines = data.get("engines", [])
                return {
                    "engines": [e.get("name") for e in engines if e.get("enabled", False)],
                    "total": len(engines)
                }
            else:
                return {"error": "Could not fetch engines", "engines": []}
    except Exception as e:
        logger.error(f"Error fetching engines: {e}")
        return {"error": str(e), "engines": []}


@app.post("/v1/web/search/enhanced")
async def enhanced_web_search(
        query: str = Form(...),
        context: Optional[str] = Form(None),
        num_results: int = Form(5)
):
    """
    Enhanced web search that uses LLM to generate better search queries
    """
    try:
        # If context is provided, use LLM to generate better search query
        enhanced_query = query
        if context:
            # Use Ollama to optimize the search query based on context
            async with aiohttp.ClientSession() as session:
                ollama_payload = {
                    "model": model_manager.current_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Generate a concise, optimized search query based on the user's question and context. Return ONLY the search query, no explanation."
                        },
                        {
                            "role": "user",
                            "content": f"Context: {context}\n\nQuestion: {query}\n\nGenerate optimized search query:"
                        }
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 100
                    }
                }

                async with session.post(
                        f"{Config.OLLAMA_HOST}/api/chat",
                        json=ollama_payload,
                        timeout=30
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        enhanced_query = result.get("message", {}).get("content", query).strip()
                        logger.info(f"Enhanced query: '{query}' -> '{enhanced_query}'")

        # Perform search with enhanced query
        return await web_search(query=enhanced_query, num_results=num_results)

    except Exception as e:
        logger.error(f"Enhanced search error: {e}")
        # Fall back to regular search
        return await web_search(query=query, num_results=num_results)


@app.get("/v1/web/health")
async def web_search_health():
    """Check if SearXNG is available"""
    try:
        search_url = f"{Config.SEARXNG_URL}/search"

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(search_url, params={"q": "test", "format": "json"})

            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "searxng_url": Config.SEARXNG_URL,
                    "response_time": response.elapsed.total_seconds()
                }
            else:
                return {
                    "status": "unhealthy",
                    "searxng_url": Config.SEARXNG_URL,
                    "error": f"HTTP {response.status_code}"
                }
    except Exception as e:
        return {
            "status": "unavailable",
            "searxng_url": Config.SEARXNG_URL,
            "error": str(e)
        }

# ============================================
# Image Processing
# ============================================

@app.post("/image/analyze")
async def analyze_image(
    file: UploadFile = File(...),
    prompt: str = Form("Describe this image in detail"),
    model: str = Form(Config.DEFAULT_MODEL)
):
    """Analyze an image using a vision model"""
    try:
        # Read image
        image_data = await file.read()
        
        # Convert to base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # For vision models in Ollama (like llava, bakllava)
        response = await aclient.chat.completions.create(
            model=model,  # Should be a vision model like 'llava'
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                    ]
                }
            ],
            max_tokens=1000
        )
        
        return {
            "filename": file.filename,
            "analysis": response.choices[0].message.content
        }
        
    except Exception as e:
        logger.error(f"Image analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def process_image(image_path: Path, model: str):
    """Helper function to process images"""
    with open(image_path, 'rb') as f:
        image_data = f.read()
    
    image_base64 = base64.b64encode(image_data).decode('utf-8')
    
    response = await aclient.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                ]
            }
        ]
    )
    
    return {
        "filename": image_path.name,
        "type": image_path.suffix,
        "description": response.choices[0].message.content
    }

@app.post("/image/generate")
async def generate_image(request: ImageRequest):
    """Generate an image (placeholder - needs image generation model)"""
    # This would require integration with Stable Diffusion or similar
    # For now, we'll use the LLM to generate a prompt for external tools
    try:
        response = await aclient.chat.completions.create(
            model=request.model,
            messages=[
                {"role": "system", "content": "Generate a detailed image generation prompt"},
                {"role": "user", "content": request.prompt}
            ]
        )
        
        return {
            "prompt": request.prompt,
            "generated_prompt": response.choices[0].message.content,
            "note": "Image generation requires additional services (Stable Diffusion, DALL-E, etc.)"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# Memory Management
# ============================================

class ConversationMemory:
    """Simple in-memory conversation storage"""
    
    def __init__(self):
        self.conversations = {}
        self.max_history = 50
    
    def add_message(self, conv_id: str, role: str, content: str):
        if conv_id not in self.conversations:
            self.conversations[conv_id] = []
        
        self.conversations[conv_id].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        
        # Trim if too long
        if len(self.conversations[conv_id]) > self.max_history:
            self.conversations[conv_id] = self.conversations[conv_id][-self.max_history:]
    
    def get_history(self, conv_id: str, limit: int = 10):
        if conv_id not in self.conversations:
            return []
        return self.conversations[conv_id][-limit:]
    
    def clear(self, conv_id: str):
        if conv_id in self.conversations:
            del self.conversations[conv_id]

memory = ConversationMemory()

@app.post("/memory/add")
async def add_to_memory(
    conversation_id: str = Form(...),
    role: str = Form(...),
    content: str = Form(...)
):
    """Add a message to conversation memory"""
    memory.add_message(conversation_id, role, content)
    return {"status": "success", "conversation_id": conversation_id}

@app.get("/memory/get/{conversation_id}")
async def get_memory(conversation_id: str, limit: int = 10):
    """Get conversation history"""
    history = memory.get_history(conversation_id, limit)
    return {
        "conversation_id": conversation_id,
        "history": history,
        "count": len(history)
    }

@app.delete("/memory/clear/{conversation_id}")
async def clear_memory(conversation_id: str):
    """Clear conversation history"""
    memory.clear(conversation_id)
    return {"status": "cleared", "conversation_id": conversation_id}

# ============================================
# WebSocket Chat with Real-time Features
# ============================================

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with all features"""
    await websocket.accept()
    conversation_id = f"ws_{datetime.now().timestamp()}"
    
    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)
            
            message_type = request.get("type", "text")
            
            if message_type == "text":
                # Regular chat message
                response = await aclient.chat.completions.create(
                    model=request.get("model", Config.DEFAULT_MODEL),
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        *memory.get_history(conversation_id, 5),
                        {"role": "user", "content": request.get("content", "")}
                    ],
                    stream=True
                )
                
                full_response = ""
                async for chunk in response:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_response += content
                        await websocket.send_text(json.dumps({
                            "type": "chunk",
                            "content": content,
                            "done": False
                        }))
                
                # Save to memory
                memory.add_message(conversation_id, "user", request.get("content", ""))
                memory.add_message(conversation_id, "assistant", full_response)
                
                await websocket.send_text(json.dumps({
                    "type": "complete",
                    "content": full_response,
                    "done": True
                }))
                
            elif message_type == "tts":
                # Text to speech
                communicate = edge_tts.Communicate(
                    request.get("text", ""),
                    request.get("voice", Config.TTS_VOICE)
                )
                
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        await websocket.send_bytes(chunk["data"])
                    
            elif message_type == "file":
                # File processing (you'd need to handle binary data)
                await websocket.send_text(json.dumps({
                    "type": "info",
                    "content": "File processing via WebSocket not implemented. Use /file/upload endpoint."
                }))
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {conversation_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()

# ============================================
# Main Entry Point
# ============================================

if __name__ == "__main__":
    print(f"🚀 Starting Ollama Chatbot API Server")
    print(f"📡 Ollama backend: {Config.OLLAMA_HOST}")
    print(f"🎯 Default model: {Config.DEFAULT_MODEL}")
    print(f"🌐 API endpoint: http://{Config.HOST}:{Config.PORT}")
    print(f"📝 API docs: http://{Config.HOST}:{Config.PORT}/docs")
    
    uvicorn.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        log_level=Config.LOG_LEVEL.lower()
    )
