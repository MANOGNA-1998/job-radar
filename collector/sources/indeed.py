"""Indeed search through an Apify scraper, using logged-out pages only. Paid per result, at a small fraction of a cent."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..http import HOSTS, request
from ..model import RawJob, parse_time, strip_html

PRICE_PER_RESULT = 0.0001
HOUR_MS = 3_600_000


def posted_within_days(seconds: int) -> str:
    """Indeed only accepts 1, 3, 7, or 14 days. Jobs already collected are recognized and not repeated."""
    for days in (1, 3, 7):
        if seconds <= days * 86400:
            return str(days)
    return "14"


def _posted(value, now: datetime) -> tuple[datetime | None, str]:
    """Some dates have a real time. Others land exactly on an hour boundary, which means only the day is known."""
    if not isinstance(value, (int, float)):
        return None, "alert"
    posted = parse_time(value)
    if value % HOUR_MS == 0 and now - posted < timedelta(days=2):
        return None, "alert"
    return posted, "posted"


def to_job(item: dict, now: datetime) -> RawJob | None:
    job_id, title = item.get("id"), (item.get("title") or "").strip()
    company = ((item.get("companyDetails") or {}).get("name") or item.get("companyDetails.name") or "").strip()
    country = ((item.get("location") or {}).get("countryCode") or item.get("location.countryCode") or "US").upper()
    # Some listings hide the employer. Without a name they can't be told apart or de-duplicated, so they are skipped.
    if not job_id or not title or not company or country != "US" or item.get("expired"):
        return None
    posted, basis = _posted(item.get("pubDate"), now)
    place = (item.get("formattedLocation") or "").strip()
    apply_url = item.get("originalApplyUrl") or ""
    return RawJob(
        source="indeed",
        ext_id=str(job_id),
        company=company,
        title=title,
        location=place,
        url=f"https://www.indeed.com/viewjob?jk={job_id}",
        description=item.get("jobDescription") or strip_html(item.get("jobDescriptionHTML") or ""),
        posted_at=posted or now,
        date_basis=basis,
        apply_links=[apply_url] if apply_url.startswith("https://") else [],
    )


def search(token: str, actor: str, keywords: str, location: str, seconds: int, limit: int, now: datetime) -> list[RawJob]:
    response = request(
        "apify", "POST",
        f"{HOSTS['apify']}/v2/acts/{actor}/run-sync-get-dataset-items",
        headers={"Authorization": f"Bearer {token}"},
        params={"timeout": 240, "maxTotalChargeUsd": round(limit * PRICE_PER_RESULT + 0.01, 4)},
        json={"country": "us", "query": keywords, "location": location, "postedWithinDays": posted_within_days(seconds), "count": limit},
        timeout=280,
    )
    response.raise_for_status()
    return [job for job in (to_job(item, now) for item in response.json()) if job]
