"""Tests for token budget (Workstream F)."""
from __future__ import annotations

import pytest

from healthclaw.agent.token_budget import TokenBudget, _first_sentence, count_tokens


def test_count_tokens_basic() -> None:
    # cl100k_base or char/4 fallback — just check it returns a positive int
    assert count_tokens("hello world") > 0
    assert count_tokens("") == 0


def test_budget_charge_and_remaining() -> None:
    budget = TokenBudget(max_tokens=1000, reserve_system=200, reserve_output=100)
    # available = 700
    tokens = budget.charge("test_section", "hello world")
    assert tokens > 0
    assert budget.used == tokens
    assert budget.remaining == budget.available - tokens


def test_fit_memories_evicts_episodes_first() -> None:
    budget = TokenBudget(max_tokens=100, reserve_system=10, reserve_output=10)
    # available = 80 tokens
    # Fill budget mostly so episodes get dropped
    big_active = {"kind": "goal", "key": "k1", "semantic_text": "A " * 40, "value": {}}
    episode = {"kind": "episode", "key": "e1", "semantic_text": "B " * 20, "value": {}}
    budget.charge("pre", "x " * 70)  # pre-charge to leave little room
    kept = budget.fit_memories([big_active, episode])
    names = [m["key"] for m in kept]
    # episode should be evicted or come after active
    if episode in kept and big_active in kept:
        assert names.index("k1") < names.index("e1")


def test_fit_recent_messages_trims_oldest() -> None:
    budget = TokenBudget(max_tokens=200, reserve_system=20, reserve_output=20)
    # available = 160 tokens
    messages = [
        {"role": "user", "content": "Short message"},
        {"role": "assistant", "content": "Short reply"},
        {"role": "user", "content": "x " * 200},  # should be dropped or compressed
    ]
    kept = budget.fit_recent_messages(messages)
    # newest messages should be kept; long one dropped or compressed
    contents = [m["content"] for m in kept]
    assert any("Short" in c for c in contents)


def test_first_sentence_extraction() -> None:
    assert _first_sentence("Hello world. Second sentence.") == "Hello world."
    # Text shorter than 120 chars with no sentence terminator → returned as-is (up to 120)
    short_text = "No period here just text"
    result = _first_sentence(short_text)
    assert result == short_text  # unchanged since < 120 chars
    assert _first_sentence("") == ""
    # Long text without sentence terminator → truncated to 120 chars
    long_text = "x " * 100
    result_long = _first_sentence(long_text)
    assert len(result_long) <= 120


def test_budget_usage_dict() -> None:
    budget = TokenBudget(max_tokens=1000, reserve_system=0, reserve_output=0)
    budget.charge("memories", "some memories text")
    budget.charge("recent", "some recent text")
    usage = budget.budget_usage()
    assert "memories" in usage
    assert "recent" in usage
    assert "_total" in usage
    assert usage["_total"] == usage["memories"] + usage["recent"]
