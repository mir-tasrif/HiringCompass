"""Shared test helpers: deterministic fake embedder and a scripted fake LLM."""

import hashlib
import math
import re
from typing import Any


# Deterministic bag-of-words embedder (768 dims, unit length) so retrieval works without Ollama.
async def fake_embed(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        vec = [0.0] * 768
        for token in re.findall(r"[a-z]+", text.lower()):
            vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % 768] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


# Fake LLM: replies are queued per schema title; the last reply repeats. Every call is recorded.
class ScriptedLLM:
    def __init__(self, script: dict[str, list[str]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    # Return the next scripted reply for the requested schema.
    async def chat(self, messages: list[dict[str, str]], *, schema: dict[str, Any] | None = None, **_: Any) -> str:
        name = schema["title"]
        self.calls.append((name, messages))
        queue = self.script[name]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    # Messages of every call made for one schema.
    def calls_for(self, name: str) -> list[list[dict[str, str]]]:
        return [m for n, m in self.calls if n == name]