"""Tests for integrations/weather.py — provider, caching, and snapshot properties."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from healthclaw.integrations.weather import (
    NullWeatherProvider,
    OpenMeteoProvider,
    WeatherSnapshot,
    build_weather_salience_context,
)


def _snapshot(
    temp_c: float = 33, humidity: int = 82, uv: float = 9, wmo: int = 2
) -> WeatherSnapshot:
    return WeatherSnapshot(
        lat=1.3, lon=103.8,
        temp_c=temp_c,
        feels_like_c=temp_c + 2,
        humidity_pct=humidity,
        condition="partly_cloudy",
        wmo_code=wmo,
        uv_index=uv,
        fetched_at="2026-05-01T14:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_null_provider_returns_none():
    provider = NullWeatherProvider()
    result = await provider.get_current(1.3, 103.8)
    assert result is None


def test_high_uv_property():
    snap = _snapshot(uv=9)
    assert snap.high_uv is True
    snap2 = _snapshot(uv=5)
    assert snap2.high_uv is False


def test_severe_property():
    snap = _snapshot(wmo=95)
    assert snap.is_severe is True
    snap2 = _snapshot(wmo=2)
    assert snap2.is_severe is False


def test_to_dict_roundtrip():
    snap = _snapshot()
    d = snap.to_dict()
    assert d["temp_c"] == snap.temp_c
    assert d["humidity_pct"] == snap.humidity_pct


@pytest.mark.asyncio
async def test_open_meteo_returns_cached_on_second_call(respx_mock):
    """Second identical call returns cached snapshot without HTTP."""
    provider = OpenMeteoProvider()
    mock_response = {
        "current": {
            "temperature_2m": 33.2,
            "apparent_temperature": 35.0,
            "relative_humidity_2m": 82,
            "weather_code": 2,
            "uv_index": 7.0,
        }
    }
    # First call hits HTTP
    respx_mock.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=httpx.Response(200, json=mock_response)
    )
    snap1 = await provider.get_current(1.3, 103.8)
    assert snap1 is not None
    assert snap1.temp_c == 33.2

    # Second call — cache hit, no new HTTP request
    snap2 = await provider.get_current(1.3, 103.8)
    assert snap2 is snap1
    assert respx_mock.calls.call_count == 1


@pytest.mark.asyncio
async def test_open_meteo_builds_and_caches_seasonal_normals(respx_mock):
    provider = OpenMeteoProvider()
    archive_route = respx_mock.get("https://archive-api.open-meteo.com/v1/archive")

    archive_route.mock(
        side_effect=[
            httpx.Response(200, json={"hourly": {"apparent_temperature": [8.0, 12.0, 16.0]}}),
            httpx.Response(200, json={"hourly": {"apparent_temperature": [10.0, 14.0, 18.0]}}),
            httpx.Response(200, json={"hourly": {"apparent_temperature": [9.0, 13.0, 17.0]}}),
        ]
    )

    as_of = datetime(2026, 5, 4, tzinfo=UTC)
    normal_1 = await provider.get_seasonal_normal(64.1, -21.9, as_of=as_of)
    normal_2 = await provider.get_seasonal_normal(64.1, -21.9, as_of=as_of)

    assert normal_1 is not None
    assert normal_2 is normal_1
    assert normal_1.sample_count == 9
    assert normal_1.apparent_temp_mean_c == pytest.approx(13.0)
    assert normal_1.apparent_temp_p90_c == pytest.approx(17.2, rel=1e-3)
    assert archive_route.call_count == 3


@pytest.mark.asyncio
async def test_build_weather_salience_context_requires_explicit_location(monkeypatch):
    class DummyUser:
        home_lat = None
        home_lon = None

    context = await build_weather_salience_context(DummyUser())
    assert context.location_confirmed is False
    assert context.seasonal_normal is None
