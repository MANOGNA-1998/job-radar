"""JSearch, which reads Google for Jobs. Free tier: 200 requests a month."""
from __future__ import annotations

from datetime import datetime

from ..http import HOSTS, request
from ..model import RawJob, parse_time


def date_filter(seconds: int) -> str:
    # This API only offers broad date ranges. Jobs already collected are recognized and not repeated.
    if seconds <= 86400:
        return "today"
    if seconds <= 3 * 86400:
        return "3days"
    return "week"


def to_job(item: dict, now: datetime) -> RawJob | None:
    job_id, title, company = item.get("job_id"), item.get("job_title"), item.get("employer_name")
    if not job_id or not title or not company:
        return None
    if (item.get("job_country") or "US").upper() not in ("US", "USA"):
        return None
    options = item.get("apply_options") or []
    direct = [o.get("apply_link") for o in options if o.get("is_direct") and o.get("apply_link")]
    main = item.get("job_apply_link") or ""
    url = direct[0] if direct else main
    place = ", ".join(x for x in (item.get("job_city"), item.get("job_state")) if x)
    if item.get("job_is_remote"):
        place = f"{place} (Remote)".strip() if place else "Remote"
    posted = parse_time(item.get("job_posted_at_datetime_utc"))
    return RawJob(
        source="jsearch",
        ext_id=str(job_id),
        company=company.strip(),
        title=title.strip(),
        location=place or "United States",
        url=url,
        description=item.get("job_description") or "",
        posted_at=posted or now,
        date_basis="posted" if posted else "alert",
        apply_links=[link for link in [main, *[o.get("apply_link") for o in options]] if link],
    )


def search(key: str, phrase: str, seconds: int, now: datetime) -> list[RawJob]:
    response = request(
        "jsearch", "GET", f"{HOSTS['jsearch']}/search",
        headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
        params={"query": f"{phrase} in United States", "page": 1, "num_pages": 1, "country": "us", "date_posted": date_filter(seconds)},
    )
    response.raise_for_status()
    return [job for job in (to_job(item, now) for item in response.json().get("data") or []) if job]
