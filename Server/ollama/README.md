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
  Edge's online voices, or Kokoro, Chatterbox or Piper running on this machine. See
  [Text to speech](#text-to-speech).
- **Code sandbox.** The model tests code in a throwaway Ubuntu container on
  this machine before the user runs it for real: one locked-down Docker
  container per chat, with no network. See [Code sandbox](#code-sandbox).

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
| `NUM_CTX_MODELS` | (empty) | per-model context windows, e.g. `xortron=4096, qwen3.6:35b-a3b=32768`. A model not listed uses the `num_ctx` in its own Modelfile, or `NUM_CTX` if it has none. A chat request's own `num_ctx` beats both |
| `KEEP_ALIVE` | `30m` | how long Ollama keeps a model loaded |
| `SEARCH_RESULTS` / `SEARCH_FETCH_PAGES` | `6` / `3` | results kept / pages read |
| `STT_ENGINE` | `auto` | speech to text: `whisper` (local), `google` (internet), `off`; `auto` = whisper if installed |
| `STT_MODEL` / `STT_DEVICE` | (auto) / `auto` | Whisper model and `cuda`/`cpu`; auto = `large-v3-turbo` on the GPU, `small` on the CPU |
| `STT_PRELOAD` | `0` | `1` loads Whisper at start-up instead of on the first clip |
| `TTS_ENGINE` / `TTS_VOICE` | `edge` / `en-US-JennyNeural` | engine and Edge voice used when the GUI asks for "Server default" |
| `TTS_PIPER_DIR` | `Server/ollama/voices` | where Piper voices (`.onnx` + `.onnx.json`) are kept |
| `TTS_KOKORO_DIR` / `TTS_KOKORO_MODEL` | `voices/kokoro` / `kokoro-v1.0.onnx` | Kokoro model files (downloaded by themselves) |
| `TTS_CHATTERBOX_DIR` / `TTS_CHATTERBOX_LANGUAGES` | `voices/chatterbox` / `en,zh,ja` | voice clips for Chatterbox to copy, and the languages offered |
| `TTS_SOVITS_URL` / `TTS_SOVITS_HOME` / `TTS_SOVITS_DIR` | `http://127.0.0.1:9880` / `~/GPT-SoVITS` / `voices/gpt-sovits` | GPT-SoVITS's API, its clone (trained files), and the reference clips |
| `TTS_SOVITS_LANGUAGES` | `ja,zh,en,zh-HK,ko` | languages each GPT-SoVITS voice is offered in |
| `TTS_PRELOAD` | `kokoro` | engines loaded at start-up (`kokoro,chatterbox`, or empty) |
| `TTS_TRANSLATE_MODEL` | (the chat's model) | Ollama model that translates replies for "speak in the voice's language" |
| `SANDBOX_ENABLED` / `SANDBOX_IMAGE` | `1` / `chatui-sandbox` | the code sandbox, and the Docker image it runs |
| `SANDBOX_NETWORK` | `none` | `bridge` lets code in the sandbox reach the internet (pip, npm) |
| `SANDBOX_MEMORY` / `SANDBOX_CPUS` / `SANDBOX_PIDS` / `SANDBOX_DISK` | `2g` / `2` / `256` / `1g` | limits for each chat's container (`/work` is kept in memory) |
| `SANDBOX_TIMEOUT` / `SANDBOX_MAX_TIMEOUT` | `180` / `900` | seconds one run gets, and the most the model may ask for |
| `SANDBOX_OUTPUT_CHARS` | `12000` | output kept from one run (its start and end) |
| `SANDBOX_IDLE_MINUTES` / `SANDBOX_MAX_SESSIONS` | `30` / `4` | an unused chat's container is removed; containers at once |
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
| POST | `/v1/audio/speech` (also `/tts`) | `{"input", "model": "edge"\|"kokoro"\|"chatterbox"\|"piper", "voice", "speed", "translate", "translate_model"}` → mp3 (Edge) or wav; with `translate`, the `X-Spoken-Language` and `X-Spoken-Text` (URL-encoded) headers carry the translation |
| GET | `/v1/audio/voices` | the engines and their voices `{"engines", "voices": [{"id", "engine", "language", "name"}]}` |
| POST | `/v1/audio/voices/download` | `{"voice": "en_US-lessac-medium"}` fetches a Piper voice |
| POST | `/v1/images/generations` | `{"prompt", "negative_prompt", "workflow", "seed"}` → `{"data": [{"b64_json", "mime", "filename", ...}]}`, drawn by ComfyUI |
| GET | `/v1/images/workflows` | the workflows in `workflows/` `{"data": [{"id", "name", "usable", "error"}], "comfyui": true\|false}` |
| POST | `/v1/sandbox/run` | `{"session", "language", "code", "files", "archive", "command", "timeout"}` → `{"exit_code", "output", "timed_out", "truncated", "seconds", "command"}`; see [Code sandbox](#code-sandbox) |
| POST | `/v1/sandbox/reset` | `{"session"}` throws that chat's container away |
| GET | `/v1/sandbox/status` | whether the sandbox can run, its image, network and limits |
| GET | `/health` | Ollama reachability and optional features |
| GET | `/v1/rea/tools` | REA's allowed tools in OpenAI `tools` form, plus `meta` (each one's kind and group). Needs `X-ChatUI-Token` |
| POST | `/v1/rea/call` | `{"name", "arguments"}` → `{"name", "output"}`, output clipped to 20,000 characters. Needs the token |
| GET | `/v1/rea/status` | whether REA is running, how often it was started, its last error. Needs the token |
| GET, POST | `/v1/ubuntu/tools`, `/v1/ubuntu/call` | the same for the Ubuntu file and run tools; `tools` also returns the allowed folders (`roots`) |
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
`status` (progress text such as `"Searching: …"`), `sources`
(`[{"n", "title", "url"}]`) and `images` (see Pictures below). The last chunk has `usage` with Ollama's real
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

The GUI picks an engine and voice per chat (Chat Settings > Voice). Engines
that aren't installed show up there as "not installed on the server".

| Engine | Runs | Sounds | Languages | Setup |
|---|---|---|---|---|
| `edge` (default) | Microsoft's servers (needs internet) | natural | 100+ incl. Cantonese `zh-HK` | none (in `requirements.txt`) |
| `kokoro` | here, CPU is fine | natural; ★ voices are the best | English (US/UK), Mandarin, Japanese, Spanish, French, Hindi, Italian, Portuguese | `pip install kokoro-onnx` |
| `chatterbox` | here, wants an NVIDIA GPU | the most human; copies any voice from a clip | 23, set by `TTS_CHATTERBOX_LANGUAGES` | `pip install chatterbox-tts` (large) |
| `piper` | here, very light | plainer | many, one file per voice | `pip install piper-tts` + download voices |
| `gpt-sovits` | here, its own program, NVIDIA GPU | closest to a real person: voices you **train** on your own audio | Japanese, Mandarin, English, Cantonese, Korean | install GPT-SoVITS and run its `api_v2.py` (below) |

**Kokoro** (82M parameters, Apache 2.0). Install it and restart the server:

```bash
.venv/bin/pip install kokoro-onnx
```

Its model (`kokoro-v1.0.onnx`, 310 MB, plus `voices-v1.0.bin`, 28 MB) downloads
from GitHub into `voices/kokoro/` by itself when the server starts
(`TTS_PRELOAD=kokoro`), so the first Play doesn't wait. 54 voices; the model
card rates `af_heart`, `af_bella`, `bf_emma`, `ff_siwis`, `jf_alpha` and
`zf_xiaoxiao` best (★ in the list). `TTS_KOKORO_MODEL=kokoro-v1.0.int8.onnx`
uses the 90 MB version, a little rougher. No Cantonese: use Edge's `zh-HK` voices for that.

**Chatterbox** (Resemble AI, MIT). The most natural of the four, and it can
speak in *any* voice: put a clean 5 to 20 second recording of someone speaking
(`.wav`, `.mp3`, `.flac`, ...) into `voices/chatterbox/`, and it shows up in the
voice list under the file's name, once per language. Without clips there is a
built-in default voice. English uses the English model; other languages use
the multilingual one (both download from Hugging Face on first use, a few GB).
It installs PyTorch, so it is a big install, and it is slow without an NVIDIA
GPU. Speed can't be changed for it.

```bash
.venv/bin/pip install chatterbox-tts
# TTS_PRELOAD=kokoro,chatterbox in .env loads it at start-up instead of on the first Play
```

*Chatterbox on a new GPU.* `chatterbox-tts` installs a PyTorch built for
CUDA 12.4, which has no code for the newest cards (RTX 50xx, `sm_120`):
loading fails with "no kernel image is available for execution on the
device". The server then logs a warning and runs Chatterbox on the CPU
(slow). To use the GPU, check what torch was built for, then install a build
for CUDA 12.8 (pip warns that chatterbox asked for an older torch; that's fine):

```bash
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.get_device_name(), torch.cuda.get_device_capability(), torch.cuda.get_arch_list())"
.venv/bin/pip install --upgrade torch torchaudio --index-url https://download.pytorch.org/whl/cu128
```

`TTS_CHATTERBOX_DEVICE=cpu` in `.env` skips the GPU on purpose.

**Piper**: light and offline, plainer voices; each voice is a file to fetch
once into `voices/` (names at https://huggingface.co/rhasspy/piper-voices):

```bash
.venv/bin/pip install piper-tts
.venv/bin/python -m piper.download_voices en_US-lessac-medium zh_CN-huayan-medium --download-dir voices
```

or, with the server running,
`curl -X POST localhost:8100/v1/audio/voices/download -H 'Content-Type: application/json' -d '{"voice": "en_US-lessac-medium"}'`.

**GPT-SoVITS** (github.com/RVC-Boss/GPT-SoVITS, MIT). You train a voice from
1 to 10 minutes of clean recordings of one person, on the GPU, in its own web
UI; then this server speaks with it. It's a separate program with its own
Python, so its big dependencies stay out of this server's `.venv`. Install it
once next to this repo (`~/GPT-SoVITS`, or set `TTS_SOVITS_HOME`); `CU128` is
the build for RTX 50xx cards:

```bash
cd ~ && git clone https://github.com/RVC-Boss/GPT-SoVITS.git && cd GPT-SoVITS
conda create -n GPTSoVits python=3.10 -y && conda activate GPTSoVits   # Miniconda, if you don't have conda
bash install.sh --device CU128 --source HF
```

*Train a voice* (once per voice): `python webui.py en` opens the training UI
at http://SERVER:9874. In tab 0, give it the folder with your recordings, then
slice, (denoise), ASR and proofread the transcripts. In tab 1, give the
experiment a name, say `Mio`, then format the data, train SoVITS, and train
GPT. It saves `SoVITS_weights_*/Mio_e*_s*.pth` and `GPT_weights_*/Mio-e*.ckpt`.
Close the web UI when you're done, since it holds GPU memory.

*Give it a reference clip:* copy one of the sliced clips (3 to 10 seconds, from
`output/slicer_opt/`) to this server's `voices/gpt-sovits/Mio.wav`, and put what
it says in `voices/gpt-sovits/Mio.txt`. The names must match the experiment.
The clip's language is guessed from the text; `Mio.json` with
`{"language": "ja"}` sets it (ja, zh, en, zh-HK, ko), and `"gpt"`/`"sovits"`
paths pick other trained files than the newest. A clip with no trained files
works too: GPT-SoVITS then copies it with whichever models it has loaded,
less closely.

*Run its API* while the chat server runs. `run.sh` starts it in its own terminal
if it finds GPT-SoVITS (`TTS_SOVITS_HOME`, `~/GPT-SoVITS` or next to this repo) and
its Python (`TTS_SOVITS_PYTHON`, by default the `GPTSoVits` conda environment);
`TTS_SOVITS_AUTOSTART=0` leaves it off, as it keeps a few GB of GPU memory. By hand:

```bash
cd ~/GPT-SoVITS && conda activate GPTSoVits
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

The voices show up in Chat Settings under GPT-SoVITS as `Mio (Japanese,
trained)`, once per language in `TTS_SOVITS_LANGUAGES`. A voice can speak a
language other than its clip's, e.g. a Japanese voice speaking English. The GUI
has a reply in another language translated first, so `Mio|ja` always speaks Japanese. The server
loads the voice's trained files into GPT-SoVITS when a chat switches voice
(a few seconds), then keeps them loaded.

**Speaking another language.** With `"translate": true` the text is first
translated by an Ollama model into the language of the voice (`ja-JP` for
`ja-JP-NanamiNeural` or `jf_alpha`, written Cantonese for `zh-HK` voices, the
part after `|` for Chatterbox), then read. The GUI sends the chat's own model,
which is usually already loaded, so this costs one short extra answer;
`TTS_TRANSLATE_MODEL` picks a different (e.g. smaller) one.

New GPT-SoVITS voices show up in Chat Settings within a few seconds, and so does
the engine itself once its API is started. Other engines' voices are read when the
GUI first lists them, so a restart of the GUI picks up new ones.
Markdown, code blocks, links and citation numbers are removed before reading.
Check an engine by hand:

```bash
curl -X POST localhost:8100/v1/audio/speech -H 'Content-Type: application/json' \
     -d '{"input": "Hello there", "model": "kokoro", "voice": "af_heart"}' -o hello.wav
```

## Pictures (ComfyUI)

ComfyUI runs on this same machine (default `http://127.0.0.1:8188`, set
`COMFYUI_URL` in `.env` otherwise). Start it as usual, e.g.
`python main.py --listen 127.0.0.1` in the ComfyUI folder; nothing extra is
installed here, the server only uses ComfyUI's HTTP API.

A chat picks a workflow in **Chat Settings > Pictures**. The workflows are the
API-format `.json` files in `workflows/` (see `workflows/README.md`: in
ComfyUI, *Workflow > Export (API)*, with `{{positive}}` and `{{negative}}` in
the prompt boxes). New files show up without a restart.

It works like auto web search: with `"image_workflow": "<id>"` in a chat
request, a fixed system note tells the model it can put

```
<image>
positive: a cosy café at night, warm light, watercolour
negative: blurry, text
</image>
```

anywhere in its reply when a picture fits (`"image_mode": "on"` asks for one
with every reply). The block is cut out of the streamed text; when the answer
is done the server queues the workflow, waits for it, and sends the picture in
a chunk `{"images": [{"b64_json", "mime", "prompt", "negative_prompt",
"workflow"}]}` (or `{"error", "prompt", ...}` if ComfyUI failed). At most
`COMFYUI_MAX_IMAGES` pictures per reply.

One GPU for both Ollama and ComfyUI: if a big chat model and the image model
don't fit together, set `COMFYUI_UNLOAD_OLLAMA=1` (the chat model is unloaded
before drawing and reloads on the next message) and/or `COMFYUI_FREE_AFTER=1`
(ComfyUI frees its models after each picture).

```bash
curl -X POST localhost:8100/v1/images/generations -H 'Content-Type: application/json' \
     -d '{"prompt": "a red fox in snow", "workflow": "sd15-basic"}' | head -c 200
```

## Code sandbox

With **Run** turned on in Chat Settings, the model gets a `run_in_sandbox` tool
(Code/local_tools/sandbox.py). It runs code in an Ubuntu 24.04 container on this
machine, not on the user's PC, so it isn't asked for approval, and the model is
told to test code there before handing it over. The container has python3 (with
pytest, numpy and requests), node, gcc/g++, make and git. The model can send:

- `code` + `language` (python, bash, javascript, c, cpp): saved as `main.<ext>` in `/work` and run
- `command`: any bash command in `/work`, such as `pytest -q`
- `files`: more files to write first
- `copy_from_pc`: files or folders from the user's PC, copied in (without `.git`, `node_modules`, venvs, build output)

Each chat has its own container, started on its first run and kept between runs,
so files stay in `/work` until `reset_sandbox`, 30 idle minutes, or a server
restart. Nothing comes back out to the PC.

How it is locked down (chat_server/sandbox.py):

- no network (`SANDBOX_NETWORK=none`); set `bridge` if the model should `pip install`
- 2 GB of memory, 2 CPUs, 256 processes; no Linux capabilities, `no-new-privileges`, a normal user
- a read-only system; `/work`, `/tmp` and the home folder are in memory, so nothing lands on this disk
- nothing from this machine is mounted into it
- each run has a time limit (`timeout -k`), and only 12,000 characters of output are kept

Like the REA and Ubuntu tools, every `/v1/sandbox/*` request needs the shared
token in `X-ChatUI-Token` (see below), so only clients that have it can run code.
The sandbox is on in a chat while **Run** or **Ubuntu Run** is, and the model is
told to try new code there before running it on the PC or with `ubuntu_run_command`.

Setup, once (Docker must be installed and your user in the `docker` group):

```bash
docker build -t chatui-sandbox sandbox          # from Server/ollama; again after changing sandbox/Dockerfile
curl -s localhost:8100/v1/sandbox/status
curl -s -X POST localhost:8100/v1/sandbox/run -H 'Content-Type: application/json' \
     -d '{"session": "try", "language": "python", "code": "print(2 + 2)"}'
```

Windows isn't covered yet; a Windows VM over SSH could sit behind the same API later.

## Reverse engineering, Ubuntu files and run (for the client's model)

Three groups of tools run on this machine for the chat model on the Windows
client, each switched on per chat in the client (*Reverse engineering*,
*Ubuntu files*, *Ubuntu run*). The client asks the user before every change, as it does
for its own tools; reads run straight away.

- **REA** (`chat_server/rea.py`). One long-lived REA MCP server
  (`npx -y rea-agents@6.5.0 mcp`, `REA_COMMAND`) started on the first call,
  through the Python `mcp` client. Calls go one at a time; REA is started again
  if it dies, or if a call takes longer than `REA_TIMEOUT` (60 minutes: the
  first Ghidra analysis of a big binary is slow). Only 16 read-only tools are
  offered: `binary_overview`, `open_binary`, `binary_session`, `close_binary`,
  `list_procedures`, `search_procedures`, `procedure_pseudo_code`,
  `procedure_callers`, `procedure_callees`, `procedure_info`, `xrefs`,
  `search_strings`, `list_strings`, `inspect_managed_artifact`,
  `inspect_managed_members`, `trace_feature`. No debugger, Frida, rizin,
  Cutter, adb, capture or annotation tool is ever exposed. Three helpers come
  with them: `list_targets`, `copy_to_targets` (from
  `/mnt/nas/REA/Game File Copy/...` into `REA/targets/`) and `export_assets`
  (`REA/scripts/export-assets.sh`, to `/mnt/nas/REA/<destination>`).
  `open_binary` refuses the NAS game copies, so REA always works on a copy.
  Results start with a line saying they are target data, not instructions.
  REA's own output goes to `logs/rea.log`.
- **Ubuntu files** (`chat_server/ubuntu.py`): `ubuntu_list_directory`,
  `ubuntu_read_file`, `ubuntu_find_files`, `ubuntu_search_in_files`,
  `ubuntu_write_file`, `ubuntu_edit_file`, `ubuntu_create_folder`,
  `ubuntu_move_path`, `ubuntu_copy_path` and `ubuntu_delete_path`, which moves
  to a `.chatui-trash` folder at the top of the allowed folder (`~/.chatui-trash`,
  `/mnt/nas/.chatui-trash`) instead of deleting.
- **Ubuntu run** (`ubuntu_run_command` in the same file): one bash command
  (`bash -lc`) in a folder inside the allowed ones, with its output and exit
  code. It runs as the server's user, in its own process group: a timeout
  (180 s by default, at most `TOOL_RUN_MAX_TIMEOUT`) stops everything it
  started. The folder rules below only pick where it starts; what the command
  touches is up to you, which is why the client asks before every one.
  `TOOL_TOKEN` is taken out of its environment.

**Setup.** Port 8100 is open to the LAN, so all three groups need a shared token,
and refuse everything until one is set:

```bash
.venv/bin/pip install -r requirements.txt       # adds the mcp package
mkdir -p ~/.config/chatui && openssl rand -hex 24 > ~/.config/chatui/tool_token
chmod 600 ~/.config/chatui/tool_token
cat ~/.config/chatui/tool_token                   # paste this into the client
```

(or set `TOOL_TOKEN` in `.env`). Restart the server, then paste the token in
the client under **Settings > General > Ubuntu server** and press *Test*. REA
also needs Node (for `npx`), Java 21 and Ghidra; the server finds them through
`JAVA_HOME` and `GHIDRA_INSTALL_DIR`, or under `REA_HOME/tools/ghidra_*` and
`/usr/lib/jvm/java-21-openjdk-*` when those aren't set.

**Which folders.** Every path must resolve (symlinks followed) inside
`TOOL_ROOTS` (default: the home folder and the NAS, `NAS_MOUNT` or `/mnt/nas`). Inside them,
`TOOL_DENY` is never touched (`~/.ssh`, `~/.gnupg`, `~/.aws`, git and GitHub
credentials, `~/.config/chatui`, `~/.claude.json`, this folder's `.env`), and
`TOOL_READ_ONLY` (`/mnt/nas/REA/Game File Copy`) is only read. Paths are
Ubuntu paths (`/home/...`, `~/...`); Windows paths are refused with a reason.

**Per chat.** `TOOL_ROOTS` is what you open up; each chat then picks from it.
With *Ubuntu files* or *Ubuntu run* on, the chat window shows 🖥: it browses the
allowed folders on the server (`GET /v1/ubuntu/folders`, nothing is mounted on
the PC) and links the one you pick to the chat, and its ▾ lists them. The linked
folders go with every `ubuntu_*` call, and the server keeps the tools inside
them (`server_tools.LINKED`): other paths are refused, and a call without a
path starts in the first one. A chat with none linked may use all of
`TOOL_ROOTS`, as before. REA isn't narrowed by them. Folders in
`TOOL_QUICK_FOLDERS` (default: `~/snap/gitpeach-desktop/13/GitHub`, the GitHub
clones) are shown first in the picker as ★ shortcuts, when they're inside a root.

| Setting | Default | |
|---|---|---|
| `TOOL_TOKEN` / `TOOL_TOKEN_FILE` | `~/.config/chatui/tool_token` | the shared token; none = the tools are off |
| `TOOL_ROOTS` | `~:/mnt/nas` | folders the tools may use, `:`-separated; each chat can link some of them |
| `TOOL_QUICK_FOLDERS` | `~/snap/gitpeach-desktop/13/GitHub` | ★ shortcuts at the top of the client's server folder picker, `:`-separated |
| `TOOL_DENY`, `TOOL_READ_ONLY` | see above | never touched; only read |
| `TOOL_OUTPUT_CHARS` | 20000 | what one result may send back |
| `TOOL_RUN_MAX_TIMEOUT` | 5400 | seconds one command may run at most |
| `REA_HOME` | `~/snap/gitpeach-desktop/13/GitHub/REA` | the REA repository (its `targets/` and `scripts/`) |
| `REA_COMMAND` | `npx -y rea-agents@6.5.0 mcp` | keep in step with `REA_VERSION` in REA's `scripts/setup-rea.sh` |
| `REA_TIMEOUT`, `REA_START_TIMEOUT`, `REA_EXPORT_TIMEOUT` | 3600, 180, 3600 | seconds |

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

The tests run the server against a fake Ollama, a fake web, a fake ComfyUI and
a fake Docker, so they need none of them nor network access. REA is stood in for by
`tests/fake_rea_server.py`, a small MCP server the bridge starts and talks to
over stdio like the real one. One more test runs real code in the sandbox when
the `chatui-sandbox` image is built.
