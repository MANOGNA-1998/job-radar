"""HTTP with a polite per-system pace, retries, and overridable hosts for tests."""
from __future__ import annotations

import os
import threading
import time

import requests

HOSTS = {
    "greenhouse": os.environ.get("JR_GREENHOUSE_HOST", "https://boards-api.greenhouse.io"),
    "lever": os.environ.get("JR_LEVER_HOST", "https://api.lever.co"),
    "ashby": os.environ.get("JR_ASHBY_HOST", "https://api.ashbyhq.com"),
    "apify": os.environ.get("JR_APIFY_HOST", "https://api.apify.com"),
    "jsearch": os.environ.get("JR_JSEARCH_HOST", "https://jsearch.p.rapidapi.com"),
}
# Seconds between requests to one system. Lever publishes a 1 request per second crawl delay.
PACE = {"greenhouse": 0.2, "lever": 1.1, "ashby": 0.2, "workday": 0.5}
USER_AGENT = "job-radar/1.0 (personal job search tool)"

_locks = {name: threading.Lock() for name in PACE}
_last = {name: 0.0 for name in PACE}
_session = threading.local()


def _client() -> requests.Session:
    if not hasattr(_session, "client"):
        _session.client = requests.Session()
        _session.client.headers["User-Agent"] = USER_AGENT
    return _session.client


def _wait(system: str) -> None:
    if system not in _locks:
        return
    with _locks[system]:
        delay = _last[system] + PACE[system] - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last[system] = time.monotonic()


def request(system: str, method: str, url: str, *, timeout: float = 30, **kwargs) -> requests.Response:
    last_error = None
    for attempt in range(3):
        _wait(system)
        try:
            response = _client().request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2.0 * (attempt + 1))
            continue
        return response
    raise last_error  # type: ignore[misc]
