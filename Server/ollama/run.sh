#!/bin/bash
# Starts Ollama and the ChatUI server in two terminals.
# The server needs chat_server/ next to ollama_server.py. Settings can go in
# a .env file in this folder (copy .env.example; .env is git-ignored).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

gnome-terminal -- bash -c "ollama serve; exec bash"

if [ -f "$DIR/.venv/bin/activate" ]; then VENV="$DIR/.venv"; else VENV="$HOME/vllm-env"; fi

gnome-terminal -- bash -c "source '$VENV/bin/activate'; cd '$DIR'; set -a; [ -f .env ] && . ./.env; set +a; python ollama_server.py; exec bash"

#gnome-terminal -- bash -c "source ~/functiongemma/functiongemma-env/bin/activate; python ~/functiongemma/functiongemma_server.py; exec bash"
