#!/bin/bash
# Starts the ChatUI server, and Ollama only if it isn't already running
# (it usually runs on its own as the `ollama` systemd service).
# Settings can go in a .env file in this folder (copy .env.example; it is git-ignored).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

set -a; [ -f .env ] && . ./.env; set +a
PORT="${SERVER_PORT:-${API_PORT:-8100}}"
OLLAMA="${OLLAMA_URL:-http://localhost:11434}"

if [ -f "$DIR/.venv/bin/activate" ]; then VENV="$DIR/.venv"; else VENV="$HOME/vllm-env"; fi

# ---- Ollama ----
if curl -sf --max-time 3 "$OLLAMA/api/tags" >/dev/null; then
    echo "Ollama is already running at $OLLAMA."
else
    echo "Ollama is not running; starting 'ollama serve' in a new terminal."
    gnome-terminal -- bash -c "ollama serve; exec bash"
    for _ in $(seq 1 20); do
        curl -sf --max-time 1 "$OLLAMA/api/tags" >/dev/null && break
        sleep 1
    done
fi

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# ---- GPT-SoVITS voice API (optional; set TTS_SOVITS_AUTOSTART=0 in .env to leave it off) ----
SOVITS_URL="${TTS_SOVITS_URL:-http://127.0.0.1:9880}"
SOVITS_PORT="${SOVITS_URL##*:}"
SOVITS_PYTHON="${TTS_SOVITS_PYTHON:-$HOME/miniconda3/envs/GPTSoVits/bin/python}"
SOVITS_HOME=""
for d in "$TTS_SOVITS_HOME" "$HOME/GPT-SoVITS" "$DIR/../../../GPT-SoVITS"; do
    [ -n "$d" ] && [ -f "$d/api_v2.py" ] && { SOVITS_HOME="$(cd "$d" && pwd)"; break; }
done

if [ "${TTS_SOVITS_AUTOSTART:-1}" = "0" ]; then
    :
elif [[ "$SOVITS_URL" != *//127.0.0.1:* && "$SOVITS_URL" != *//localhost:* ]]; then
    echo "GPT-SoVITS is set to run on another machine ($SOVITS_URL); not starting it here."
elif port_in_use "$SOVITS_PORT"; then
    echo "Port $SOVITS_PORT is already in use; taking that to be GPT-SoVITS."
elif [ -z "$SOVITS_HOME" ] || [ ! -x "$SOVITS_PYTHON" ]; then
    echo "GPT-SoVITS not found; skipping it. Set TTS_SOVITS_HOME and TTS_SOVITS_PYTHON in .env to use it."
else
    gnome-terminal -- bash -c "cd '$SOVITS_HOME'; '$SOVITS_PYTHON' api_v2.py -a 127.0.0.1 -p '$SOVITS_PORT' -c GPT_SoVITS/configs/tts_infer.yaml; exec bash"
    echo "GPT-SoVITS voice API starting on port $SOVITS_PORT in a new terminal."
fi

# ---- ChatUI server ----

if port_in_use "$PORT"; then
    if curl -sf --max-time 3 "http://localhost:$PORT/" | grep -q '"ChatUI server"'; then
        echo "The ChatUI server is already running on port $PORT; nothing to start."
        echo "To restart it, stop the old one first:  sudo fuser -k $PORT/tcp"
    else
        echo "Port $PORT is used by another program:"
        sudo ss -ltnp "sport = :$PORT" 2>/dev/null | tail -n +2 || sudo lsof -i ":$PORT" -sTCP:LISTEN
        echo "Stop that program, or set SERVER_PORT to a free port in .env."
    fi
    exit 1
fi

# CUDA libraries for Whisper on the GPU, when they were pip-installed into the venv (see README)
NV_LIBS="$("$VENV/bin/python" -c 'import nvidia.cublas.lib as b, nvidia.cudnn.lib as d; print(b.__path__[0] + ":" + d.__path__[0])' 2>/dev/null)"
LIBS=""
[ -n "$NV_LIBS" ] && LIBS="export LD_LIBRARY_PATH='$NV_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}'; "

gnome-terminal -- bash -c "source '$VENV/bin/activate'; cd '$DIR'; ${LIBS}python ollama_server.py; exec bash"
echo "ChatUI server starting on port $PORT in a new terminal."

#gnome-terminal -- bash -c "source ~/functiongemma/functiongemma-env/bin/activate; python ~/functiongemma/functiongemma_server.py; exec bash"
