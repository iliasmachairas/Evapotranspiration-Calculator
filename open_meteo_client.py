"""
Open-Meteo Archive API client — fetches ERA5 daily temperature data.

**Zero dependencies** — uses only ``urllib.request`` + ``json`` (stdlib).
No API key required. Response in ~2 seconds.

Data source: ERA5 reanalysis (1940–present, 5-day delay) seamlessly
blended with ECMWF IFS (2017–present, no delay) to cover up to today.

API docs: https://open-meteo.com/en/docs/historical-weather-api
"""

import json
import urllib.request
import urllib.error
from datetime import date

import pandas as pd

from .http_utils import safe_urlopen

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class OpenMeteoError(Exception):
    """Raised on any Open-Meteo download / parsing failure."""


def download_daily_temperature(
    lat: float,
    lon: float,
    start_date: date,
    end_date: date,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Fetch daily Tmax / Tmin / Tmean from Open-Meteo (ERA5).

    Parameters
    ----------
    lat, lon : float
        Point coordinates in decimal degrees (WGS-84).
    start_date, end_date : date
        Inclusive date range.
    progress_callback : callable, optional
        Called with ``(message: str)`` to report progress.

    Returns
    -------
    pd.DataFrame
        Columns: ``date`` (datetime64), ``t_max``, ``t_min``,
        ``t_mean`` — all temperatures in °C.

    Raises
    ------
    OpenMeteoError
        On network errors or unexpected API responses.
    """
    def _msg(text):
        if progress_callback:
            progress_callback(text)

    params = (
        f"latitude={lat:.6f}"
        f"&longitude={lon:.6f}"
        f"&start_date={start_date.isoformat()}"
        f"&end_date={end_date.isoformat()}"
        f"&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean"
        f"&timezone=UTC"
    )
    url = f"{ARCHIVE_URL}?{params}"

    _msg("Fetching ERA5 temperature data from Open-Meteo…")

    try:
        req = urllib.request.Request(url)
        with safe_urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise OpenMeteoError(
            f"Open-Meteo returned HTTP {exc.code}:\n{detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise OpenMeteoError(
            f"Could not connect to Open-Meteo:\n{exc.reason}"
        ) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OpenMeteoError(f"Invalid JSON from Open-Meteo:\n{exc}") from exc

    # Check for API-level errors
    if "error" in data and data["error"]:
        reason = data.get("reason", str(data))
        raise OpenMeteoError(f"Open-Meteo error:\n{reason}")

    # Parse daily block
    daily = data.get("daily")
    if not daily:
        raise OpenMeteoError(
            "Open-Meteo response has no 'daily' block.\n"
            + json.dumps(data, indent=2)[:400]
        )

    times = daily.get("time", [])
    t_max = daily.get("temperature_2m_max", [])
    t_min = daily.get("temperature_2m_min", [])
    t_mean = daily.get("temperature_2m_mean", [])

    if not times:
        raise OpenMeteoError("Open-Meteo returned no time values.")

    df = pd.DataFrame({
        "date": pd.to_datetime(times),
        "t_max": t_max,
        "t_min": t_min,
        "t_mean": t_mean,
    })

    # Drop rows with missing temperature data
    df = df.dropna(subset=["t_max", "t_min", "t_mean"]).reset_index(drop=True)

    _msg(f"Received {len(df)} days of temperature data.")
    return df
