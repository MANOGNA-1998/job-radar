"""Read LinkedIn job alert emails from a dedicated inbox. No LinkedIn login or scraping is involved."""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from ..model import RawJob

JOB_LINK = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"']*?-)?(\d{6,})")
SKIP_TEXT = re.compile(r"^(view job|apply|apply now|easy apply|see all jobs?|view all|save|promoted|actively recruiting|be an early applicant|\d+ (school|connection|alum).*|.*connections?.*|.*alum.*|.*applicants?.*|.*ago)$", re.I)


class _Chunks(HTMLParser):
    """Flatten an email into text pieces, remembering which job link each piece sits inside."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[tuple[str, str | None]] = []
        self.current: str | None = None
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        if tag == "a":
            match = JOB_LINK.search(dict(attrs).get("href") or "")
            self.current = match.group(1) if match else None

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head") and self.skip:
            self.skip -= 1
        if tag == "a":
            self.current = None

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data).strip()
        if text and not self.skip:
            self.chunks.append((text, self.current))


def _split_company_location(parts: list[str]) -> tuple[str, str]:
    for part in parts:
        if "·" in part or " • " in part:
            pieces = [p.strip() for p in re.split(r"[·•]", part) if p.strip()]
            if len(pieces) >= 2:
                return pieces[0], pieces[1]
    useful = [p for p in parts if not SKIP_TEXT.match(p)]
    return (useful[0] if useful else ""), (useful[1] if len(useful) > 1 else "")


def parse_html(body: str) -> list[dict]:
    parser = _Chunks()
    parser.feed(body)
    chunks = parser.chunks
    starts: list[tuple[int, str]] = []
    seen = set()
    for index, (text, job_id) in enumerate(chunks):
        if job_id and job_id not in seen and not SKIP_TEXT.match(text):
            seen.add(job_id)
            starts.append((index, job_id))
    jobs = []
    for position, (index, job_id) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else min(index + 8, len(chunks))
        following = [text for text, _ in chunks[index + 1:end]][:6]
        company, location = _split_company_location(following)
        jobs.append({"id": job_id, "title": chunks[index][0], "company": company, "location": location})
    return jobs


def parse_text(body: str) -> list[dict]:
    jobs, seen = [], set()
    lines = [line.strip() for line in body.splitlines()]
    for index, line in enumerate(lines):
        match = JOB_LINK.search(line)
        if not match or match.group(1) in seen:
            continue
        before = []
        for candidate in reversed(lines[max(0, index - 6):index]):
            if not candidate or set(candidate) <= set("-_=*"):
                if before:
                    break
                continue
            if JOB_LINK.search(candidate):
                break
            if not SKIP_TEXT.match(candidate):
                before.insert(0, candidate)
        if len(before) >= 2:
            seen.add(match.group(1))
            location = before[2] if len(before) > 2 else ""
            jobs.append({"id": match.group(1), "title": before[0], "company": before[1], "location": location})
    return jobs


def parse_message(raw: bytes) -> list[RawJob]:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    sender = str(message.get("From", "")).lower()
    original = str(message.get("X-Forwarded-For", "")) + str(message.get("Reply-To", ""))
    if "linkedin.com" not in sender and "linkedin.com" not in original.lower():
        return []
    try:
        received = parsedate_to_datetime(message["Date"]).astimezone(timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    parsed: list[dict] = []
    html_part = message.get_body(preferencelist=("html",))
    if html_part:
        parsed = parse_html(html_part.get_content())
    if not parsed:
        text_part = message.get_body(preferencelist=("plain",))
        if text_part:
            parsed = parse_text(text_part.get_content())
    return [
        RawJob(
            source="linkedin",
            ext_id=item["id"],
            company=item["company"] or "Unknown company",
            title=item["title"],
            location=item["location"],
            url=f"https://www.linkedin.com/jobs/view/{item['id']}",
            posted_at=received,
            date_basis="alert",
        )
        for item in parsed
    ]


def fetch(host: str, user: str, password: str, days: int = 2, port: int = 993) -> list[RawJob]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    jobs: dict[str, RawJob] = {}
    with imaplib.IMAP4_SSL(host, port) as client:
        client.login(user, password)
        client.select("INBOX", readonly=True)
        status, data = client.search(None, "SINCE", since)
        if status != "OK":
            return []
        for number in data[0].split():
            status, parts = client.fetch(number, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            for job in parse_message(parts[0][1]):
                jobs.setdefault(job.ext_id, job)
    return list(jobs.values())
