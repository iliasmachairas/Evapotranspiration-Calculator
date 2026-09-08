"""
Hargreaves–Samani reference evapotranspiration (ET₀) calculation.

Implements:
  ET₀ = 0.0023 × (T_mean + 17.8) × (T_max − T_min)^0.5 × Ra

where Ra is extraterrestrial radiation computed from latitude and day-of-year
following the FAO-56 methodology (Allen et al., 1998).
"""

import math
import numpy as np
import pandas as pd


# ---------- physical constants ----------
GSC = 0.0820          # solar constant  [MJ m⁻² min⁻¹]
LAMBDA = 2.45         # latent heat of vaporisation  [MJ kg⁻¹]


def _deg2rad(deg: float) -> float:
    return deg * math.pi / 180.0


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """
    Daily extraterrestrial radiation Ra  [mm day⁻¹].

    Parameters
    ----------
    lat_deg : float
        Latitude in decimal degrees (positive north).
    doy : int
        Day of year (1–366).

    Returns
    -------
    float
        Ra in equivalent mm day⁻¹ of evaporation.
    """
    phi = _deg2rad(lat_deg)

    # inverse relative distance Earth–Sun
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)

    # solar declination  [rad]
    delta = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)

    # sunset hour angle  [rad]
    ws_arg = -math.tan(phi) * math.tan(delta)
    ws_arg = max(-1.0, min(1.0, ws_arg))  # clamp for polar regions
    ws = math.acos(ws_arg)

    # Ra in MJ m⁻² day⁻¹
    ra_mj = (
        (24.0 * 60.0 / math.pi)
        * GSC
        * dr
        * (ws * math.sin(phi) * math.sin(delta)
           + math.cos(phi) * math.cos(delta) * math.sin(ws))
    )

    # convert to mm day⁻¹
    return max(ra_mj / LAMBDA, 0.0)


def hargreaves_et0(t_max: float, t_min: float, t_mean: float,
                   ra: float) -> float:
    """
    Hargreaves–Samani ET₀  [mm day⁻¹].

    Parameters
    ----------
    t_max, t_min, t_mean : float
        Daily max / min / mean 2 m air temperature [°C].
    ra : float
        Extraterrestrial radiation [mm day⁻¹].

    Returns
    -------
    float
        Reference evapotranspiration ET₀ [mm day⁻¹].
    """
    delta_t = t_max - t_min
    if delta_t < 0:
        delta_t = 0.0
    return 0.0023 * (t_mean + 17.8) * math.sqrt(delta_t) * ra


# ------------------------------------------------------------------
# High-level helpers
# ------------------------------------------------------------------

def compute_et0_from_daily(df: pd.DataFrame, lat_deg: float) -> pd.DataFrame:
    """
    Compute daily ET₀ from a DataFrame that already has daily temps (°C).

    Parameters
    ----------
    df : pd.DataFrame
        Must have columns ``date`` (datetime), ``t_max``, ``t_min``,
        ``t_mean`` — all in **°C**.
    lat_deg : float
        Latitude of the point in decimal degrees.

    Returns
    -------
    pd.DataFrame
        Same columns plus ``ra`` [mm day⁻¹] and ``et0`` [mm day⁻¹].
    """
    df = df.copy()
    ra_vals, et0_vals = [], []

    for _, row in df.iterrows():
        doy = row["date"].timetuple().tm_yday
        ra = extraterrestrial_radiation(lat_deg, doy)
        et0 = hargreaves_et0(row["t_max"], row["t_min"],
                             row["t_mean"], ra)
        ra_vals.append(round(ra, 3))
        et0_vals.append(round(et0, 3))

    df["ra"] = ra_vals
    df["et0"] = et0_vals
    return df


def compute_daily_et0(df: pd.DataFrame, lat_deg: float) -> pd.DataFrame:
    """
    Compute daily ET₀ from an hourly temperature DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Must have columns ``date`` (datetime) and ``t2m`` (2 m temperature
        in **Kelvin**).  Typically 24 rows per day.
    lat_deg : float
        Latitude of the point in decimal degrees.

    Returns
    -------
    pd.DataFrame
        Columns: ``date``, ``t_max``, ``t_min``, ``t_mean`` (all °C),
        ``ra`` [mm day⁻¹], ``et0`` [mm day⁻¹].
    """
    # Kelvin → Celsius
    df = df.copy()
    df["t2m_c"] = df["t2m"] - 273.15

    # aggregate to daily
    daily = (
        df.groupby(df["date"].dt.date)["t2m_c"]
        .agg(t_max="max", t_min="min", t_mean="mean")
        .reset_index()
    )
    daily.rename(columns={"date": "date"}, inplace=True)
    daily["date"] = pd.to_datetime(daily["date"])

    return compute_et0_from_daily(daily, lat_deg)
