from __future__ import annotations

import json
import time

from django.conf import settings
from openai import OpenAI

class AIUnavailable(Exception):
    """
    A distinct exception type on purpose: categorization failing must never fail
    the receipt. The caller catches this, records status FAILED, and the expense
    still reaches a human with its extracted fields intact.
    """

def _client():
        if not settings.OPENAI_API_KEY:
            raise AIUnavailable("Open ai api key is not configured")
        return OpenAI(api_key=settings.OPENAI_API_KEY,timeout=settings.OPENAI_TIMEOUT_SECONDS)

def complete_json(*, model:str, system:str, user:str, schema:dict) -> tuple[dict, dict]:

        started = time.monotonic()
        try:
            response = _client().chat.completions.create(

                model = model,
                temperature=0,
                messages=[{"role":"system","content":system},
                          {"role":"user","content":user}],
                response_format={"type":"json_schema","json_schema":schema}
            )
        except Exception as exc:
            raise AIUnavailable(f"{model} call failed: {exc}") from exc

        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise AIUnavailable(f"{model} returned an empty response")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIUnavailable(f"{model} returned an invalid JSON: {exc}") from exc

        usage = getattr(response, "usage", None)
        telemetry = {
            "model": model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        }
        return parsed, telemetry

def embed(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = _client().embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL, input=texts
            )
        except Exception as exc:
            raise AIUnavailable(f"embedding call failed: {exc}") from exc
        return [row.embedding for row in response.data]
