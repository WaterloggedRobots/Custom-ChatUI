# Ollama server settings (for auto-linking)

Machine-readable summary of how to reach the home LLM server that runs Ollama.
Written 2026-10-17. Sources are marked on every value:

- **[code]** found in this repository (file named next to it)
- **[memory]** recorded from the user's notes about the home LAN, not in code
- **[inferred]** a reasonable guess that has not been checked on the machine

The ChatUI server code lives on branch `claude/chatui-reasoning-rework-khs4uv`
(PR #1, commit `67292e4`), under `Server/ollama/`. This branch (`iot`) still has the
older client, see "Legacy values" at the end.

## Quick answer

```yaml
llm_server:
  hostname: seamatrix-Machine          # [memory] Ubuntu 24.04
  lan_ip: 192.168.0.192                # [memory] since router reset 2026-10-16 (was 192.168.0.247)
  services:
    chatui_server:                     # FastAPI proxy in front of Ollama; preferred entry point
      base_url: http://192.168.0.192:8100        # [code] Code/UI/network.json, chat_server/config.py
      openai_base_url: http://192.168.0.192:8100/v1
      bind: 0.0.0.0:8100               # [code] SERVER_BIND_IP / SERVER_PORT defaults (LAN-reachable)
      identify: GET / -> {"service": "ChatUI server", ...}; GET /type -> {"type": "ollama"}
      auth: none                       # [code] no API key is checked
    ollama:
      url_on_server: http://localhost:11434      # [code] OLLAMA_URL default
      url_from_lan: http://192.168.0.192:11434   # [inferred] only works if Ollama was started with OLLAMA_HOST=0.0.0.0
      api: native /api/* (tags, ps, show, chat)
      auth: none
    vllm_legacy:
      base_url: http://192.168.0.192:8000/v1     # [code] Server/start_vllm_server.sh; older setup, may not be running
  default_model: gemma4:31b            # [code] DEFAULT_MODEL
other_hosts:
  windows_client: 192.168.0.184        # [memory] the user's PC; never needs to be contacted
  nas: 192.168.0.163                   # [memory] Samba NAS; not used by the server yet
```

## How a bot should link (recommended order)

1. `GET http://192.168.0.192:8100/` and check that `service == "ChatUI server"`.
   If so, use `http://192.168.0.192:8100/v1` as an OpenAI-compatible base URL
   (no API key; send any placeholder if the client library insists on one).
2. If that fails, try Ollama directly: `GET http://192.168.0.192:11434/api/tags`
   returns `{"models": [...]}` when Ollama listens on the LAN. Ollama also serves an
   OpenAI-compatible API at `http://192.168.0.192:11434/v1`.
3. If both fail, the IP may have changed again (it changed once after a router
   reset). Scan `192.168.0.0/24` for port 8100 answering `GET /type` with
   `{"type": "ollama"}`, or ask the user.
4. Pick the model from `GET /v1/models` (field `default` gives the server's default);
   do not hard-code it, the installed models change.

## ChatUI server (port 8100)

Started with `Server/ollama/run.sh` (or `.venv/bin/python ollama_server.py`) from the
clone at `/home/seamatrix/snap/gitpeach-desktop/13/GitHub/Custom-ChatUI` [memory].
`run.sh` also starts `ollama serve` if `GET $OLLAMA_URL/api/tags` does not answer [code].

### Endpoints [code: chat_server/app.py]

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | service name, version, endpoint list (use to identify the server) |
| GET | `/type` | `{"type": "ollama"}` |
| GET | `/health`, `/v1/health` | `{"status": "healthy"/"degraded", "ollama": bool, "ollama_url", "default_model", "features"}` |
| GET | `/v1/models?detailed=true` | models Ollama has now, with capabilities (vision, thinking, tools), size, `loaded`; top-level `default` |
| POST | `/v1/chat/completions` | OpenAI-style chat, streaming or not |
| POST | `/v1/models/load?model=…&keep_alive=…` | preload a model |
| POST | `/v1/models/unload/{model}` | free a model |
| POST | `/web_search`, `/v1/web/search` | `{"query", "num_results", "read_pages"}` |
| POST | `/v1/web/browse` | `{"url"}` → page title and text |
| POST | `/v1/files/extract` | multipart upload → extracted text |
| | `/tts`, `/stt`, `/v1/tools/*` | optional extras (`ENABLE_EXTRAS=1`) |

Extra chat request fields beyond OpenAI's: `think` (`true`/`false` or `"low"|"medium"|"high"`),
`web_search` (`"auto"|"on"|"off"`, default `"off"` for API callers), `num_ctx`, and `file`
content parts. Streamed chunks carry `delta.reasoning_content`, `delta.content`, and
top-level `status` and `sources`.

Minimal check:

```bash
curl -s http://192.168.0.192:8100/health
curl -s http://192.168.0.192:8100/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma4:31b","messages":[{"role":"user","content":"ping"}],"stream":false}'
```

## Environment variables [code: Server/ollama/.env.example, chat_server/config.py]

Set in `Server/ollama/.env` on the server (git-ignored; values below are the defaults).
The real `.env` on the machine has not been read, so actual values may differ [inferred].

| Variable | Default | Meaning |
|---|---|---|
| `SERVER_BIND_IP` (old: `API_HOST`) | `0.0.0.0` | where the ChatUI server listens |
| `SERVER_PORT` (old: `API_PORT`) | `8100` | ChatUI server port (8000 is the vLLM script's) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama address; `OLLAMA_HOST` is used only if it is a full `http…` URL |
| `DEFAULT_MODEL` | `gemma4:31b` | model used when a request names none |
| `PLANNER_MODEL` | (empty = chat model) | model for the "search or not" step |
| `NUM_CTX` | `16384` | context window requested from Ollama |
| `KEEP_ALIVE` | `30m` | how long Ollama keeps a model loaded |
| `SEARCH_RESULTS` / `SEARCH_FETCH_PAGES` / `SEARCH_PAGE_CHARS` | `6` / `3` / `3000` | web search limits |
| `MAX_ATTACHMENT_BYTES` / `FILE_TEXT_CHARS` | `26214400` / `40000` | attachment limits |
| `ENABLE_EXTRAS` | `1` | TTS, STT, tool-calling test routes |
| `LOG_LEVEL` | `INFO` | logging |
| `NAS_IP` / `NAS_MOUNT` | `192.168.0.163` / `/mnt/nas` in `.env.example` | reserved, unused |
| `ESP_API_URL` / `ESP_API_TOKEN` / `MERCURY_MAC` | (empty) | Mercury power tool (`tools/mercury.py`) |

## Ollama itself

- Runs as the `ollama` systemd service on the server, port `11434` [code: run.sh comment; port is Ollama's default].
- Whether it accepts LAN connections depends on `OLLAMA_HOST` in that service; this is not
  recorded anywhere [inferred: probably localhost only, since the ChatUI server is the
  documented LAN entry point]. To open it: `sudo systemctl edit ollama`, add
  `Environment="OLLAMA_HOST=0.0.0.0"`, then `sudo systemctl restart ollama`.
- Installed models: not recorded; query `GET /api/tags` (Ollama) or `GET /v1/models` (ChatUI server).

## Client config files [code]

| File (branch) | Value |
|---|---|
| `Code/UI/network.json` (rework branch) | `llm_server.ip = 192.168.0.192`, `llm_server.port = 8100` |
| Windows client user override | saved by *Server > Set IP* under `%APPDATA%\Waterlogged\ChatUI` [memory] |

## Legacy values on this branch (`iot`) — do not use

- `Code/UI/config.json`: `ip = 192.168.0.247` (address before the 2026-10-16 router reset).
- `Code/llmClient.py`: port `8000` (the old vLLM server), admin route `:9000/admin/switch_model`.
