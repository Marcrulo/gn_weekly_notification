"""Talk to the local language model that writes AI-fred's words.

The model runs on the GitHub Actions runner itself, through Ollama on
localhost - no API key, no account, no per-call cost. See the README for the
workflow steps that install it.
"""

from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_HOST = "http://localhost:11434"
MODEL = "qwen3.5:9b"

# A 9B model on a 4-core runner has to load ~7.6GB off disk, read the prompt and
# then generate a few hundred tokens. A few minutes is normal; this is the ceiling.
TIMEOUT = 900


def model() -> str:
    return os.environ.get("OLLAMA_MODEL") or MODEL


def chat(request: dict, host: str | None = None, timeout: int = TIMEOUT) -> str:
    """POST to Ollama's /api/chat and return the assistant's message content."""
    host = host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
    call = urllib.request.Request(
        host.rstrip("/") + "/api/chat",
        data=json.dumps(request).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(call, timeout=timeout) as response:
        body = json.load(response)
    return body["message"]["content"]
