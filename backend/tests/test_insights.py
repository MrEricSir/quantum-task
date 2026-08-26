"""
Tests for routers/insights.py's _generate_texts -- previously had zero test coverage
anywhere in the codebase. Scoped to the LLM-reliability/error-handling fix applied here
(reasoning_effort, and logging a failure instead of silently swallowing it) -- not a full
audit of insights.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
from unittest.mock import MagicMock, patch

import pytest

import routers.insights as insights
from routers.insights import _generate_texts


@pytest.fixture(autouse=True)
def clear_text_cache():
    insights._text_cache.clear()
    yield
    insights._text_cache.clear()


def _mock_llm(payload):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class TestGenerateTexts:

    def test_returns_texts_matched_by_index(self):
        payload = {"insights": [{"index": 0, "text": "Custom text A"}, {"index": 1, "text": "Custom text B"}]}
        with patch("routers.insights.llm_client", return_value=_mock_llm(payload)):
            texts = _generate_texts(["pattern A", "pattern B"])

        assert texts == ["Custom text A", "Custom text B"]

    def test_requests_reasoning_effort_low(self):
        """The reliability fix: on a reasoning model, an unbounded chain-of-thought can burn
        the whole max_tokens budget before the real JSON answer, truncating it -- see
        correlations.py's _generate_experiment for the full story."""
        mock_client = _mock_llm({"insights": []})
        with patch("routers.insights.llm_client", return_value=mock_client), \
             patch("deps.LLM_MODEL", "openai/gpt-oss-120b"):
            _generate_texts(["pattern A"])

        assert mock_client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "low"

    def test_missing_index_falls_back_to_empty_string(self):
        payload = {"insights": [{"index": 0, "text": "Custom text A"}]}
        with patch("routers.insights.llm_client", return_value=_mock_llm(payload)):
            texts = _generate_texts(["pattern A", "pattern B"])

        assert texts == ["Custom text A", ""]

    def test_llm_failure_falls_back_to_empty_strings_and_is_logged(self, capsys):
        """Previously a bare `except Exception: texts = ["" for _ in patterns]` with zero
        logging -- callers already show an honest static template when text is empty, so
        this doesn't display anything false, but a real recurring failure was invisible."""
        broken_client = MagicMock()
        broken_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch("routers.insights.llm_client", return_value=broken_client):
            texts = _generate_texts(["pattern A", "pattern B"])

        assert texts == ["", ""]
        assert "text generation error" in capsys.readouterr().out

    def test_malformed_json_falls_back_to_empty_strings_and_is_logged(self, capsys):
        malformed_client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='{"insights": [truncated'))]
        malformed_client.chat.completions.create.return_value = resp
        with patch("routers.insights.llm_client", return_value=malformed_client):
            texts = _generate_texts(["pattern A"])

        assert texts == [""]
        assert "text generation error" in capsys.readouterr().out

    def test_repeated_call_with_same_patterns_uses_cache(self):
        mock_client = _mock_llm({"insights": [{"index": 0, "text": "Custom text"}]})
        with patch("routers.insights.llm_client", return_value=mock_client):
            _generate_texts(["pattern A"])
            _generate_texts(["pattern A"])

        assert mock_client.chat.completions.create.call_count == 1
