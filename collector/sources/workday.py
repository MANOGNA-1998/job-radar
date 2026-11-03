"""Workday career sites. This is the JSON the sites' own search pages use. It is public but undocumented."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from ..http import request
from ..model import BoardNotFound, BoardResult, RawJob, parse_time, strip_html

DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAGE_SIZE = 20
PAGES_PER_SEARCH = 2


def host_for(tenant: str, dc: str) -> str:
    override = os.environ.get("JR_WORKDAY_HOST")
    return override or f"https://{tenant}.{dc}.myworkdayjobs.com"


def parse_posted_on(text: str, now: datetime | None = None) -> datetime | None:
    """Workday lists "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"."""
    now = now or datetime.now(timezone.utc)
    lowered = (text or "").lower()
    if "today" in lowered:
        return now
    if "yesterday" in lowered:
        return now - timedelta(days=1)
    match = re.search(r"(\d+)\+?\s*day", lowered)
    return now - timedelta(days=int(match.group(1))) if match else None


def fetch(company: str, tenant: str, dc: str, site: str, searches: list[str]) -> BoardResult:
    host = host_for(tenant, dc)
    api = f"{host}/wday/cxs/{tenant}/{site}"
    found: dict[str, RawJob] = {}
    for phrase in searches:
        for page in range(PAGES_PER_SEARCH):
            response = request("workday", "POST", f"{api}/jobs", json={
                "appliedFacets": {}, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": phrase,
            })
            if response.status_code in (404, 422):
                raise BoardNotFound(api)
            response.raise_for_status()
            postings = response.json().get("jobPostings") or []
            for item in postings:
                path = item.get("externalPath") or ""
                if not path or path in found:
                    continue
                found[path] = _build(company, tenant, dc, site, api, item, path)
            if len(postings) < PAGE_SIZE:
                break
    # Searches only cover part of the site, so a missing job does not mean it closed.
    return BoardResult(list(found.values()), complete=False)


def _posted(value: str | None, now: datetime) -> datetime | None:
    """Workday gives a date with no time. A recent date means posted about now, not at midnight."""
    parsed = parse_time(value)
    if parsed is None:
        return None
    if DATE_ONLY.match(value or "") and now - parsed < timedelta(days=2):
        return None
    return parsed


def _place_from_path(path: str) -> str:
    """Listings can omit the place or say "3 Locations", but the link starts with the main one."""
    parts = path.split("/")
    return re.sub(r"[-\s]+", " ", parts[2]).strip() if len(parts) > 2 and parts[1] == "job" else ""


def listed_location(text: str, path: str) -> str:
    text = (text or "").strip()
    many = re.fullmatch(r"(\d+) Locations?", text, re.I)
    if many:
        # Keeps the job until its full list of places is read, in case the first place is outside the US.
        return f"{_place_from_path(path)} | {many.group(1)} locations in total".strip(" |")
    return text or _place_from_path(path)


def _build(company, tenant, dc, site, api, item, path) -> RawJob:
    def load_description():
        response = request("workday", "GET", f"{api}{path}")
        response.raise_for_status()
        info = response.json().get("jobPostingInfo") or {}
        places = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
        place = " | ".join(p for p in places if p)
        return strip_html(info.get("jobDescription") or ""), _posted(info.get("startDate"), datetime.now(timezone.utc)), place

    estimated = parse_posted_on(item.get("postedOn") or "")
    return RawJob(
        source="workday",
        ext_id=f"{tenant}:{path.rsplit('/', 1)[-1]}",
        company=company,
        title=(item.get("title") or "").strip(),
        location=listed_location(item.get("locationsText") or "", path),
        url=f"https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}{path}",
        posted_at=estimated,
        date_basis="posted" if estimated else "none",
        load_description=load_description,
    )
