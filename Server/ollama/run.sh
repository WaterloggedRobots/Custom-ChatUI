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

# ---- ChatUI server ----
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

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

gnome-terminal -- bash -c "source '$VENV/bin/activate'; cd '$DIR'; python ollama_server.py; exec bash"
echo "ChatUI server starting on port $PORT in a new terminal."

#gnome-terminal -- bash -c "source ~/functiongemma/functiongemma-env/bin/activate; python ~/functiongemma/functiongemma_server.py; exec bash"
