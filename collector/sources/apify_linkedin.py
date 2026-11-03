"""LinkedIn search through an Apify scraper, using logged-out pages only. Paid per result."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from ..http import HOSTS, request
from ..model import RawJob, parse_time, strip_html

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRICE_PER_RESULT = 0.002


def _first(item: dict, *names: str) -> str:
    for name in names:
        value = item.get(name)
        if value:
            return str(value)
    return ""


def _posted(value: str, now: datetime) -> tuple[datetime | None, str]:
    """LinkedIn gives a date with no time. A recent date means it was just found, not posted at midnight."""
    parsed = parse_time(value)
    if parsed is None:
        return None, "alert"
    if DATE_ONLY.match(value):
        if now - parsed < timedelta(days=2):
            return None, "alert"
        return parsed + timedelta(hours=12), "posted"
    return parsed, "posted"


def to_job(item: dict, now: datetime) -> RawJob | None:
    link = _first(item, "link", "jobUrl", "url")
    match = re.search(r"(\d{6,})", _first(item, "id", "jobId") or link)
    title = _first(item, "title", "jobTitle")
    if not match or not title:
        return None
    posted, basis = _posted(_first(item, "postedAt", "publishedAt"), now)
    apply_url = _first(item, "applyUrl")
    return RawJob(
        source="linkedin",
        ext_id=match.group(1),
        company=_first(item, "companyName", "company") or "Unknown company",
        title=title.strip(),
        location=_first(item, "location", "jobLocation"),
        url=f"https://www.linkedin.com/jobs/view/{match.group(1)}",
        description=_first(item, "descriptionText") or strip_html(_first(item, "descriptionHtml", "description")),
        posted_at=posted or now,
        date_basis=basis,
        apply_links=[apply_url] if apply_url else [],
    )


def search_url(keywords: str, location: str, seconds: int) -> str:
    query = urlencode({"keywords": keywords, "location": location, "f_TPR": f"r{seconds}", "sortBy": "DD"}, quote_via=quote)
    return f"https://www.linkedin.com/jobs/search/?{query}"


def search(token: str, actor: str, keywords: str, location: str, seconds: int, limit: int, now: datetime) -> list[RawJob]:
    response = request(
        "apify", "POST",
        f"{HOSTS['apify']}/v2/acts/{actor}/run-sync-get-dataset-items",
        headers={"Authorization": f"Bearer {token}"},
        # The charge ceiling is a second safeguard in case the limit below is ever ignored.
        params={"timeout": 240, "maxTotalChargeUsd": round(limit * PRICE_PER_RESULT + 0.01, 3)},
        json={"urls": [search_url(keywords, location, seconds)], "limitPerSource": limit, "scrapeCompany": False},
        timeout=280,
    )
    response.raise_for_status()
    return [job for job in (to_job(item, now) for item in response.json()) if job]
