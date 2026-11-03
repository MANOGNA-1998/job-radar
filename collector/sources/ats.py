"""Greenhouse, Lever and Ashby public job board APIs. None of them need a key."""
from __future__ import annotations

from ..http import HOSTS, request
from ..model import BoardNotFound, BoardResult, RawJob, parse_time, strip_html


def _json(system: str, url: str, **kwargs):
    response = request(system, "GET", url, **kwargs)
    if response.status_code == 404:
        raise BoardNotFound(url)
    response.raise_for_status()
    return response.json()


def _company(slug: str, given: str | None) -> str:
    return given or slug.replace("-", " ").replace("_", " ").title()


def greenhouse(slug: str, company: str | None = None) -> BoardResult:
    data = _json("greenhouse", f"{HOSTS['greenhouse']}/v1/boards/{slug}/jobs", params={"content": "true"})
    jobs = []
    for item in data.get("jobs", []):
        published = parse_time(item.get("first_published"))
        updated = parse_time(item.get("updated_at"))
        jobs.append(RawJob(
            source="greenhouse",
            ext_id=f"{slug}:{item['id']}",
            company=_company(slug, company or item.get("company_name")),
            title=item.get("title", "").strip(),
            location=((item.get("location") or {}).get("name") or "").strip(),
            url=item.get("absolute_url", ""),
            description=strip_html(item.get("content", "")),
            posted_at=published or updated,
            date_basis="posted" if published else ("updated" if updated else "none"),
        ))
    return BoardResult(jobs)


def lever(slug: str, company: str | None = None) -> BoardResult:
    data = _json("lever", f"{HOSTS['lever']}/v0/postings/{slug}", params={"mode": "json"})
    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location") or ""]
        location = " | ".join(x for x in locations if x)
        if item.get("workplaceType") == "remote" and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        sections = [item.get("descriptionPlain") or ""]
        for section in item.get("lists") or []:
            sections.append(f"{section.get('text', '')}\n{strip_html(section.get('content', ''))}")
        sections.append(item.get("additionalPlain") or "")
        created = parse_time(item.get("createdAt"))
        jobs.append(RawJob(
            source="lever",
            ext_id=f"{slug}:{item['id']}",
            company=_company(slug, company),
            title=(item.get("text") or "").strip(),
            location=location,
            url=item.get("hostedUrl") or item.get("applyUrl") or "",
            description="\n".join(s for s in sections if s).strip(),
            posted_at=created,
            date_basis="posted" if created else "none",
        ))
    return BoardResult(jobs)


def ashby(slug: str, company: str | None = None) -> BoardResult:
    data = _json("ashby", f"{HOSTS['ashby']}/posting-api/job-board/{slug}", params={"includeCompensation": "false"})
    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        places = [item.get("location") or ""]
        for extra in item.get("secondaryLocations") or []:
            places.append(extra.get("location") or "")
        location = " | ".join(p for p in places if p)
        if (item.get("isRemote") or item.get("workplaceType") == "Remote") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        published = parse_time(item.get("publishedAt"))
        jobs.append(RawJob(
            source="ashby",
            ext_id=f"{slug}:{item['id']}",
            company=_company(slug, company),
            title=(item.get("title") or "").strip(),
            location=location,
            url=item.get("jobUrl") or item.get("applyUrl") or "",
            description=item.get("descriptionPlain") or strip_html(item.get("descriptionHtml") or ""),
            posted_at=published,
            date_basis="posted" if published else "none",
        ))
    return BoardResult(jobs)


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
