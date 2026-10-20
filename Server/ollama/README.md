# ChatUI server (Ollama)

A FastAPI server in front of Ollama. It gives the ChatUI client (and any
OpenAI-style client, such as Futu-Network later) one chat endpoint that adds:

- **Native reasoning.** `think` is passed to Ollama and the model's reasoning
  streams back separately as `reasoning_content`, so it never mixes into the
  answer or the saved history. Models that don't support thinking are retried
  without it automatically, and inline `<think>…</think>` output is split out too.
- **Images and files.** Images go to vision models natively. PDF, Word and
  text/code files are converted to text and added to the message.
- **Web search.** With `web_search: "auto"` the model decides itself: a fixed
  system note lets it reply with only `<search>query | query</search>`, and the
  server then stops that reply, runs the searches and asks again with the
  results. Questions that don't need the web cost no extra call. `"on"` always
  searches; `"off"` (the default for API callers) never does.
  Queries run in parallel on DuckDuckGo (Wikipedia as fallback), the top pages
  are read in parallel, and the model cites them as `[1]`, `[2]`.
- **Speech to text.** The GUI's 🎤 button records the microphone and sends the
  clip to `/v1/audio/transcriptions`; local Whisper (faster-whisper) turns it
  into text on this machine. See [Speech to text](#speech-to-text).
- **Text to speech.** The GUI's ▶ Play buttons send a reply here with the
  chat's engine, voice and speed and play the audio that comes back: Microsoft
  Edge's online voices or Piper running offline on this machine. See
  [Text to speech](#text-to-speech).

## Setup (Ubuntu 24.04)

Run it straight from the git clone; no Docker is needed. The only
prerequisites are Python 3.10+ and Ollama with at least one model pulled.
Ubuntu 24.04 blocks `pip install` into the system Python, so use a virtual
environment inside the clone (`.venv/` is git-ignored):

```bash
sudo apt install python3-venv          # once
cd ~/Custom-ChatUI/Server/ollama        # wherever you cloned it
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests     # optional: no Ollama or network needed
```

## Run

```bash
ollama pull gemma4:31b                  # or any model you prefer
.venv/bin/python ollama_server.py       # Ollama must already be running
# or: ./run.sh   (starts Ollama only if it is not running, and refuses if port 8100 is taken)
```

`run.sh` uses `.venv` when it exists, and falls back to `~/vllm-env` otherwise.
After a `git pull`, restart the server; re-run the `pip install` line only if
`requirements.txt` changed.

It listens on **port 8100** (8000 belongs to the vLLM script). Settings are
environment variables, or a `.env` file in this folder when started with
`run.sh`. Copy `.env.example` to `.env` to start; `.env` is git-ignored, so
keep tokens there. All addresses are summarised at the top of
`chat_server/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama address |
| `DEFAULT_MODEL` | `gemma4:31b` | model used when a request names none |
| `PLANNER_MODEL` | (chat model) | model for the "search or not" step |
| `SERVER_BIND_IP` / `SERVER_PORT` | `0.0.0.0` / `8100` | where this server listens (`API_HOST`/`API_PORT` still work) |
| `NAS_IP` / `NAS_MOUNT` | (empty) | NAS address, kept for later; nothing uses it yet |
| `NUM_CTX` | `16384` | context window requested from Ollama |
| `KEEP_ALIVE` | `30m` | how long Ollama keeps a model loaded |
| `SEARCH_RESULTS` / `SEARCH_FETCH_PAGES` | `6` / `3` | results kept / pages read |
| `STT_ENGINE` | `auto` | speech to text: `whisper` (local), `google` (internet), `off`; `auto` = whisper if installed |
| `STT_MODEL` / `STT_DEVICE` | (auto) / `auto` | Whisper model and `cuda`/`cpu`; auto = `large-v3-turbo` on the GPU, `small` on the CPU |
| `STT_PRELOAD` | `0` | `1` loads Whisper at start-up instead of on the first clip |
| `TTS_ENGINE` / `TTS_VOICE` | `edge` / `en-US-JennyNeural` | engine and Edge voice used when the GUI asks for "Server default" |
| `TTS_PIPER_DIR` | `Server/ollama/voices` | where Piper voices (`.onnx` + `.onnx.json`) are kept |
| `ENABLE_EXTRAS` | `1` | the tool-calling test routes |

## Endpoints

| Method | Path | |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-style chat, streaming or not |
| GET | `/v1/models?detailed=true` | the models Ollama has right now, with capabilities (vision, thinking, tools), size and whether each is loaded |
| POST | `/web_search`, `/v1/web/search` | `{"query", "num_results", "read_pages"}` |
| POST | `/v1/web/browse` | `{"url"}` → page title and text |
| POST | `/v1/files/extract` | multipart upload → the text the model would see |
| POST | `/v1/models/load`, `/v1/models/unload/{model}` | preload or free a model |
| POST | `/v1/audio/transcriptions` (also `/stt`) | multipart `file` (+ optional `language`, `prompt`, `response_format=text`) → `{"text", "language", "duration", "engine"}` |
| GET | `/v1/audio/stt` | whether speech to text is available, and which engine/model |
| POST | `/v1/audio/speech` (also `/tts`) | `{"input", "model": "edge"\|"piper", "voice", "speed"}` → mp3 (Edge) or wav (Piper) |
| GET | `/v1/audio/voices` | the engines and their voices `{"engines", "voices": [{"id", "engine", "language", "name"}]}` |
| POST | `/v1/audio/voices/download` | `{"voice": "en_US-lessac-medium"}` fetches a Piper voice |
| GET | `/health` | Ollama reachability and optional features |
| | `/v1/tools/*` | unchanged extras; function calling is to be reworked later |

### Chat request

Standard fields (`model`, `messages`, `stream`, `temperature`, `top_p`,
`max_tokens`, `stop`, `seed`, `response_format`) work as in OpenAI's API.
`response_format` with a JSON schema becomes Ollama structured output. Extra fields:

```json
{
  "model": "gemma4:31b",
  "think": true,
  "web_search": "auto",
  "num_ctx": 32768,
  "messages": [
    {"role": "user", "content": [
      {"type": "text", "text": "Summarise the report and the chart"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
      {"type": "file", "file": {"filename": "report.pdf", "file_data": "data:application/pdf;base64,..."}}
    ]}
  ]
}
```

`think` can also be `"low"`, `"medium"` or `"high"` for gpt-oss; OpenAI's
`reasoning_effort` is accepted as well. Streamed chunks carry
`delta.reasoning_content`, `delta.content`, and two extra top-level keys:
`status` (progress text such as `"Searching: …"`) and `sources`
(`[{"n", "title", "url"}]`). The last chunk has `usage` with Ollama's real
token counts.

`tools` (OpenAI function tools) are passed to Ollama, but the server never runs
them. When the model calls one, the stream carries `delta.tool_calls` (OpenAI
shape, arguments as a JSON string, with an `id`) and ends with
`finish_reason: "tool_calls"`. The caller runs the tools and sends the results
in its next request as `{"role": "tool", "tool_call_id", "name", "content"}`
after the assistant message that holds the `tool_calls`. The ChatUI client uses
this for its file, git and terminal tools, which act on the Windows PC. A model
that can't call tools is retried without them.

## Speech to text

`pip install -r requirements.txt` installs faster-whisper. The model downloads
from Hugging Face the first time a clip arrives (`large-v3-turbo` is about
1.6 GB; `small` about 0.5 GB) and is cached in `~/.cache/huggingface`, so the
first clip takes a while and later ones take a second or two. Set
`STT_PRELOAD=1` to do that at start-up instead.

On the GPU, faster-whisper needs CUDA 12 cuBLAS and cuDNN 9. With
`STT_DEVICE=auto` the server first tries the GPU in a separate process (a missing
cuDNN would otherwise crash the server) and falls back to the CPU by itself; the
log then says `Whisper can't run on the GPU`. To use the GPU, install the
libraries into the venv (`run.sh` then puts them on the library path itself;
when starting `ollama_server.py` by hand, export the path first):

```bash
.venv/bin/pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
export LD_LIBRARY_PATH=$(.venv/bin/python -c 'import nvidia.cublas.lib as b, nvidia.cudnn.lib as d; print(b.__path__[0] + ":" + d.__path__[0])')
```

Whisper shares the GPU with Ollama; `large-v3-turbo` needs about 2 GB of VRAM.
Language is detected per clip unless the GUI sets one. Check it by hand:

```bash
curl -F file=@clip.wav http://localhost:8100/v1/audio/transcriptions
```

## Text to speech

Two engines; the GUI picks one per chat (Chat Settings > Voice):

- **edge** (default): Microsoft Edge's online voices through `edge-tts`, which
  `requirements.txt` installs. Natural voices in most languages, Cantonese
  (`zh-HK-HiuGaaiNeural`, `zh-HK-WanLungNeural`, ...) included. It needs this
  machine to reach the internet; nothing is downloaded or set up.
- **piper**: runs here, offline and fast even on the CPU, but each voice is a
  file to download once. Install it and fetch voices into `voices/` (the list
  of names is at https://huggingface.co/rhasspy/piper-voices):

  ```bash
  .venv/bin/pip install piper-tts
  .venv/bin/python -m piper.download_voices en_US-lessac-medium zh_CN-huayan-medium --download-dir voices
  ```

  or, with the server running,
  `curl -X POST localhost:8100/v1/audio/voices/download -H 'Content-Type: application/json' -d '{"voice": "en_US-lessac-medium"}'`.
  Restart nothing: new voices show up in the GUI's list the next time it is opened.

Markdown, code blocks, links and citation numbers are removed before reading.
Check it by hand:

```bash
curl -X POST localhost:8100/v1/audio/speech -H 'Content-Type: application/json' \
     -d '{"input": "Hello there", "model": "edge", "voice": "en-GB-SoniaNeural"}' -o hello.mp3
```

## Why Python and not C++

The time per request is spent in model inference on the GPU and in waiting on
web pages; the HTTP layer is a few milliseconds of that. A C++ rewrite would not
make answers noticeably faster, but it would mean re-implementing PDF and Word
parsing, HTML extraction and the DuckDuckGo client. The speed-ups that matter
are done here instead: one pooled connection to Ollama, parallel searches and
page reads, models kept loaded (`KEEP_ALIVE`), a larger context window so
Ollama doesn't silently cut the system prompt, and no per-request token counting.
Old messages are dropped in blocks (here and in the client) rather than one per
turn, so the start of the prompt stays the same and Ollama reuses its prompt
cache instead of re-reading the whole chat on every message. Each answer logs a
`timing:` line showing model load, prompt reading and generation.

## Tests

```bash
cd Server/ollama
python -m pytest -q tests
```

The tests run the server against a fake Ollama and a fake web, so they need
neither Ollama nor network access.
