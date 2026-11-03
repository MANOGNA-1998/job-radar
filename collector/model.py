"""Shared job record and text helpers."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass
class RawJob:
    source: str  # greenhouse, lever, ashby, workday, linkedin
    ext_id: str
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    posted_at: Optional[datetime] = None
    # posted: the employer's own publish date. updated: only a last-edited date, not trusted as a post date.
    # alert: the time an alert email or search reported it. none: no date at all.
    date_basis: str = "none"
    # Other links to this job, used to find the employer's own job board.
    apply_links: list[str] = field(default_factory=list)
    load_description: Optional[Callable[[], tuple[str, Optional[datetime], str]]] = field(default=None, repr=False)


@dataclass
class BoardResult:
    jobs: list[RawJob]
    # True when the response lists every open job, so a missing job can be treated as closed.
    complete: bool = True


class BoardNotFound(Exception):
    """The board does not exist on this system (a wrong slug, or the company moved)."""


def parse_time(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def strip_html(text: str) -> str:
    text = html.unescape(html.unescape(text or ""))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<(br|/p|/li|/div|/h\d)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


COMPANY_SUFFIX = re.compile(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|technologies|technology|labs)\b\.?", re.I)


def norm_company(name: str) -> str:
    cleaned = COMPANY_SUFFIX.sub(" ", name or "").lower()
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def norm_title(title: str) -> str:
    cleaned = re.sub(r"\(.*?\)", " ", (title or "").lower())
    cleaned = re.sub(r"\b(remote|hybrid|onsite|on-site|nyc|new york|usa|us)\b", " ", cleaned)
    return " ".join(re.findall(r"[a-z0-9+#]+", cleaned))


def dedup_key(company: str, title: str) -> str:
    return hashlib.sha1(f"{norm_company(company)}|{norm_title(title)}".encode()).hexdigest()
