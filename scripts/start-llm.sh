#!/bin/bash
# Resolve llama-server path (checks both common locations)
if [ -f "$HOME/llama.cpp/build/bin/llama-server" ]; then
  LLAMA_BIN="$HOME/llama.cpp/build/bin/llama-server"
elif [ -f "$HOME/projects/bookmark-brain/llama.cpp/build/bin/llama-server" ]; then
  LLAMA_BIN="$HOME/projects/bookmark-brain/llama.cpp/build/bin/llama-server"
else
  echo "llama-server not found. Build llama.cpp first:"
  echo ""
  echo "  git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp"
  echo "  cd ~/llama.cpp"
  echo "  cmake -B build -DLLAMA_METAL=ON"
  echo "  cmake --build build --config Release -j\$(nproc)"
  exit 1
fi

echo "Starting llama-server with Gemma 4 E2B..."
"$LLAMA_BIN" \
  -hf unsloth/gemma-4-E2B-it-GGUF:Q8_0 \
  --ctx-size 4096 \
  --port 8001
