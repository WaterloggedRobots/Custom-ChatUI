#!/usr/bin/env python3
"""
Ollama Chatbot API Server
Enhanced with full model configuration and switching
"""

import os
import sys
import json
import asyncio
import aiohttp
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
import logging
from pathlib import Path
import time
from fastapi import Form, HTTPException
import httpx

# FastAPI
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Query, BackgroundTasks, Body
from fastapi.responses import StreamingResponse, JSONResponse,FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Pydantic
from pydantic import BaseModel, Field, field_validator  # Change import
from contextlib import asynccontextmanager
from typing import Optional, Union
from contextlib import asynccontextmanager

# OpenAI client
from openai import OpenAI, AsyncOpenAI
import tempfile
import base64
import tiktoken
from ddgs import DDGS

# ============================================
# Additional Imports for Features
# ============================================

# TTS
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("⚠️ edge-tts not installed. TTS features will be disabled.")

# STT
try:
    import speech_recognition as sr
    STT_AVAILABLE = True
except ImportError:
    STT_AVAILABLE = False
    print("⚠️ SpeechRecognition not installed. STT features will be disabled.")

# Web scraping - ADD THIS BLOCK
try:
    import httpx
    from bs4 import BeautifulSoup
    SCRAPING_AVAILABLE = True
except ImportError:
    SCRAPING_AVAILABLE = False
    print("⚠️ httpx or beautifulsoup4 not installed. Web search features will be disabled.")
    print("   Install with: pip install httpx beautifulsoup4")


# ============================================
# Configuration
# ============================================

class Config:
    OLLAMA_HOST=os.getenv("OLLAMA_HOST", "http://localhost:11434")
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "llama3.2:3b")
    HOST = os.getenv("API_HOST", "0.0.0.0")
    PORT = int(os.getenv("API_PORT", "8000"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    OLLAMA_MODELS_DIR = os.getenv("OLLAMA_MODELS", "/home/ollama/Models")
    TTS_VOICE = os.getenv("TTS_VOICE", "en-US-JennyNeural")
    TTS_RATE = os.getenv("TTS_RATE", "+0%")
    TTS_VOLUME = os.getenv("TTS_VOLUME", "+0%")
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    USER_AGENT = "Mozilla/5.0 (compatible; OllamaChatbot/1.0)"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ============================================
# Enhanced Pydantic Models for Model Configuration
# ============================================

class ModelParameters(BaseModel):
    """All configurable model parameters"""
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0, description="Sampling temperature")
    top_p: Optional[float] = Field(0.9, ge=0.0, le=1.0, description="Nucleus sampling")
    top_k: Optional[int] = Field(40, ge=0, description="Top-k sampling")
    max_tokens: Optional[int] = Field(2048, ge=1, description="Maximum tokens to generate")
    presence_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0, description="Presence penalty")
    frequency_penalty: Optional[float] = Field(0.0, ge=-2.0, le=2.0, description="Frequency penalty")
    repeat_penalty: Optional[float] = Field(1.1, ge=0.0, description="Repeat penalty")
    stop: Optional[List[str]] = Field(None, description="Stop sequences")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    num_ctx: Optional[int] = Field(8192, ge=1, description="Context window size")
    num_predict: Optional[int] = Field(None, ge=1, description="Number of tokens to predict")
    num_thread: Optional[int] = Field(None, ge=1, description="Number of threads")
    num_gpu: Optional[int] = Field(None, ge=0, description="Number of GPUs to use")
    repeat_last_n: Optional[int] = Field(64, ge=0, description="Last n tokens to penalize")
    tfs_z: Optional[float] = Field(1.0, ge=0.0, description="Tail free sampling")
    mirostat: Optional[int] = Field(0, ge=0, le=2, description="Mirostat mode")
    mirostat_tau: Optional[float] = Field(5.0, description="Mirostat tau")
    mirostat_eta: Optional[float] = Field(0.1, description="Mirostat eta")
    numa: Optional[bool] = Field(False, description="Enable NUMA support")
    num_batch: Optional[int] = Field(512, description="Batch size for prompt processing")
    num_keep: Optional[int] = Field(None, description="Number of tokens to keep from prompt")
    
    class Config:
        json_schema_extra = {
            "example": {
                "temperature": 0.8,
                "top_p": 0.95,
                "max_tokens": 4096,
                "num_ctx": 16384,
                "seed": 42
            }
        }

class ModelConfig(BaseModel):
    """Complete model configuration"""
    model: str = Field(..., description="Model name")
    parameters: ModelParameters = Field(default_factory=ModelParameters)
    system_prompt: Optional[str] = Field(None, description="System prompt for the model")
    template: Optional[str] = Field(None, description="Custom prompt template")
    context: Optional[List[int]] = Field(None, description="Context tokens")
    options: Optional[Dict[str, Any]] = Field(None, description="Additional Ollama options")

class ModelSwitchRequest(BaseModel):
    """Request to switch the active model"""
    model: str = Field(..., description="Model name to switch to")
    parameters: Optional[ModelParameters] = Field(None, description="Model parameters")
    system_prompt: Optional[str] = Field(None, description="System prompt")
    keep_alive: Optional[Union[int, str]] = Field(-1, description="How long to keep model loaded")

    @field_validator('model', check_fields=False)  # Add this parameter
    @classmethod
    def validate_model(cls, v):
        if not v:
            raise ValueError("Model name cannot be empty")
        return v

class ChatRequest(BaseModel):
    """Enhanced chat request with model override per request"""
    messages: List[Dict[str, str]]
    model: Optional[str] = None  # If not provided, use current active model
    parameters: Optional[ModelParameters] = None
    system_prompt: Optional[str] = None
    stream: Optional[bool] = False
    functions: Optional[List[Dict]] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "messages": [{"role": "user", "content": "Hello!"}],
                "model": "llama3.2:70b",
                "parameters": {
                    "temperature": 0.8,
                    "max_tokens": 2048
                }
            }
        }

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup code (runs before app starts) ---
    logger.info(f"Starting Ollama API server")
    logger.info(f"Active model: {model_manager.current_model}")

    if Config.OLLAMA_MODELS_DIR:
        os.environ["OLLAMA_MODELS"] = Config.OLLAMA_MODELS_DIR
        logger.info(f"📁 Model download path: {Config.OLLAMA_MODELS_DIR}")

    # Check Ollama connection
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    logger.info(f"Connected to Ollama. Available models: {models}")

                    if model_manager.current_model not in models:
                        logger.warning(f"Default model {model_manager.current_model} not found locally")
    except Exception as e:
        logger.error(f"Failed to connect to Ollama: {e}")

    yield  # App runs here

    # --- Shutdown code (runs after app stops) ---
    logger.info("Shutting down Ollama API server")
    # Add any cleanup code here if needed

# ============================================
# Model Manager
# ============================================

class ModelManager:
    """Manages active model and configurations"""
    
    def __init__(self):
        self.current_model = Config.DEFAULT_MODEL
        self.model_configs: Dict[str, ModelConfig] = {}
        self.loaded_models: Dict[str, datetime] = {}
        self.default_parameters = ModelParameters()
        
    def set_active_model(self, model_name: str, config: Optional[ModelConfig] = None):
        """Set the active model"""
        self.current_model = model_name
        if config:
            self.model_configs[model_name] = config
        self.loaded_models[model_name] = datetime.now()
        
    def get_model_config(self, model_name: Optional[str] = None) -> ModelConfig:
        """Get configuration for a model"""
        model = model_name or self.current_model
        
        if model in self.model_configs:
            return self.model_configs[model]
        
        # Return default config
        return ModelConfig(
            model=model,
            parameters=self.default_parameters
        )
    
    def update_model_config(self, model_name: str, **kwargs):
        """Update model configuration"""
        if model_name not in self.model_configs:
            self.model_configs[model_name] = ModelConfig(model=model_name)
        
        config = self.model_configs[model_name]
        
        # Update parameters if provided
        if 'parameters' in kwargs:
            for key, value in kwargs['parameters'].items():
                if hasattr(config.parameters, key):
                    setattr(config.parameters, key, value)
        
        # Update other fields
        for key, value in kwargs.items():
            if key != 'parameters' and hasattr(config, key):
                setattr(config, key, value)
    
    def get_ollama_options(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Convert model config to Ollama options"""
        config = self.get_model_config(model_name)
        options = config.parameters.dict(exclude_none=True)
        
        # Add any additional options
        if config.options:
            options.update(config.options)
        
        return options
    
    def list_loaded(self) -> List[str]:
        """List currently loaded models"""
        # Clean up old entries (older than 5 minutes)
        now = datetime.now()
        self.loaded_models = {
            model: ts for model, ts in self.loaded_models.items()
            if (now - ts).total_seconds() < 300
        }
        return list(self.loaded_models.keys())

# Initialize model manager
model_manager = ModelManager()

# ============================================
# Initialize Clients
# ============================================

aclient = AsyncOpenAI(
    base_url=f"{Config.OLLAMA_HOST}/v1",
    api_key="dummy"
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
    description="Enhanced API with model switching and configuration",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)

# ============================================
# Model Management Endpoints
# ============================================

@app.post("/v1/models/switch")
async def switch_model(request: ModelSwitchRequest):
    """
    Switch the active model with full configuration
    
    Example JSON:
    {
        "model": "llama3.2:70b",
        "parameters": {
            "temperature": 0.8,
            "top_p": 0.95,
            "max_tokens": 4096,
            "num_ctx": 16384,
            "seed": 42
        },
        "system_prompt": "You are a helpful assistant.",
        "keep_alive": -1
    }
    """
    try:
        # First, check if model exists
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                data = await resp.json()
                available_models = [m["name"] for m in data.get("models", [])]
                
                if request.model not in available_models:
                    # Model not found, try to pull it
                    logger.info(f"Model {request.model} not found locally, attempting to pull...")
                    
                    # You could implement auto-pull here
                    # For now, return helpful error
                    return JSONResponse(
                        status_code=404,
                        content={
                            "error": f"Model '{request.model}' not found locally",
                            "available_models": available_models,
                            "hint": "Pull the model first: ollama pull " + request.model
                        }
                    )
        
        # Update model configuration
        config_dict = {"model": request.model}
        if request.parameters:
            config_dict["parameters"] = request.parameters.dict(exclude_none=True)
        if request.system_prompt:
            config_dict["system_prompt"] = request.system_prompt
        
        model_manager.update_model_config(request.model, **config_dict)
        
        # Set as active model
        model_manager.set_active_model(request.model)
        
        # Preload model if keep_alive is set
        if request.keep_alive != 0:
            # Send a dummy request to load the model
            await aclient.chat.completions.create(
                model=request.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0
            )
        
        # Get the full config for response
        full_config = model_manager.get_model_config(request.model)
        
        return {
            "status": "success",
            "active_model": request.model,
            "configuration": full_config.dict(exclude_none=True),
            "loaded": request.keep_alive != 0,
            "message": f"Switched to model: {request.model}"
        }
        
    except Exception as e:
        logger.error(f"Model switch error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/models/active")
async def get_active_model():
    """Get currently active model and its configuration"""
    config = model_manager.get_model_config()
    loaded_models = model_manager.list_loaded()
    
    return {
        "active_model": model_manager.current_model,
        "configuration": config.dict(exclude_none=True),
        "loaded_models": loaded_models,
        "is_loaded": model_manager.current_model in loaded_models
    }

@app.post("/v1/models/{model_name}/config")
async def configure_model(model_name: str, config: ModelConfig):
    """Configure a specific model without switching to it"""
    model_manager.update_model_config(model_name, **config.dict(exclude_none=True))
    return {
        "status": "success",
        "model": model_name,
        "configuration": model_manager.get_model_config(model_name).dict(exclude_none=True)
    }

@app.get("/v1/models/{model_name}/config")
async def get_model_config(model_name: str):
    """Get configuration for a specific model"""
    try:
        config = model_manager.get_model_config(model_name)
        return config.dict(exclude_none=True)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Model {model_name} not configured")

@app.post("/v1/models/load")
async def load_model(
    model: str = Query(..., description="Model name to load"),
    keep_alive: Union[int, str] = Query(-1, description="How long to keep loaded")
):
    """Explicitly load a model into memory"""
    try:
        # Send a minimal request to load the model
        response = await aclient.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0
        )
        
        model_manager.loaded_models[model] = datetime.now()
        
        return {
            "status": "loaded",
            "model": model,
            "keep_alive": keep_alive
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/models/unload/{model_name}")
async def unload_model(model_name: str):
    """Unload a model from memory"""
    try:
        # Ollama doesn't have explicit unload, but we can use the API to stop
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{Config.OLLAMA_HOST}/api/generate",
                json={"model": model_name, "prompt": "", "keep_alive": 0}
            ) as resp:
                pass
        
        if model_name in model_manager.loaded_models:
            del model_manager.loaded_models[model_name]
        
        return {"status": "unloaded", "model": model_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# Enhanced Chat Endpoint
# ============================================

    """
    Chat completion with per-request model override
    
    You can override the active model per request by specifying 'model'
    and 'parameters' in the request JSON.
    """
def prepare_openai_params(request: ChatRequest) -> Dict:
    """Extract only OpenAI-compatible parameters from the request"""

    # Convert messages to dict format properly
    messages = []
    for msg in request.messages:
        if hasattr(msg, 'dict'):
            # It's a ChatMessage object
            messages.append(msg.dict())
        elif isinstance(msg, dict):
            # It's already a dict
            messages.append(msg)
        else:
            # Try to convert
            messages.append({"role": msg.role, "content": msg.content})

    # Base parameters that OpenAI client accepts
    openai_params = {
        "model": request.model,
        "messages": messages,  # Now properly formatted
        "stream": request.stream
    }

    # Add optional parameters only if they exist
    optional_params = [
        ("temperature", float),
        ("max_tokens", int),
        ("top_p", float),
        ("frequency_penalty", float),
        ("presence_penalty", float),
        ("stop", (list, str)),  # Can be list or string
        ("seed", int)
    ]

    for param_name, param_type in optional_params:
        if hasattr(request, param_name):
            value = getattr(request, param_name)
            if value is not None:
                openai_params[param_name] = value

    logger.debug(f"Sending to OpenAI: {list(openai_params.keys())}")
    return openai_params


@app.post("/v1/chat/completions")
async def chat_completion(request: ChatRequest):
    try:
        # Determine which model to use
        model_name = request.model or model_manager.current_model

        # Get model configuration
        model_config = model_manager.get_model_config(model_name)

        # Merge parameters (request overrides config)
        params = model_config.parameters.dict(exclude_none=True)
        if request.parameters:
            request_params = request.parameters.dict(exclude_none=True)
            params.update(request_params)

        # Map OpenAI's "max_tokens" to Ollama's "num_predict"
        if "max_tokens" in params:
            params["num_predict"] = params.pop("max_tokens")
        if hasattr(request, 'max_tokens') and request.max_tokens:
            params["num_predict"] = request.max_tokens

        # Prepare messages with system prompt
        messages = request.messages.copy()
        system_prompt = request.system_prompt or model_config.system_prompt
        if system_prompt and not any(msg.get("role") == "system" for msg in messages):
            messages.insert(0, {"role": "system", "content": system_prompt})

        # Track this model as loaded
        model_manager.loaded_models[model_name] = datetime.now()

        # Handle streaming response
        if request.stream:
            return StreamingResponse(
                stream_chat_response(model_name, messages, params),
                media_type="text/event-stream"
            )

        # Make the actual call to Ollama
        async with aiohttp.ClientSession() as session:
            ollama_payload = {
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": params
            }

            async with session.post(
                f"{Config.OLLAMA_HOST}/api/chat",
                json=ollama_payload
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=error_text)

                result = await resp.json()

                # Extract token counts from Ollama response
                prompt_tokens = result.get("prompt_eval_count", 0)
                completion_tokens = result.get("eval_count", 0)

                # Calculate prompt tokens if not provided (fallback)
                if prompt_tokens == 0:
                    # Rough estimate: ~4 chars per token for English text
                    # Replace the rough estimate with:
                    prompt_text = " ".join([msg.get("content", "") for msg in messages])
                    prompt_tokens = count_tokens(prompt_text)

                # Convert Ollama response to OpenAI format WITH token usage
                return {
                    "id": f"chatcmpl-{int(datetime.now().timestamp())}",
                    "object": "chat.completion",
                    "created": int(datetime.now().timestamp()),
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": result.get("message", {}).get("content", "")
                        },
                        "finish_reason": result.get("done_reason", "stop")
                    }],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens
                    }
                }

    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def stream_chat_response(model: str, messages: List[Dict], params: Dict):
    """Stream chat responses with token usage in final chunk"""
    try:
        # Filter parameters for OpenAI client
        openai_compatible_params = {}
        param_mapping = {"num_predict": "max_tokens"}
        allowed_params = {"temperature", "max_tokens", "top_p", "frequency_penalty",
                         "presence_penalty", "stop", "seed"}

        for key, value in params.items():
            if key in allowed_params:
                openai_compatible_params[key] = value
            elif key in param_mapping:
                openai_compatible_params[param_mapping[key]] = value

        # Track token counts
        total_prompt_tokens = 0
        total_completion_tokens = 0

        # Calculate prompt tokens (rough estimate)
        # Replace the rough estimate with:
        prompt_text = " ".join([msg.get("content", "") for msg in messages])
        prompt_tokens = count_tokens(prompt_text)

        # Create the stream
        stream = await aclient.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            **openai_compatible_params
        )

        async for chunk in stream:
            # Count completion tokens as they arrive
            if chunk.choices and chunk.choices[0].delta.content:
                total_completion_tokens += 1

            yield f"data: {chunk.json()}\n\n"

        # Send final chunk with token usage (OpenAI format)
        final_chunk = {
            "id": f"chatcmpl-{int(datetime.now().timestamp())}",
            "object": "chat.completion.chunk",
            "created": int(datetime.now().timestamp()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens
            }
        }
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Stream error: {e}")
        yield f"data: {json.dumps({'error': str(e)})}\n\n"

# ============================================
# Model Information Endpoints
# ============================================

@app.get("/v1/models")
async def list_models(detailed: bool = False):
    """List available models with optional details"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                data = await resp.json()
                
                if detailed:
                    # Return detailed model info including configs
                    models = []
                    for model in data.get("models", []):
                        model_name = model["name"]
                        config = model_manager.get_model_config(model_name)
                        models.append({
                            **model,
                            "configured": model_name in model_manager.model_configs,
                            "configuration": config.dict(exclude_none=True),
                            "loaded": model_name in model_manager.loaded_models
                        })
                else:
                    # Simple OpenAI-compatible format
                    models = [{
                        "id": model["name"],
                        "created": int(datetime.now().timestamp()),
                        "owned_by": "ollama"
                    } for model in data.get("models", [])]
                
                return {                        "object": "model",

                    "object": "list",
                    "data": models
                }
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/models/{model_name}")
async def get_model_info(model_name: str):
    """Get detailed information about a specific model"""
    try:
        # Get model details from Ollama
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{Config.OLLAMA_HOST}/api/show",
                json={"model": model_name}
            ) as resp:
                if resp.status == 404:
                    raise HTTPException(status_code=404, detail="Model not found")
                
                model_info = await resp.json()
        
        # Add our configuration
        config = model_manager.get_model_config(model_name)
        
        return {
            **model_info,
            "configured": model_name in model_manager.model_configs,
            "configuration": config.dict(exclude_none=True),
            "loaded": model_name in model_manager.loaded_models,
            "is_active": model_name == model_manager.current_model
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting model info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/type")
def get_llm_type():
    return {"type":"ollama"}
# ============================================
# Original Feature Endpoints (TTS, STT, etc.)
# ============================================
# ... (keep all your existing TTS, STT, file, web, image endpoints)# Main Entry Point


# ============================================
# Main Entry Point
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup code (runs before app starts) ---
    logger.info(f"Starting Ollama API server")
    logger.info(f"Active model: {model_manager.current_model}")

    if Config.OLLAMA_MODELS_DIR:
        os.environ["OLLAMA_MODELS"] = Config.OLLAMA_MODELS_DIR
        logger.info(f"📁 Model download path: {Config.OLLAMA_MODELS_DIR}")

    # Check Ollama connection
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    logger.info(f"Connected to Ollama. Available models: {models}")

                    if model_manager.current_model not in models:
                        logger.warning(f"Default model {model_manager.current_model} not found locally")
    except Exception as e:
        logger.error(f"Failed to connect to Ollama: {e}")

    yield  # App runs here

    # --- Shutdown code (runs after app stops) ---
    logger.info("Shutting down Ollama API server")
    # Add any cleanup code here if needed
"""
@app.on_event("startup")
async def startup_event():
    ###Initialize on startup###
    logger.info(f"Starting Ollama API server")
    logger.info(f"Active model: {model_manager.current_model}")
    
    if Config.OLLAMA_MODELS_DIR:
        os.environ["OLLAMA_MODELS"] = Config.OLLAMA_MODELS_DIR
        logger.info(f"📁 Model download path: {Config.OLLAMA_MODELS_DIR}")
    
    logger.info(f"Starting Ollama API server")
    # Check Ollama connection
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    logger.info(f"Connected to Ollama. Available models: {models}")
                    
                    if model_manager.current_model not in models:
                        logger.warning(f"Default model {model_manager.current_model} not found locally")
    except Exception as e:
        logger.error(f"Failed to connect to Ollama: {e}")
"""

# ============================================
# TTS Endpoints
# ============================================
class TTSRequest(BaseModel):
    """Text to Speech request model"""
    text: str = Field(..., description="Text to convert to speech")
    voice: Optional[str] = Field("en-US-JennyNeural", description="Voice to use")
    rate: Optional[str] = Field("+0%", description="Speech rate")
    volume: Optional[str] = Field("+0%", description="Speech volume")
    output_format: Optional[str] = Field("mp3", description="Output audio format (e.g., mp3, wav)")

@app.post("/v1/audio/speech")
@app.post("/tts")
async def text_to_speech(
    request: TTSRequest,
    background_tasks: BackgroundTasks
):
    """
    Convert text to speech using Edge TTS

    Returns an audio file (MP3 by default)
    """
    if not EDGE_TTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="TTS not available. Install edge-tts")

    try:
        # Create temp file for audio
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{request.output_format}")
        temp_path = temp_file.name
        temp_file.close()

        # Generate speech
        communicate = edge_tts.Communicate(
            request.text,
            request.voice,
            rate=request.rate,
            volume=request.volume
        )

        await communicate.save(temp_path)

        # Schedule cleanup
        background_tasks.add_task(os.unlink, temp_path)

        # Return audio file
        return FileResponse(
            path=temp_path,
            media_type=f"audio/{request.output_format}",
            filename=f"speech_{datetime.now().timestamp()}.{request.output_format}"
        )

    except Exception as e:
        logger.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/v1/audio/speech/stream")
@app.websocket("/tts/stream")
async def tts_stream(websocket: WebSocket):
    """Stream TTS audio over WebSocket"""
    if not EDGE_TTS_AVAILABLE:
        await websocket.close(code=1011, reason="TTS not available")
        return

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)

            text = request.get("text", "")
            voice = request.get("voice", Config.TTS_VOICE)
            rate = request.get("rate", Config.TTS_RATE)
            volume = request.get("volume", Config.TTS_VOLUME)

            communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)

            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    await websocket.send_bytes(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    await websocket.send_text(json.dumps({
                        "type": "word",
                        "text": chunk["text"],
                        "offset": chunk["offset"],
                        "duration": chunk["duration"]
                    }))

            await websocket.send_text(json.dumps({"type": "end"}))

    except WebSocketDisconnect:
        logger.info("TTS WebSocket disconnected")
    except Exception as e:
        logger.error(f"TTS stream error: {e}")
        await websocket.close()

@app.get("/v1/audio/voices")
async def list_voices():
    """List available TTS voices"""
    if not EDGE_TTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="TTS not available")

    try:
        voices = await edge_tts.list_voices()
        return {"voices": voices}
    except Exception as e:
        logger.error(f"Error listing voices: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# STT Endpoints
# ============================================

@app.post("/v1/audio/transcriptions")
@app.post("/stt")
async def speech_to_text(
    file: UploadFile = File(...),
    language: str = Form("en-US")
):
    """
    Convert speech to text using SpeechRecognition
    """
    if not STT_AVAILABLE:
        raise HTTPException(status_code=503, detail="STT not available. Install SpeechRecognition")

    temp_path = None
    try:
        content = await file.read()
        temp_path = Config.UPLOAD_DIR / f"stt_{datetime.now().timestamp()}_{file.filename}"

        with open(temp_path, "wb") as f:
            f.write(content)

        recognizer = sr.Recognizer()
        with sr.AudioFile(str(temp_path)) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language=language)

        return {"text": text, "language": language}

    except sr.UnknownValueError:
        raise HTTPException(status_code=400, detail="Could not understand audio")
    except sr.RequestError as e:
        raise HTTPException(status_code=503, detail=f"STT service error: {e}")
    except Exception as e:
        logger.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and temp_path.exists():
            try:
                os.unlink(temp_path)
            except:
                pass

# ============================================
# Image Processing Endpoints
# ============================================

@app.post("/v1/images/analyze_from_path")
@app.post("/image/analyze_from_path")
async def analyze_image_from_path(request: dict):
    try:
        file_path = request.get('file_path')
        prompt = request.get('prompt', "Describe this image in detail")
        model = request.get('model', "llama3.2-vision:latest")

        if not file_path:
            raise HTTPException(status_code=400, detail="No file_path provided")

        # Validate file exists
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        # Read and encode the image
        with open(file_path, 'rb') as f:
            content = f.read()

        # Verify it's a valid image
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(content))
            logger.info(f"✅ Valid image: {img.format} {img.width}x{img.height}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid image file: {e}")

        # Convert to base64
        image_base64 = base64.b64encode(content).decode('utf-8')

        # CRITICAL FIX: Use the correct format for llama3.2-vision
        # The model expects images in a separate 'images' array, not embedded in text
        ollama_payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_base64]  # This is the key!
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 500
            }
        }

        logger.info(f"📤 Sending to Ollama with {len(image_base64)} chars base64")

        # Send to Ollama
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{Config.OLLAMA_HOST}/api/chat",
                json=ollama_payload,
                timeout=120
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"❌ Ollama error: {error_text}")
                    raise HTTPException(status_code=500, detail=error_text)

                result = await resp.json()
                description = result.get("message", {}).get("content", "")

                if not description:
                    # Try alternative format
                    alt_payload = {
                        "model": model,
                        "prompt": prompt,
                        "images": [image_base64],
                        "stream": False
                    }

                    async with session.post(
                        f"{Config.OLLAMA_HOST}/api/generate",
                        json=alt_payload,
                        timeout=120
                    ) as gen_resp:
                        if gen_resp.status == 200:
                            gen_result = await gen_resp.json()
                            description = gen_result.get("response", "")

        logger.info(f"📝 Got description ({len(description)} chars)")

        return {
            "file_path": file_path,
            "filename": os.path.basename(file_path),
            "description": description,
            "model_used": model,
            "image_info": {
                "size_bytes": len(content),
                "width": img.width,
                "height": img.height,
                "format": img.format
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# Web Search Endpoints
# ============================================

last_request_time=time.time()
# Add a simple mock for testing
@app.post("/v1/web/search")
@app.post("/web/search")
async def web_search(
    query: str = Form(...),
    num_results: int = Form(5)
):
    """Perform web search with rate limiting"""
    global last_request_time

    # Rate limiting: ensure at least 2 seconds between requests
    now = time.time()
    time_since_last = now - last_request_time
    if time_since_last < 2.0:
        await asyncio.sleep(2.0 - time_since_last)

    last_request_time = time.time()

    # Try real search
    try:
        results = await duckduckgo_search_async(query, num_results)
        if results:
            return {
                "query": query,
                "results": results,
                "source": "duckduckgo"
            }
    except Exception as e:
        print(f"DuckDuckGo search failed: {e}")
        print(f"DuckDuckGo search failed: {e}")

    # Fallback to Wikipedia if DuckDuckGo fails
    try:
        results = await wikipedia_search(query, num_results)
        if results:
            return {
                "query": query,
                "results": results,
                "source": "wikipedia"
            }
    except Exception as e:
        print(f"Wikipedia search failed: {e}")

    # Final fallback
    return {
        "query": query,
        "results": [
            {
                "title": f"Search results for: {query}",
                "link": f"https://www.google.com/search?q={query.replace(' ', '+')}",
                "snippet": f"Unable to retrieve search results. Please try again later or click the link to search directly."
            }
        ],
        "source": "fallback"
    }


async def duckduckgo_search_async(query: str, max_results: int = 5):
    """Async wrapper for DuckDuckGo search"""

    def _sync_search():
        with DDGS() as ddgs:
            return list(ddgs.text(
                query,
                max_results=max_results,
                backend="auto"
            ))

    # Run in thread pool to avoid blocking
    return await asyncio.to_thread(_sync_search)


async def wikipedia_search(query: str, num_results: int = 5):
    """Search Wikipedia as fallback"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": num_results
            }
        )

        if response.status_code == 200:
            data = response.json()
            results = []
            for item in data.get("query", {}).get("search", []):
                title = item.get("title", "")
                results.append({
                    "title": title,
                    "link": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    "snippet": item.get("snippet", "").replace('<span class="searchmatch">', '').replace('</span>', '')
                })
            return results

        return []
@app.post("/v1/web/browse")
@app.post("/web/browse")
async def browse_website(
    url: str = Form(...),
    summarize: bool = Form(False)
):
    """Browse and extract content from a webpage"""
    if not SCRAPING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Web browsing not available")

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": Config.USER_AGENT})
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            for script in soup(["script", "style"]):
                script.decompose()

            title = soup.title.string if soup.title else None
            text = soup.get_text()

            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            text = text[:5000] + "..." if len(text) > 5000 else text

            summary = None
            if summarize:
                try:
                    response = await aclient.chat.completions.create(
                        model=model_manager.current_model,
                        messages=[
                            {"role": "system", "content": "Summarize this webpage content."},
                            {"role": "user", "content": text[:2000]}
                        ],
                        max_tokens=300
                    )
                    summary = response.choices[0].message.content
                except:
                    pass

            return {
                "url": url,
                "title": title,
                "text": text,
                "summary": summary
            }

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        logger.error(f"Web browsing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# File Processing Endpoints
# ============================================

@app.post("/v1/files/process_from_path")
@app.post("/file/process_from_path")
async def process_file_from_path(request: dict):
    """
    Process a file by providing a file path on the server

    Expected JSON format:
    {
        "file_path": "/absolute/path/to/file.txt",
        "include_content": true,  # optional, default: true
        "generate_summary": false,  # optional, default: false
        "model": "llama3.2:3b"  # optional, uses current model if not specified
    }
    """
    try:
        # Extract parameters
        file_path = request.get('file_path')
        include_content = request.get('include_content', True)
        generate_summary = request.get('generate_summary', False)
        model = request.get('model')

        if not file_path:
            raise HTTPException(status_code=400, detail="No file_path provided")

        # Validate file exists
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

        if not os.path.isfile(file_path):
            raise HTTPException(status_code=400, detail=f"Path is not a file: {file_path}")

        # Check file size (with warning for large files)
        file_size = os.path.getsize(file_path)
        max_size = 50 * 1024 * 1024  # 50MB
        if file_size > max_size:
            raise HTTPException(status_code=413, detail=f"File too large: {file_size} bytes (max 50MB)")

        # Read file content
        with open(file_path, 'rb') as f:
            content = f.read()

        # Determine file type and encoding
        file_ext = Path(file_path).suffix.lower()
        file_type = "unknown"
        encoding = "utf-8"
        text_content = None
        is_text = False

        # Check if it's a text-based file
        text_extensions = [
            '.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.xml',
            '.c', '.cpp', '.java', '.rs', '.go', '.php', '.rb', '.sh',
            '.csv', '.tsv', '.yml', '.yaml', '.toml', '.ini', '.cfg',
            '.log', '.rst', '.tex', '.bib'
        ]

        if file_ext in text_extensions:
            is_text = True
            try:
                text_content = content.decode('utf-8')
            except UnicodeDecodeError:
                # Try other common encodings
                for enc in ['latin-1', 'cp1252', 'iso-8859-1']:
                    try:
                        text_content = content.decode(enc)
                        encoding = enc
                        break
                    except:
                        continue
            if text_content is None:
                text_content = f"[Could not decode file with common encodings. Raw bytes: {content[:100]}...]"

        # Handle different file types
        result = {
            "file_path": file_path,
            "filename": os.path.basename(file_path),
            "file_type": file_type,
            "size": file_size,
            "extension": file_ext,
            "is_text": is_text,
            "encoding": encoding if is_text else None,
            "metadata": {
                "processed": include_content or generate_summary
            }
        }

        # Include full content if requested
        if include_content and is_text:
            result["content"] = text_content
            result["content_length"] = len(text_content)
            result["line_count"] = len(text_content.splitlines()) if text_content else 0
        elif include_content and not is_text:
            # For binary files, include base64 encoded content
            result["content_base64"] = base64.b64encode(content).decode('utf-8')
            result["content_type"] = "base64"

        # Generate summary if requested
        if generate_summary and is_text and text_content and len(text_content) > 50:
            try:
                model_name = model or model_manager.current_model
                content_for_llm = text_content[:4000] if len(text_content) > 4000 else text_content

                response = await aclient.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": f"Summarize this {file_type or 'text'} file content concisely. Focus on key information."},
                        {"role": "user", "content": content_for_llm}
                    ],
                    max_tokens=300,
                    temperature=0.3
                )
                result["summary"] = response.choices[0].message.content
            except Exception as e:
                logger.warning(f"Summary generation failed: {e}")
                result["summary"] = f"[Summary generation failed: {str(e)}]"

        # For images, redirect to image analysis
        elif file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
            image_result = await analyze_image_from_path({
                "file_path": file_path,
                "prompt": request.get('image_prompt', "Extract any text from this image and describe its contents"),
                "model": model or "llama3.2-vision:latest"
            })
            result.update({
                "file_type": "image",
                "image_analysis": image_result
            })

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"File processing error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/files/upload")
@app.post("/file/upload")
async def upload_file(
    file: UploadFile = File(...),
    process: bool = Form(True)
):
    """Upload and process a file"""
    try:
        content = await file.read()
        file_ext = Path(file.filename).suffix.lower()

        text_content = None
        file_type = "unknown"

        if file_ext == '.txt':
            file_type = "text"
            text_content = content.decode('utf-8', errors='ignore')
        elif file_ext in ['.py', '.js', '.html', '.css', '.json']:
            file_type = "code"
            text_content = content.decode('utf-8', errors='ignore')
        else:
            text_content = f"[Binary file: {file.filename}]"

        summary = None
        if process and text_content and len(text_content) > 50:
            try:
                response = await aclient.chat.completions.create(
                    model=model_manager.current_model,
                    messages=[
                        {"role": "system", "content": "Summarize this file content briefly."},
                        {"role": "user", "content": text_content[:2000]}
                    ],
                    max_tokens=200
                )
                summary = response.choices[0].message.content
            except:
                pass

        return {
            "filename": file.filename,
            "file_type": file_type,
            "size": len(content),
            "content_preview": text_content[:500] + "..." if text_content and len(text_content) > 500 else text_content,
            "summary": summary
        }

    except Exception as e:
        logger.error(f"File upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# Health and Info Endpoints
# ============================================

@app.get("/v1/health")
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "tts": EDGE_TTS_AVAILABLE,
            "stt": STT_AVAILABLE,
            "web_scraping": SCRAPING_AVAILABLE
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{Config.OLLAMA_HOST}/api/tags", timeout=2) as resp:
                status["ollama"] = resp.status == 200
    except:
        status["ollama"] = False

    return status

def count_tokens(text: str, model_name: str = "gpt-3.5-turbo") -> int:
    """Count tokens using tiktoken for more accurate estimation"""
    try:
        encoding = tiktoken.encoding_for_model(model_name)
        return len(encoding.encode(text))
    except:
        # Fallback to rough estimate if tiktoken fails
        return len(text) // 4


if __name__ == "__main__":
    print(f"🚀 Starting Ollama Chatbot API Server v2.0")
    print(f"📡 Ollama backend: {Config.OLLAMA_HOST}")
    print(f"🎯 Default model: {Config.DEFAULT_MODEL}")
    print(f"🌐 API endpoint: http://{Config.HOST}:{Config.PORT}")
    print(f"📝 API docs: http://{Config.HOST}:{Config.PORT}/docs")
    print(f"\n📋 Model Management Endpoints:")
    print(f"   POST /v1/models/switch - Switch model with full config")
    print(f"   GET  /v1/models/active - Get active model")
    print(f"   POST /v1/models/load   - Load model into memory")
    print(f"   POST /v1/models/unload  - Unload model from memory")
    
    uvicorn.run(
        app,
        host=Config.HOST,
        port=Config.PORT,
        log_level=Config.LOG_LEVEL.lower()
    )
