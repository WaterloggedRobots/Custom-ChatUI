# The Ubuntu sandbox the chat model tests its code in (chat_server/sandbox.py).
# Build once on the server, and again after changing this file:
#   docker build -t chatui-sandbox Server/ollama/sandbox
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv python3-pytest python3-numpy python3-requests \
        nodejs npm build-essential git curl ca-certificates jq \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu's image has a user 'ubuntu' with uid 1000; the sandbox user takes its place.
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /work && chown sandbox:sandbox /work

USER sandbox
WORKDIR /work
CMD ["sleep", "infinity"]
