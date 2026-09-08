"""
CDS API client — downloads ERA5 hourly 2 m temperature for a single point.

**Zero external dependencies** — uses only:
  • ``urllib.request``  for HTTP calls to the CDS REST API
  • ``osgeo.gdal``      for reading the downloaded NetCDF file
  • ``numpy / pandas``   for data wrangling (both ship with QGIS)

Compatible with the *new* Copernicus Climate Data Store API
(https://cds.climate.copernicus.eu  — OGC Processes style).
"""

import json
import os
import tempfile
import time as _time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from osgeo import gdal

from .http_utils import safe_urlopen

# Suppress GDAL warnings about unrecognised NetCDF attributes
gdal.UseExceptions()
gdal.SetConfigOption("CPL_LOG", "OFF")


CDS_API_URL = "https://cds.climate.copernicus.eu/api"
DATASET = "reanalysis-era5-single-levels"
POLL_INTERVAL = 10          # seconds between job-status checks
MAX_POLL_MINUTES = 30       # give up after this long


class CDSDownloadError(Exception):
    """Raised on any CDS download / parsing failure."""


# ======================================================================
# Public entry-point
# ======================================================================

def download_era5_temperature(
    api_key: str,
    lat: float,
    lon: float,
    start_date: date,
    end_date: date,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Download ERA5 hourly 2 m temperature → DataFrame.

    Returns
    -------
    pd.DataFrame
        Columns ``date`` (datetime64) and ``t2m`` (Kelvin).
    """
    def _msg(text):
        if progress_callback:
            progress_callback(text)

    # --- build CDS request body ---
    margin = 0.15  # ±0.15° around the point (captures nearest 0.25° cell)
    area = [
        round(lat + margin, 2),
        round(lon - margin, 2),
        round(lat - margin, 2),
        round(lon + margin, 2),
    ]

    all_dates = list(_daterange(start_date, end_date))
    years  = sorted({str(d.year)       for d in all_dates})
    months = sorted({f"{d.month:02d}"  for d in all_dates})
    days   = sorted({f"{d.day:02d}"    for d in all_dates})
    times  = [f"{h:02d}:00" for h in range(24)]

    request_body = {
        "product_type": ["reanalysis"],
        "variable":     ["2m_temperature"],
        "year":   years,
        "month":  months,
        "day":    days,
        "time":   times,
        "data_format": "netcdf",
        "area": area,
    }

    # --- submit → poll → download ---
    tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        _msg("Submitting request to CDS…")
        _cds_retrieve(api_key, request_body, tmp_path, _msg)

        _msg("Reading NetCDF with GDAL…")
        df = _read_netcdf_gdal(tmp_path, lat, lon, start_date, end_date)
        _msg(f"Got {len(df)} hourly records.")
        return df

    except CDSDownloadError:
        raise
    except Exception as exc:
        raise CDSDownloadError(str(exc)) from exc
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ======================================================================
# CDS REST helpers  (replaces cdsapi package)
# ======================================================================

def _cds_headers(api_key: str, content_type: str = None) -> dict:
    h = {"PRIVATE-TOKEN": api_key}
    if content_type:
        h["Content-Type"] = content_type
    return h


def _http_get_json(url: str, api_key: str) -> dict:
    req = urllib.request.Request(url, headers=_cds_headers(api_key))
    with safe_urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cds_retrieve(api_key: str, request_body: dict,
                  target_path: str, msg) -> None:
    """Submit a CDS retrieve job, poll until done, download the file."""

    # 1) Submit ---------------------------------------------------------
    url = f"{CDS_API_URL}/retrieve/v1/processes/{DATASET}/execution"
    payload = json.dumps({"inputs": request_body}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers=_cds_headers(api_key, "application/json"),
        method="POST",
    )

    try:
        resp = safe_urlopen(req, timeout=120)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CDSDownloadError(
            f"CDS returned HTTP {exc.code}.\n{body}"
        ) from exc

    status_code = resp.status
    resp_body = resp.read().decode("utf-8")
    resp_data = json.loads(resp_body) if resp_body.strip() else {}

    # 2) Determine job URL / direct download ----------------------------
    if status_code == 200:
        # Rarely: direct result (small request)
        _download_url_to_file(resp.url, api_key, target_path, msg)
        return

    # 201 / 202 → async job
    job_url = resp.headers.get("Location", "")
    if not job_url and "jobID" in resp_data:
        job_url = f"{CDS_API_URL}/retrieve/v1/jobs/{resp_data['jobID']}"
    if not job_url.startswith("http"):
        job_url = f"{CDS_API_URL}{job_url}" if job_url.startswith("/") \
                  else f"{CDS_API_URL}/{job_url}"

    # 3) Poll -----------------------------------------------------------
    deadline = _time.time() + MAX_POLL_MINUTES * 60
    while _time.time() < deadline:
        _time.sleep(POLL_INTERVAL)
        status_data = _http_get_json(job_url, api_key)
        status = status_data.get("status", "unknown")

        if status == "successful":
            msg("CDS job finished — downloading data…")
            break
        elif status == "failed":
            detail = status_data.get("message",
                     json.dumps(status_data, indent=2))
            raise CDSDownloadError(f"CDS job failed:\n{detail}")
        else:
            msg(f"CDS job status: {status}…")
    else:
        raise CDSDownloadError(
            f"CDS job did not finish within {MAX_POLL_MINUTES} minutes."
        )

    # 4) Get download URL from results ----------------------------------
    results_url = f"{job_url}/results"
    results = _http_get_json(results_url, api_key)

    download_url = _extract_download_url(results)
    if not download_url:
        raise CDSDownloadError(
            "Could not find a download URL in CDS results:\n"
            + json.dumps(results, indent=2)[:600]
        )

    # 5) Download -------------------------------------------------------
    _download_url_to_file(download_url, api_key, target_path, msg)


def _extract_download_url(results: dict) -> Optional[str]:
    """Try several known result-JSON layouts."""
    # Layout 1  {"asset": {"value": {"href": "..."}}}
    try:
        return results["asset"]["value"]["href"]
    except (KeyError, TypeError):
        pass
    # Layout 2  list of links with rel=download
    for link in results.get("links", []):
        if link.get("rel") in ("download", "results"):
            return link["href"]
    # Layout 3  flat "href" key
    if "href" in results:
        return results["href"]
    return None


def _download_url_to_file(url: str, api_key: str,
                          target_path: str, msg) -> None:
    req = urllib.request.Request(url, headers=_cds_headers(api_key))
    with safe_urlopen(req, timeout=600) as resp:
        total = resp.headers.get("Content-Length")
        downloaded = 0
        with open(target_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // int(total)
                    msg(f"Downloading… {pct}%")
    msg("Download complete.")


# ======================================================================
# NetCDF reader  (GDAL — always present in QGIS)
# ======================================================================

def _read_netcdf_gdal(nc_path: str, target_lat: float, target_lon: float,
                      start_date: date, end_date: date) -> pd.DataFrame:
    """
    Read the ERA5 NetCDF using GDAL and return a tidy DataFrame.
    """
    # Open the top-level file to inspect subdatasets
    ds_main = gdal.Open(nc_path, gdal.GA_ReadOnly)
    if ds_main is None:
        raise CDSDownloadError(f"GDAL cannot open {nc_path}")

    subdatasets = ds_main.GetSubDatasets()

    # Find the t2m subdataset
    t2m_ds = None
    for sd_name, _sd_desc in subdatasets:
        if ":t2m" in sd_name or ":2m_temperature" in sd_name:
            t2m_ds = gdal.Open(sd_name, gdal.GA_ReadOnly)
            break

    if t2m_ds is None:
        # File might have a single variable → use main dataset
        if ds_main.RasterCount > 0:
            t2m_ds = ds_main
        else:
            raise CDSDownloadError(
                "Cannot find 2 m temperature variable in the NetCDF file."
            )

    n_bands = t2m_ds.RasterCount
    gt = t2m_ds.GetGeoTransform()
    nx, ny = t2m_ds.RasterXSize, t2m_ds.RasterYSize

    # Nearest pixel to target point
    col = int(round((target_lon - gt[0]) / gt[1] - 0.5))
    row = int(round((target_lat - gt[3]) / gt[5] - 0.5))
    col = max(0, min(col, nx - 1))
    row = max(0, min(row, ny - 1))

    # ---- timestamps ---------------------------------------------------
    meta = t2m_ds.GetMetadata_Dict()

    # discover time-dimension name
    time_dim = None
    extra = meta.get("NETCDF_DIM_EXTRA", "")
    if extra:
        time_dim = extra.strip("{}").split(",")[0].strip()
    if not time_dim:
        for candidate in ("valid_time", "time"):
            if f"NETCDF_DIM_{candidate}_VALUES" in meta:
                time_dim = candidate
                break
    if not time_dim:
        time_dim = "time"  # fallback

    # read raw time values
    values_key = f"NETCDF_DIM_{time_dim}_VALUES"
    if values_key in meta:
        raw_times = [float(v) for v in
                     meta[values_key].strip("{}").split(",")]
    else:
        # fall back to per-band metadata
        raw_times = []
        for i in range(1, n_bands + 1):
            bm = t2m_ds.GetRasterBand(i).GetMetadata_Dict()
            val = bm.get(f"NETCDF_DIM_{time_dim}")
            raw_times.append(float(val) if val else float(i))

    # determine units and convert to datetime
    units_key = f"{time_dim}#units"
    units_str = meta.get(units_key, meta.get("time#units", ""))

    timestamps = _time_values_to_datetime(raw_times, units_str)

    # ---- read temperature raster per band -----------------------------
    temps = np.empty(n_bands, dtype=np.float64)
    for i in range(n_bands):
        band = t2m_ds.GetRasterBand(i + 1)
        arr = band.ReadAsArray(col, row, 1, 1)
        temps[i] = float(arr[0, 0])

    # clean up GDAL handles
    t2m_ds = None
    ds_main = None

    df = pd.DataFrame({"date": timestamps, "t2m": temps})

    # filter to requested date range
    df = df[
        (df["date"].dt.date >= start_date)
        & (df["date"].dt.date <= end_date)
    ].reset_index(drop=True)

    return df


def _time_values_to_datetime(raw, units_str: str) -> list:
    """Convert NetCDF numeric time values to Python datetimes."""
    # "hours since 1900-01-01 00:00:00.0"
    if "since" in units_str:
        parts = units_str.split("since")
        step = parts[0].strip().lower()
        ref_str = parts[1].strip().split(".")[0]  # drop fractional sec
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                ref = datetime.strptime(ref_str, fmt)
                break
            except ValueError:
                continue
        else:
            ref = datetime(1900, 1, 1)

        if "hour" in step:
            return [ref + timedelta(hours=float(v)) for v in raw]
        elif "second" in step:
            return [ref + timedelta(seconds=float(v)) for v in raw]
        elif "day" in step:
            return [ref + timedelta(days=float(v)) for v in raw]
        elif "minute" in step:
            return [ref + timedelta(minutes=float(v)) for v in raw]

    # auto-detect: large values (> year-2000 in seconds) → Unix epoch
    if raw and raw[0] > 1e9:
        return [datetime.utcfromtimestamp(v) for v in raw]

    # default: hours since 1900-01-01 (classic ERA5)
    epoch = datetime(1900, 1, 1)
    return [epoch + timedelta(hours=float(v)) for v in raw]


# ======================================================================
# Helpers
# ======================================================================

def _daterange(start: date, end: date):
    """Yield each date in [start, end]."""
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)
