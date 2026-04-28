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


def test_goal_without_i_trying_to() -> None:
    mutations = extract_memory_mutations("trying to sleep by 10pm tonight")
    by_kind = {mutation.kind: mutation for mutation in mutations}

    assert "goal" in by_kind
    assert "10pm" in by_kind["goal"].value["text"]


def test_goal_i_need_to() -> None:
    mutations = extract_memory_mutations("I need to drink more water daily")
    by_kind = {mutation.kind: mutation for mutation in mutations}

    assert "goal" in by_kind
    assert "water" in by_kind["goal"].value["text"]


def test_goal_id_like_to() -> None:
    mutations = extract_memory_mutations("I'd like to start meditation habit")
    by_kind = {mutation.kind: mutation for mutation in mutations}

    assert "goal" in by_kind
    assert "meditation" in by_kind["goal"].value["text"]


def test_goal_i_would_like_to() -> None:
    mutations = extract_memory_mutations("I would like to reduce my screen time")
    by_kind = {mutation.kind: mutation for mutation in mutations}

    assert "goal" in by_kind
    assert "screen time" in by_kind["goal"].value["text"]
