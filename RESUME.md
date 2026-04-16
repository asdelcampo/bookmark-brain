# Resuming bookmark-brain

## First time: build llama.cpp

llama.cpp is a runtime dependency — not tracked in this repo. Build it once:

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DLLAMA_METAL=ON
cmake --build build --config Release -j$(nproc)
```

The model (`unsloth/gemma-4-E2B-it-GGUF:Q8_0`) is downloaded automatically by
llama-server on first run via Hugging Face.

---

## Startup sequence

**Terminal 1 — LLM server:**

```bash
cd ~/projects/bookmark-brain
./scripts/start-llm.sh
```

Wait for: `llama server listening at http://0.0.0.0:8001`

**Terminal 2 — bb CLI:**

```bash
cd ~/projects/bookmark-brain
source venv/bin/activate
bb <command>
```

---

## Quick health check

```bash
curl http://localhost:8001/health
```

Expected: `{"status":"ok"}`

---

## Common workflows

```bash
bb sync                          # pull new X bookmarks + process them
bb search "topic"                # instant keyword search
bb ask "what tools do I know for X"
bb list --recent 7d              # see what was added this week
bb stats                         # overview
```

See `README.md` for the full command reference.
