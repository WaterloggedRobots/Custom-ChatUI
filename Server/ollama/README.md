# ChatUI server (Ollama)

A FastAPI server in front of Ollama. It gives the ChatUI client (and any
OpenAI-style client, such as Futu-Network later) one chat endpoint that adds:

- **Native reasoning.** `think` is passed to Ollama and the model's reasoning
  streams back separately as `reasoning_content`, so it never mixes into the
  answer or the saved history. Models that don't support thinking are retried
  without it automatically, and inline `<think>…</think>` output is split out too.
- **Images and files.** Images go to vision models natively. PDF, Word and
  text/code files are converted to text and added to the message.
- **Web search.** `web_search: "auto"` makes one small structured-output call
  to decide whether the question needs the web and which queries to run;
  `"on"` always searches; `"off"` (the default for API callers) never does.
  Queries run in parallel on DuckDuckGo (Wikipedia as fallback), the top pages
  are read in parallel, and the model cites them as `[1]`, `[2]`.

## Run

```bash
cd Server/ollama
pip install -r requirements.txt
DEFAULT_MODEL=gemma4:31b python ollama_server.py      # or ./run.sh
```

It listens on **port 8100** (8000 belongs to the vLLM script). Settings are
environment variables, or a `.env` file in this folder when started with
`run.sh` (`.env` is git-ignored, so keep tokens there):

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama address |
| `DEFAULT_MODEL` | `gemma4:31b` | model used when a request names none |
| `PLANNER_MODEL` | (chat model) | model for the "search or not" step |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8100` | where this server listens |
| `NUM_CTX` | `16384` | context window requested from Ollama |
| `KEEP_ALIVE` | `30m` | how long Ollama keeps a model loaded |
| `SEARCH_RESULTS` / `SEARCH_FETCH_PAGES` | `6` / `3` | results kept / pages read |
| `ENABLE_EXTRAS` | `1` | TTS, STT and the tool-calling test routes |

## Endpoints

| Method | Path | |
|---|---|---|
| POST | `/v1/chat/completions` | OpenAI-style chat, streaming or not |
| GET | `/v1/models?detailed=true` | Ollama models, with capabilities (vision, thinking, tools) |
| POST | `/web_search`, `/v1/web/search` | `{"query", "num_results", "read_pages"}` |
| POST | `/v1/web/browse` | `{"url"}` → page title and text |
| POST | `/v1/files/extract` | multipart upload → the text the model would see |
| POST | `/v1/models/load`, `/v1/models/unload/{model}` | preload or free a model |
| GET | `/health` | Ollama reachability and optional features |
| | `/tts`, `/stt`, `/v1/tools/*` | unchanged extras; function calling is to be reworked later |

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

## Why Python and not C++

The time per request is spent in model inference on the GPU and in waiting on
web pages; the HTTP layer is a few milliseconds of that. A C++ rewrite would not
make answers noticeably faster, but it would mean re-implementing PDF and Word
parsing, HTML extraction and the DuckDuckGo client. The speed-ups that matter
are done here instead: one pooled connection to Ollama, parallel searches and
page reads, models kept loaded (`KEEP_ALIVE`), a larger context window so
Ollama doesn't silently cut the system prompt, and no per-request token counting.

## Tests

```bash
cd Server/ollama
python -m pytest -q tests
```

The tests run the server against a fake Ollama and a fake web, so they need
neither Ollama nor network access.
