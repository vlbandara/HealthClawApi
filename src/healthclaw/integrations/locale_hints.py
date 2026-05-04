"""Locale hint inference from Telegram language_code and other cheap signals.

This module provides *hints* only — low-confidence guesses from observable signals
without any regex on free text. The companion prompt asks the user directly when
confidence is too low. Never used for authoritative decisions alone.
"""
from __future__ import annotations

from dataclasses import dataclass

# Static map: BCP-47 language tags (or prefix) → (country ISO-3166, IANA timezone, confidence)
# Confidence reflects how uniquely the language_code implies a timezone.
# Many codes are ambiguous (e.g. "en" could be anywhere) — low confidence by design.
_LANG_TO_LOCALE: dict[str, tuple[str, str, float]] = {
    # High-confidence: language spoken almost exclusively in one timezone
    "si": ("LK", "Asia/Colombo", 0.85),       # Sinhala → Sri Lanka
    "ta": ("LK", "Asia/Colombo", 0.45),        # Tamil → ambiguous (India/SG/LK)
    "ms": ("MY", "Asia/Kuala_Lumpur", 0.70),   # Malay → Malaysia (also BN/SG but dominant)
    "id": ("ID", "Asia/Jakarta", 0.80),         # Indonesian → Indonesia (WIB dominant)
    "th": ("TH", "Asia/Bangkok", 0.90),         # Thai → Thailand
    "vi": ("VN", "Asia/Ho_Chi_Minh", 0.85),    # Vietnamese → Vietnam
    "ko": ("KR", "Asia/Seoul", 0.85),           # Korean → South Korea
    "ja": ("JP", "Asia/Tokyo", 0.90),           # Japanese → Japan
    "zh": ("CN", "Asia/Shanghai", 0.60),        # Chinese → China (but also TW/SG/HK)
    "zh-tw": ("TW", "Asia/Taipei", 0.80),
    "zh-hk": ("HK", "Asia/Hong_Kong", 0.80),
    "de": ("DE", "Europe/Berlin", 0.65),
    "fr": ("FR", "Europe/Paris", 0.55),         # French — many countries
    "es": ("ES", "Europe/Madrid", 0.35),        # Spanish — many countries
    "pt": ("BR", "America/Sao_Paulo", 0.50),    # Portuguese — Brazil dominant but also PT
    "pt-br": ("BR", "America/Sao_Paulo", 0.85),
    "pt-pt": ("PT", "Europe/Lisbon", 0.85),
    "ru": ("RU", "Europe/Moscow", 0.70),
    "ar": ("SA", "Asia/Riyadh", 0.35),          # Arabic — very ambiguous
    "hi": ("IN", "Asia/Kolkata", 0.80),
    "bn": ("BD", "Asia/Dhaka", 0.70),
    "ur": ("PK", "Asia/Karachi", 0.75),
    "tr": ("TR", "Europe/Istanbul", 0.85),
    "pl": ("PL", "Europe/Warsaw", 0.90),
    "nl": ("NL", "Europe/Amsterdam", 0.70),
    "sv": ("SE", "Europe/Stockholm", 0.80),
    "no": ("NO", "Europe/Oslo", 0.80),
    "da": ("DK", "Europe/Copenhagen", 0.80),
    "fi": ("FI", "Europe/Helsinki", 0.90),
    "el": ("GR", "Europe/Athens", 0.85),
    "cs": ("CZ", "Europe/Prague", 0.90),
    "ro": ("RO", "Europe/Bucharest", 0.85),
    "uk": ("UA", "Europe/Kyiv", 0.80),
    # Low-confidence: English (spoken everywhere)
    "en": ("US", "America/New_York", 0.15),
    "en-gb": ("GB", "Europe/London", 0.75),
    "en-au": ("AU", "Australia/Sydney", 0.80),
    "en-nz": ("NZ", "Pacific/Auckland", 0.85),
    "en-ca": ("CA", "America/Toronto", 0.75),
    "en-sg": ("SG", "Asia/Singapore", 0.90),
    "en-lk": ("LK", "Asia/Colombo", 0.85),
    "en-in": ("IN", "Asia/Kolkata", 0.80),
    "en-za": ("ZA", "Africa/Johannesburg", 0.80),
}


@dataclass(frozen=True)
class LocaleHints:
    country_guess: str
    tz_guess: str
    confidence: float
    source: str  # "language_code" | "shared_location" | "none"


def infer_locale_hints(
    language_code: str | None = None,
    shared_lat: float | None = None,
    shared_lon: float | None = None,
) -> LocaleHints:
    """Return a LocaleHints from cheaply observable Telegram signals.

    Priority: shared_location > language_code > default.
    No regex anywhere — only static lookups.
    """
    # Shared location from Telegram location button → maximum confidence
    if shared_lat is not None and shared_lon is not None:
        tz = _tz_from_latlon(shared_lat, shared_lon)
        return LocaleHints(
            country_guess=_country_from_latlon(shared_lat, shared_lon),
            tz_guess=tz,
            confidence=1.0,
            source="shared_location",
        )

    # Language code lookup
    if language_code:
        code = language_code.lower().strip()
        # Try exact match first, then prefix (e.g. "en-US" → "en-us" → "en")
        entry = _LANG_TO_LOCALE.get(code)
        if entry is None:
            prefix = code.split("-")[0]
            entry = _LANG_TO_LOCALE.get(prefix)
        if entry is not None:
            country, tz, conf = entry
            return LocaleHints(
                country_guess=country,
                tz_guess=tz,
                confidence=conf,
                source="language_code",
            )

    return LocaleHints(
        country_guess="US",
        tz_guess="UTC",
        confidence=0.0,
        source="none",
    )


def _tz_from_latlon(lat: float, lon: float) -> str:
    """Best-effort timezone from lat/lon using timezonefinder if available, else rough grid."""
    try:
        from timezonefinder import TimezoneFinder  # optional dep
        tf = TimezoneFinder()
        result = tf.timezone_at(lat=lat, lng=lon)
        if result:
            return result
    except ImportError:
        pass
    # Rough fallback: use longitude to estimate UTC offset
    offset_hours = round(lon / 15)
    if offset_hours == 0:
        return "UTC"
    sign = "+" if offset_hours >= 0 else "-"
    return f"Etc/GMT{sign}{abs(offset_hours)}"


def _country_from_latlon(lat: float, lon: float) -> str:
    """Very rough country guess from lat/lon bounding boxes."""
    # Not used for anything authoritative — just hints
    if 5.0 <= lat <= 10.0 and 79.5 <= lon <= 82.0:
        return "LK"  # Sri Lanka
    if 1.0 <= lat <= 1.6 and 103.5 <= lon <= 104.1:
        return "SG"  # Singapore
    if 6.0 <= lat <= 37.0 and 68.0 <= lon <= 98.0:
        return "IN"  # India (rough)
    return "?"
