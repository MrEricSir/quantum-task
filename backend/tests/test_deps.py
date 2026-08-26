"""
Tests for deps.reasoning_kwargs() -- the fix for a real local-dev regression: passing
reasoning_effort="low" unconditionally to every chat.completions.create() call broke local
LLM calls entirely, since Ollama's OpenAI-compatible endpoint rejects the whole request with
a 400 ("does not support thinking") for any model that doesn't support it, rather than
silently ignoring the unknown kwarg the way it was assumed to. reasoning_kwargs() centralizes
the "is this actually a reasoning model" decision in one place instead of 24 call sites each
guessing the same thing.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch

import deps


class TestReasoningKwargs:

    def test_included_for_groqs_gpt_oss(self):
        with patch("deps.LLM_MODEL", "openai/gpt-oss-120b"):
            assert deps.reasoning_kwargs() == {"reasoning_effort": "low"}

    def test_omitted_for_local_ollama_models(self):
        """The actual regression: llama3.2 (the local dev default) doesn't support
        "thinking" at all -- Ollama's OpenAI-compatible endpoint 400s on the request
        rather than ignoring the unrecognized kwarg."""
        for model in ["llama3.2", "phi4-mini", "mistral-nemo"]:
            with patch("deps.LLM_MODEL", model):
                assert deps.reasoning_kwargs() == {}

    def test_omitted_for_other_non_reasoning_hosted_models(self):
        with patch("deps.LLM_MODEL", "gpt-4o-mini"):
            assert deps.reasoning_kwargs() == {}
