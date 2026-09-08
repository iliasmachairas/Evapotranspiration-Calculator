"""
Shared HTTP helper.

A single scheme-checked wrapper around ``urllib.request.urlopen`` used by
every network call in the plugin (CDS API, Open-Meteo). ``urlopen`` accepts
arbitrary schemes (``file://`` included), which static analysis (Bandit
B310) flags as a risk — this restricts it to ``http``/``https``.
"""

import urllib.request
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


def safe_urlopen(req, timeout=None):
    """``urlopen`` restricted to http/https URLs."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    scheme = urlparse(url).scheme
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Refusing to open URL with scheme {scheme!r}: {url}")
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 - scheme checked above
