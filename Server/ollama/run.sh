#!/bin/bash

gnome-terminal -- bash -c "ollama serve; exec bash"

gnome-terminal -- bash -c "source ~/vllm-env/bin/activate; python ~/ollama/ollama_server.py; exec bash"

#gnome-terminal -- bash -c "source ~/functiongemma/functiongemma-env/bin/activate; python ~/functiongemma/functiongemma_server.py; exec bash"
