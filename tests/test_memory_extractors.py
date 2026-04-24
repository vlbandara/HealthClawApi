from __future__ import annotations

from healthclaw.memory.extractors import extract_memory_mutations


def test_extract_goal_and_preference() -> None:
    mutations = extract_memory_mutations("My goal is sleep by 10pm. I prefer concise replies.")
    keys = {mutation.key for mutation in mutations}
    assert "current_goal" in keys
    assert "user_tone_preference" in keys


def test_extract_preferred_name() -> None:
    mutations = extract_memory_mutations("my name is Vinodh")

    assert mutations[0].kind == "profile"
    assert mutations[0].key == "preferred_name"
    assert mutations[0].value["text"] == "Vinodh"


def test_goal_extraction_splits_contrast_friction() -> None:
    mutations = extract_memory_mutations(
        "I am trying to sleep by 10pm but I keep drifting to midnight."
    )
    by_kind = {mutation.kind: mutation for mutation in mutations}

    assert by_kind["goal"].value["text"] == "sleep by 10pm"
    assert by_kind["friction"].value["text"] == "drifting to midnight"
