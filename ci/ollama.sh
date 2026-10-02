#!/usr/bin/env bash
# Install Ollama on the runner, start it, pull the bot's model.
set -e
curl -fsSL https://ollama.com/install.sh | sh >/dev/null
pgrep -x ollama >/dev/null || (nohup ollama serve >/tmp/ollama.log 2>&1 &)
for i in $(seq 30); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
ollama pull "${LLM_MODEL:-qwen2.5:7b}" >/dev/null
echo "ollama ready: $(ollama list | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"
