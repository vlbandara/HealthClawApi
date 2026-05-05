from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CURRENT_CACHE_TTL_SECONDS = 1800  # 30 min
SEASONAL_NORMAL_CACHE_TTL_SECONDS = 21600  # 6 h
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_SEASONAL_LOOKBACK_YEARS = 3
_SEASONAL_WINDOW_DAYS = 15

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
    def is_severe(self) -> bool:
        return self.wmo_code >= 80

    @property
    def high_uv(self) -> bool:
        return self.uv_index > 8


@dataclass(frozen=True)
class SeasonalWeatherNormal:
    lat: float
    lon: float
    seasonal_window_start: str
    seasonal_window_end: str
    apparent_temp_mean_c: float
    apparent_temp_p90_c: float
    sample_count: int

    @property
    def heat_stress_threshold_c(self) -> float:
        return self.apparent_temp_p90_c


@dataclass(frozen=True)
class WeatherSalienceContext:
    location_confirmed: bool
    seasonal_normal: SeasonalWeatherNormal | None = None

    @property
    def has_heat_baseline(self) -> bool:
        return (
            self.location_confirmed
            and self.seasonal_normal is not None
            and self.seasonal_normal.sample_count > 0
        )


class WeatherProvider(ABC):
    @abstractmethod
    async def get_current(self, lat: float, lon: float) -> WeatherSnapshot | None:
        raise NotImplementedError

    @abstractmethod
    async def get_seasonal_normal(
        self,
        lat: float,
        lon: float,
        *,
        as_of: datetime | None = None,
    ) -> SeasonalWeatherNormal | None:
        raise NotImplementedError


class NullWeatherProvider(WeatherProvider):
    async def get_current(self, lat: float, lon: float) -> WeatherSnapshot | None:
        return None

    async def get_seasonal_normal(
        self,
        lat: float,
        lon: float,
        *,
        as_of: datetime | None = None,
    ) -> SeasonalWeatherNormal | None:
        return None


class OpenMeteoProvider(WeatherProvider):
    """Free Open-Meteo API — no key required. Caches snapshots per 0.1° grid cell."""

    def __init__(
        self,
        cache: dict[str, tuple[WeatherSnapshot, float]] | None = None,
        seasonal_cache: dict[str, tuple[SeasonalWeatherNormal, float]] | None = None,
    ) -> None:
        self._cache: dict[str, tuple[WeatherSnapshot, float]] = cache if cache is not None else {}
        self._seasonal_cache: dict[str, tuple[SeasonalWeatherNormal, float]] = (
            seasonal_cache if seasonal_cache is not None else {}
        )

    def _cache_key(self, lat: float, lon: float) -> str:
        return f"{round(lat, 1)},{round(lon, 1)}"

    def _cache_hit(self, key: str, now: float) -> WeatherSnapshot | None:
        if key in self._cache:
            snapshot, ts = self._cache[key]
            if now - ts < CURRENT_CACHE_TTL_SECONDS:
                return snapshot
        return None

    def _seasonal_cache_key(self, lat: float, lon: float, as_of: date) -> str:
        return f"{round(lat, 1)},{round(lon, 1)}:{as_of.month:02d}-{as_of.day:02d}"

    def _seasonal_cache_hit(self, key: str, now: float) -> SeasonalWeatherNormal | None:
        if key in self._seasonal_cache:
            normal, ts = self._seasonal_cache[key]
            if now - ts < SEASONAL_NORMAL_CACHE_TTL_SECONDS:
                return normal
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

    async def get_seasonal_normal(
        self,
        lat: float,
        lon: float,
        *,
        as_of: datetime | None = None,
    ) -> SeasonalWeatherNormal | None:
        import time

        anchor = (as_of or datetime.now(UTC)).astimezone(UTC).date()
        now = time.monotonic()
        key = self._seasonal_cache_key(lat, lon, anchor)
        cached = self._seasonal_cache_hit(key, now)
        if cached is not None:
            return cached

        samples: list[float] = []
        window_start: date | None = None
        window_end: date | None = None
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                for years_back in range(1, _SEASONAL_LOOKBACK_YEARS + 1):
                    center = _shift_year(anchor, years_back)
                    start_date = center - timedelta(days=_SEASONAL_WINDOW_DAYS)
                    end_date = center + timedelta(days=_SEASONAL_WINDOW_DAYS)
                    if window_start is None or start_date < window_start:
                        window_start = start_date
                    if window_end is None or end_date > window_end:
                        window_end = end_date

                    resp = await client.get(
                        _OPEN_METEO_ARCHIVE_URL,
                        params={
                            "latitude": lat,
                            "longitude": lon,
                            "start_date": start_date.isoformat(),
                            "end_date": end_date.isoformat(),
                            "hourly": ["apparent_temperature"],
                            "timezone": "UTC",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    samples.extend(_extract_apparent_temperature_samples(data))
        except Exception as exc:
            logger.warning("OpenMeteo seasonal normal fetch failed (%.2f,%.2f): %s", lat, lon, exc)
            if key in self._seasonal_cache:
                return self._seasonal_cache[key][0]
            return None

        if not samples or window_start is None or window_end is None:
            return None

        normal = SeasonalWeatherNormal(
            lat=lat,
            lon=lon,
            seasonal_window_start=window_start.isoformat(),
            seasonal_window_end=window_end.isoformat(),
            apparent_temp_mean_c=round(sum(samples) / len(samples), 2),
            apparent_temp_p90_c=round(_percentile(samples, 0.9), 2),
            sample_count=len(samples),
        )
        self._seasonal_cache[key] = (normal, now)
        return normal


_default_provider: WeatherProvider | None = None


def get_weather_provider() -> WeatherProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = OpenMeteoProvider()
    return _default_provider


async def build_weather_salience_context(
    user: Any,
    *,
    now: datetime | None = None,
    provider: WeatherProvider | None = None,
) -> WeatherSalienceContext:
    lat = getattr(user, "home_lat", None)
    lon = getattr(user, "home_lon", None)
    if lat is None or lon is None:
        return WeatherSalienceContext(location_confirmed=False)

    weather_provider = provider or get_weather_provider()
    seasonal_normal = await weather_provider.get_seasonal_normal(lat, lon, as_of=now)
    return WeatherSalienceContext(
        location_confirmed=True,
        seasonal_normal=seasonal_normal,
    )


def _extract_apparent_temperature_samples(payload: dict[str, Any]) -> list[float]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return []
    raw_values = hourly.get("apparent_temperature")
    if not isinstance(raw_values, list):
        return []
    values: list[float] = []
    for value in raw_values:
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _shift_year(day: date, years_back: int) -> date:
    try:
        return day.replace(year=day.year - years_back)
    except ValueError:
        # Leap day → Feb 28 for non-leap historical years.
        return day.replace(year=day.year - years_back, month=2, day=28)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * quantile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
