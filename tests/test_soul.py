from __future__ import annotations

from healthclaw.agent.soul import default_policy_memory, system_prompt, trust_band_label, trust_tone_block


def test_system_prompt_prioritizes_natural_first_chat() -> None:
    prompt = system_prompt()

    assert "# Healthclaw Identity" in prompt
    assert "# Voice" in prompt
    assert "# Healthclaw SOUL.md" in prompt
    assert "lead with one useful move before asking a question" in prompt
    assert "brand-new or low-context users" in prompt.lower()
    assert "ask small human questions before steering into goals" in prompt.lower()
    assert "ask at most one good question" in prompt
    assert "do not mention old memories, routines, reminders" in prompt.lower()


def test_system_prompt_blocks_scripted_filler() -> None:
    prompt = system_prompt()

    assert "gentle reset" in prompt
    assert "my purpose is" in prompt
    assert "one small task" in prompt
    assert "I'm here to help" in prompt
    assert "Adult, Still Safe" in prompt
    assert "medical-boundary rules are immutable" in prompt


def test_system_prompt_includes_markdown_memory_documents() -> None:
    prompt = system_prompt(
        user_id="u-doc",
        timezone="Asia/Colombo",
        memory_documents={
            "USER": "## Stable Profile\n- Preferred name: Vinodh",
            "MEMORY": "## Durable Memory\n- Likes concise replies",
        },
    )

    assert "# User.md" in prompt
    assert "Preferred name: Vinodh" in prompt
    assert "# Memory.md" in prompt
    assert "Likes concise replies" in prompt


def test_default_policy_memory_matches_natural_voice() -> None:
    policy = default_policy_memory()

    assert policy["default_next_step"] == "lead with one useful move before asking a question"
    assert "generic assistant filler" in policy["avoid"]


def test_trust_band_label_returns_correct_band() -> None:
    assert trust_band_label(None) == "low"
    assert trust_band_label(0.0) == "low"
    assert trust_band_label(0.39) == "low"
    assert trust_band_label(0.4) == "medium"
    assert trust_band_label(0.74) == "medium"
    assert trust_band_label(0.75) == "high"
    assert trust_band_label(1.0) == "high"


def test_trust_tone_block_varies_by_band() -> None:
    low_block = trust_tone_block(0.2)
    high_block = trust_tone_block(0.9)

    assert "low" in low_block
    assert "high" in high_block
    assert low_block != high_block


def test_system_prompt_includes_trust_tone_block_and_varies_by_band() -> None:
    low_prompt = system_prompt(trust_level=0.1)
    high_prompt = system_prompt(trust_level=0.9)

    assert "Trust Tone Band: low" in low_prompt
    assert "Trust Tone Band: high" in high_prompt
    assert low_prompt != high_prompt
