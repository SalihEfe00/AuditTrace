# -*- coding: utf-8 -*-
"""
Müfettiş AI - FAZ 19.9: Analysis Intelligence Core
LLM Support & Structured Prompting Utilities.

Provides centralized structured prompting, JSON extraction, and safe fallback
handling for LLM integrations without scattering prompts across modules.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional


def extract_json_from_llm_response(text: str) -> Optional[Dict[str, Any]]:
    """Safely extracts and parses JSON from raw LLM output.

    Handles markdown fences (```json ... ```) or bare JSON objects.
    Returns None if text cannot be parsed into a valid JSON object.
    """
    if not text or not isinstance(text, str):
        return None

    cleaned = text.strip()

    # 1. Match code fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # 2. Match outer braces
    brace_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # 3. Direct parse
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    return None


def execute_structured_llm_call(
    prompt: str,
    generate_fn: Callable[[str], str],
    fallback_factory: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    """Executes an LLM call via the provided generator function, parses JSON,

    and returns the result or the fallback if parsing fails or LLM errors.
    """
    try:
        raw_response = generate_fn(prompt)
        if not raw_response or raw_response.startswith("[LLM HATASI]"):
            return fallback_factory()

        parsed = extract_json_from_llm_response(raw_response)
        if parsed is not None:
            return parsed
        return fallback_factory()
    except Exception:
        return fallback_factory()
