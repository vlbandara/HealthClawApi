"""Tests for integrations/weather.py — provider, caching, and snapshot properties."""
from __future__ import annotations

import pytest

from healthclaw.integrations.weather import NullWeatherProvider, OpenMeteoProvider, WeatherSnapshot


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


def test_heat_stress_property():
    snap = _snapshot(temp_c=33, humidity=82)
    assert snap.is_heat_stress is True


def test_no_heat_stress_below_threshold():
    snap = _snapshot(temp_c=28, humidity=60)
    assert snap.is_heat_stress is False


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
    import httpx

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
