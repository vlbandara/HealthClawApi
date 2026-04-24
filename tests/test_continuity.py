from __future__ import annotations

from healthclaw.agent.continuity import build_bridges
from tests.factories import make_time_context


def test_long_lapse_bridge_fires() -> None:
    time_context = make_time_context(interaction_gap_days=8, long_lapse=True)
    
    bridges = build_bridges(time_context, memories=[], open_loops=[])
    
    assert len(bridges) == 1
    assert "no need to recap" in bridges[0]
    assert "8 days" in bridges[0]

def test_stale_open_loop_bridge_fires() -> None:
    time_context = make_time_context(interaction_gap_days=0, long_lapse=False)
    
    open_loops = [
        {
            "id": "loop-1",
            "title": "buy groceries",
            "status": "open",
            "age_hours": 20.0
        }
    ]
    
    bridges = build_bridges(time_context, memories=[], open_loops=open_loops)
    
    assert len(bridges) == 1
    assert "buy groceries" in bridges[0]
    assert "Did that end up happening" in bridges[0]

def test_no_bridge_on_crisis() -> None:
    time_context = make_time_context(interaction_gap_days=8, long_lapse=True)
    
    bridges = build_bridges(
        time_context, 
        memories=[], 
        open_loops=[], 
        safety_category="crisis"
    )
    
    assert len(bridges) == 0
