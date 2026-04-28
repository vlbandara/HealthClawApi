from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from healthclaw.agent.graph import agent_graph
from healthclaw.agent.state import AgentState
from healthclaw.agent.time_context import TimeContext
from healthclaw.integrations.openrouter import OpenRouterResult


class CapturingExporter(SpanExporter):
    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)

    def shutdown(self):
        pass


@pytest.fixture
def in_memory_exporter():
    return CapturingExporter()


@pytest.fixture
def configured_tracer(in_memory_exporter):
    from opentelemetry import trace as otel_trace
    from healthclaw.core import tracing

    resource = Resource.create({"service.name": "healthclaw"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(in_memory_exporter))
    otel_trace.set_tracer_provider(provider)
    tracing._tracer = None
    yield provider
    otel_trace.set_tracer_provider(TracerProvider())
    tracing._tracer = None


def _make_time_context() -> TimeContext:
    return TimeContext(
        local_datetime="2026-04-21T08:00:00+05:30",
        local_date="2026-04-21",
        weekday="Tuesday",
        part_of_day="morning",
        quiet_hours=False,
        interaction_gap_days=None,
        long_lapse=False,
    )


def _make_minimal_state(user_id: str = "u-tracing-test") -> AgentState:
    return {
        "user": {
            "id": user_id,
            "timezone": "Asia/Colombo",
            "quiet_start": "22:00",
            "quiet_end": "07:00",
        },
        "user_content": "I want to start a meditation habit",
        "user_message": {"role": "user", "content": "I want to start a meditation habit"},
        "safety": {"category": "wellness", "severity": "low", "action": "support"},
        "memories": [],
        "open_loops": [],
        "recent_messages": [],
        "memory_documents": {},
        "soul_preferences": {},
        "streaks": [],
        "bridges": [],
        "time_context": _make_time_context().to_dict(),
        "thread_summary": "",
        "memory_mutations": [],
        "trace_metadata": {},
    }


async def test_node_spans_emitted_in_order(configured_tracer, in_memory_exporter):
        async def fake_chat_completion(self, messages, **kwargs):
            return OpenRouterResult(
                content="Hello! How can I help you today?",
                model="moonshotai/kimi-k2.6",
                usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
            )

        with patch(
            "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
            fake_chat_completion,
        ):
            state = _make_minimal_state()
            result = await agent_graph.ainvoke(state)
            assert result.get("response") is not None

            # Force flush and yield to event loop to allow span export
            configured_tracer.force_flush()
            await asyncio.sleep(0.05)
            configured_tracer.force_flush()
            exported_spans = in_memory_exporter.spans
            span_names = [s.name for s in exported_spans]

        expected_nodes = [
            "agent.input_normalization",
            "agent.safety_and_scope",
            "agent.time_context",
            "agent.memory_retrieval",
            "agent.companion_response",
            "agent.proactive_policy",
            "agent.memory_update",
            "agent.trace_eval_logging",
        ]
        for expected in expected_nodes:
            assert expected in span_names, f"Expected span {expected} not found in {span_names}"

        node_spans = [s for s in exported_spans if s.name.startswith("agent.")]
        node_spans.sort(key=lambda s: s.start_time)
        node_span_names = [s.name for s in node_spans]
        assert node_span_names == expected_nodes, (
            f"Node spans not in expected order.\nExpected: {expected_nodes}\nGot: {node_span_names}"
        )


async def test_openrouter_chat_span_emitted(configured_tracer, in_memory_exporter):
    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content="Test response",
            model="moonshotai/kimi-k2.6",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    with patch(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    ):
        state = _make_minimal_state(user_id="u-span-test")
        await agent_graph.ainvoke(state)

        configured_tracer.force_flush()
        exported_spans = in_memory_exporter.spans
        openrouter_spans = [s for s in exported_spans if s.name == "openrouter.chat"]
        assert len(openrouter_spans) >= 1

        chat_span = openrouter_spans[0]
        attrs = {k: v for k, v in chat_span.attributes.items()}
        assert attrs.get("model_role") == "chat"


async def test_harness_build_span_emitted(configured_tracer, in_memory_exporter):
    from healthclaw.agent.context_harness import ContextHarness
    from healthclaw.core.config import get_settings

    settings = get_settings()
    harness = ContextHarness(settings)

    harness.build(
        user_content="I want to start running",
        time_context=_make_time_context(),
        memories=[],
        recent_messages=[],
        open_loops=[],
        memory_documents={},
        user_context={"id": "u-harness-test"},
    )

    exported_spans = in_memory_exporter.spans
    harness_spans = [s for s in exported_spans if s.name == "harness.build"]
    assert len(harness_spans) == 1, f"Expected 1 harness.build span, got {len(harness_spans)}"

    attrs = {k: v for k, v in harness_spans[0].attributes.items()}
    assert attrs.get("mode") == "active"
    assert "query_len" in attrs
    assert attrs["query_len"] > 0


async def test_traced_node_sets_user_id(configured_tracer, in_memory_exporter):
    async def fake_chat_completion(self, messages, **kwargs):
        return OpenRouterResult(
            content="Test",
            model="moonshotai/kimi-k2.6",
            usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        )

    with patch(
        "healthclaw.integrations.openrouter.OpenRouterClient.chat_completion",
        fake_chat_completion,
    ):
        state = _make_minimal_state(user_id="u-node-attr-test")
        await agent_graph.ainvoke(state)

        configured_tracer.force_flush()
        exported_spans = in_memory_exporter.spans
        for span in exported_spans:
            if span.name == "agent.input_normalization":
                attrs = {k: v for k, v in span.attributes.items()}
                assert "user_id" in attrs or "node" in attrs
                break