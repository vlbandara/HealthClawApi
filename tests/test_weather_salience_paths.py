from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from healthclaw.core.config import get_settings
from healthclaw.db.models import Signal, User
from healthclaw.db.session import SessionLocal
from healthclaw.inner.salience import SalienceResult
from healthclaw.inner.tick import run_inner_tick
from healthclaw.integrations.weather import SeasonalWeatherNormal, WeatherSalienceContext
from healthclaw.proactivity.route_through_synth import route_heartbeat_job_through_synth


def _weather_context() -> WeatherSalienceContext:
    return WeatherSalienceContext(
        location_confirmed=True,
        seasonal_normal=SeasonalWeatherNormal(
            lat=64.1,
            lon=-21.9,
            seasonal_window_start="2023-04-19",
            seasonal_window_end="2025-05-19",
            apparent_temp_mean_c=12.0,
            apparent_temp_p90_c=17.0,
            sample_count=72,
        ),
    )


async def test_run_inner_tick_passes_weather_context(monkeypatch) -> None:
    captured: list[WeatherSalienceContext | None] = []
    sentinel = _weather_context()

    async def fake_weather_context(user, *, now=None, provider=None):
        return sentinel

    def fake_compute_salience(signals, time_context, **kwargs):
        captured.append(kwargs.get("weather_context"))
        return SalienceResult(score=0.0, breakdown={})

    monkeypatch.setattr(
        "healthclaw.integrations.weather.build_weather_salience_context",
        fake_weather_context,
    )
    monkeypatch.setattr("healthclaw.inner.tick.compute_salience", fake_compute_salience)

    observed_at = datetime.now(UTC)
    async with SessionLocal() as session:
        session.add(
            User(
                id="weather-path-user",
                timezone="UTC",
                quiet_start="23:59",
                quiet_end="00:00",
                proactive_enabled=True,
                home_lat=64.1,
                home_lon=-21.9,
            )
        )
        session.add(
            Signal(
                user_id="weather-path-user",
                kind="weather",
                value={
                    "temp_c": 18,
                    "feels_like_c": 19,
                    "humidity_pct": 65,
                    "uv_index": 4,
                    "wmo_code": 1,
                    "condition": "clear",
                },
                observed_at=observed_at,
                source="open_meteo",
                dedup_key="weather-path-user:weather:1",
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        result = await run_inner_tick("weather-path-user", session)

    assert result["status"] == "ticked"
    assert captured == [sentinel]


async def test_route_through_synth_passes_weather_context(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("INNER_SYNTHESIZER_ENABLED", "true")
    get_settings.cache_clear()

    captured: list[WeatherSalienceContext | None] = []
    sentinel = _weather_context()

    async def fake_weather_context(user, *, now=None, provider=None):
        return sentinel

    def fake_compute_salience(signals, time_context, **kwargs):
        captured.append(kwargs.get("weather_context"))
        return SalienceResult(score=0.0, breakdown={})

    class FakeIntent:
        kind = "check_in"
        motive = None
        discarded = False
        discarded_reason = None
        draft_message = "Check in."
        earliest_send_at = None
        needs_web_search = False
        web_search_query = None

    class FakeSynthesizer:
        def __init__(self, session):
            self.session = session

        async def synthesize(self, thought_id, user, signals, motives, time_ctx_dict):
            return FakeIntent()

    class FakeGate:
        def __init__(self, session):
            self.session = session

        async def evaluate_intent(self, thought, user, time_ctx, intent):
            return {"status": "emitted"}

    monkeypatch.setattr(
        "healthclaw.integrations.weather.build_weather_salience_context",
        fake_weather_context,
    )
    monkeypatch.setattr("healthclaw.inner.salience.compute_salience", fake_compute_salience)
    monkeypatch.setattr(
        "healthclaw.inner.synthesizer.InnerSynthesizer",
        FakeSynthesizer,
    )
    monkeypatch.setattr("healthclaw.inner.speech_gate.SpeechGate", FakeGate)

    observed_at = datetime.now(UTC)
    async with SessionLocal() as session:
        session.add(
            User(
                id="weather-proactive-user",
                timezone="UTC",
                quiet_start="23:59",
                quiet_end="00:00",
                proactive_enabled=True,
                home_lat=64.1,
                home_lon=-21.9,
            )
        )
        session.add(
            Signal(
                user_id="weather-proactive-user",
                kind="weather",
                value={
                    "temp_c": 18,
                    "feels_like_c": 19,
                    "humidity_pct": 65,
                    "uv_index": 4,
                    "wmo_code": 1,
                    "condition": "clear",
                },
                observed_at=observed_at,
                source="open_meteo",
                dedup_key="weather-proactive-user:weather:1",
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        user = await session.get(User, "weather-proactive-user")
        decision = await route_heartbeat_job_through_synth(
            SimpleNamespace(kind="followup"),
            user,
            session,
            now=observed_at,
        )

    assert decision is not None
    assert decision.action == "emit"
    assert captured == [sentinel]
    get_settings.cache_clear()
