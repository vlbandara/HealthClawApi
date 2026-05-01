from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 1800  # 30 min
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes → human-readable condition
_WMO_CONDITIONS: dict[int, str] = {
    0: "clear", 1: "mainly_clear", 2: "partly_cloudy", 3: "overcast",
    45: "fog", 48: "icy_fog",
    51: "light_drizzle", 53: "moderate_drizzle", 55: "dense_drizzle",
    61: "light_rain", 63: "moderate_rain", 65: "heavy_rain",
    71: "light_snow", 73: "moderate_snow", 75: "heavy_snow",
    80: "light_showers", 81: "moderate_showers", 82: "heavy_showers",
    95: "thunderstorm", 96: "thunderstorm_with_hail", 99: "heavy_thunderstorm",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    lat: float
    lon: float
    temp_c: float
    feels_like_c: float
    humidity_pct: int
    condition: str
    wmo_code: int
    uv_index: float
    fetched_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_heat_stress(self) -> bool:
        return self.temp_c > 31 and self.humidity_pct > 70

    @property
    def is_severe(self) -> bool:
        return self.wmo_code >= 80

    @property
    def high_uv(self) -> bool:
        return self.uv_index > 8


class WeatherProvider(ABC):
    @abstractmethod
    async def get_current(self, lat: float, lon: float) -> WeatherSnapshot | None:
        raise NotImplementedError


class NullWeatherProvider(WeatherProvider):
    async def get_current(self, lat: float, lon: float) -> WeatherSnapshot | None:
        return None


class OpenMeteoProvider(WeatherProvider):
    """Free Open-Meteo API — no key required. Caches snapshots per 0.1° grid cell."""

    def __init__(self, cache: dict[str, tuple[WeatherSnapshot, float]] | None = None) -> None:
        self._cache: dict[str, tuple[WeatherSnapshot, float]] = cache if cache is not None else {}

    def _cache_key(self, lat: float, lon: float) -> str:
        return f"{round(lat, 1)},{round(lon, 1)}"

    def _cache_hit(self, key: str, now: float) -> WeatherSnapshot | None:
        if key in self._cache:
            snapshot, ts = self._cache[key]
            if now - ts < CACHE_TTL_SECONDS:
                return snapshot
        return None

    async def get_current(self, lat: float, lon: float) -> WeatherSnapshot | None:
        import time

        now = time.monotonic()
        key = self._cache_key(lat, lon)
        cached = self._cache_hit(key, now)
        if cached is not None:
            return cached

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    _OPEN_METEO_URL,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": [
                            "temperature_2m",
                            "apparent_temperature",
                            "relative_humidity_2m",
                            "weather_code",
                            "uv_index",
                        ],
                        "timezone": "UTC",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("OpenMeteo fetch failed (%.2f,%.2f): %s", lat, lon, exc)
            if key in self._cache:
                return self._cache[key][0]
            return None

        current = data.get("current", {})
        wmo_code = int(current.get("weather_code", 0))
        snapshot = WeatherSnapshot(
            lat=lat,
            lon=lon,
            temp_c=float(current.get("temperature_2m", 0)),
            feels_like_c=float(current.get("apparent_temperature", 0)),
            humidity_pct=int(current.get("relative_humidity_2m", 0)),
            condition=_WMO_CONDITIONS.get(wmo_code, "unknown"),
            wmo_code=wmo_code,
            uv_index=float(current.get("uv_index", 0)),
            fetched_at=datetime.now(UTC).isoformat(),
        )
        self._cache[key] = (snapshot, now)
        return snapshot


_default_provider: WeatherProvider | None = None


def get_weather_provider() -> WeatherProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = OpenMeteoProvider()
    return _default_provider
