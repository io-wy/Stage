"""Tests for TokenCounter."""

from __future__ import annotations

import pytest

from openagents_orchestration.token_counter import TokenCounter


class TestTokenCounter:
    def test_count_empty(self):
        counter = TokenCounter()
        assert counter.count("") == 0

    def test_count_ascii_fallback(self):
        counter = TokenCounter()  # no tiktoken → fallback
        # "hello world" = 11 chars → 11 // 4 = 2 (but max(1, ...) so 3? no 11//4=2, max(1,2)=2)
        assert counter.count("hello world") == 2

    def test_count_chinese_fallback(self):
        counter = TokenCounter()
        text = "你好世界"
        expected = max(1, len(text) // 4)
        assert counter.count(text) == expected

    def test_count_messages(self):
        counter = TokenCounter()
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        total = counter.count_messages(msgs)
        assert total == 2  # "hello"=5//4=1, "world"=5//4=1

    def test_count_messages_with_blocks(self):
        counter = TokenCounter()
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_result", "content": "output"},
                ],
            },
        ]
        total = counter.count_messages(msgs)
        # hello + \n + output = "hello\noutput" = 11 chars → 11//4 = 2
        assert total >= 2

    def test_name_fallback(self):
        counter = TokenCounter()
        assert counter.name == "fallback_len//4"

    def test_model_resolution(self):
        counter = TokenCounter(model="gpt-4o")
        assert "gpt-4o" in counter._model

    def test_count_non_empty_always_positive(self):
        counter = TokenCounter()
        assert counter.count("a") == 1  # 1 // 4 = 0, but max(1, 0) = 1
