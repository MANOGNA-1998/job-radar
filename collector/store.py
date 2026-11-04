"""All database access for the collector."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .model import RawJob, dedup_key
from .scoring import Score

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
ATS_SOURCES = ("greenhouse", "lever", "ashby", "workday")
AGGREGATORS = ["linkedin", "indeed", "jsearch"]


def connect() -> psycopg.Connection:
    # prepare_threshold=None keeps this compatible with Supabase's connection pooler.
    try:
        return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, prepare_threshold=None, autocommit=True)
    except psycopg.Error as exc:
        # The driver's message can quote part of the connection string, and Actions logs on a public repo are public.
        raise SystemExit(f"Could not connect to the database ({type(exc).__name__}). Check the DATABASE_URL secret.") from None


def init(conn) -> None:
    conn.execute(SCHEMA.read_text())


def seed_boards(conn, config: dict) -> None:
    names = config.get("display_names") or {}
    for ats in ("greenhouse", "lever", "ashby"):
        for slug in config.get(ats) or []:
            conn.execute(
                """INSERT INTO boards (ats, slug, company) SELECT %s, %s, %s
                   WHERE NOT EXISTS (SELECT 1 FROM boards WHERE slug = %s AND ats IN ('greenhouse','lever','ashby'))""",
                (ats, slug, names.get(slug), slug),
            )
    for entry in config.get("workday") or []:
        conn.execute(
            """INSERT INTO boards (ats, slug, company, config) VALUES ('workday', %s, %s, %s)
               ON CONFLICT (ats, slug) DO UPDATE SET company = EXCLUDED.company, config = EXCLUDED.config""",
            (entry["tenant"], entry["company"], json.dumps(entry)),
        )


def active_boards(conn) -> list[dict]:
    return conn.execute("SELECT * FROM boards WHERE active ORDER BY id").fetchall()


def board_ok(conn, board_id: int, count: int) -> None:
    conn.execute(
        """UPDATE boards SET last_ok_at = now(), last_error = NULL, last_error_at = NULL,
           job_count = %s, first_crawl_done = true WHERE id = %s""",
        (count, board_id),
    )


def board_failed(conn, board_id: int, error: str) -> None:
    conn.execute("UPDATE boards SET last_error = %s, last_error_at = now() WHERE id = %s", (error[:300], board_id))


def move_board(conn, board: dict, new_ats: str) -> bool:
    exists = conn.execute("SELECT 1 FROM boards WHERE ats = %s AND slug = %s", (new_ats, board["slug"])).fetchone()
    if exists:
        conn.execute("UPDATE boards SET active = false WHERE id = %s", (board["id"],))
        return False
    conn.execute("UPDATE boards SET ats = %s, last_error = NULL, last_error_at = NULL WHERE id = %s", (new_ats, board["id"]))
    return True


def learned_titles(conn) -> tuple[list[str], list[str]]:
    rows = conn.execute("SELECT title, status FROM jobs WHERE status IN ('not_fit', 'applied', 'interviewing')").fetchall()
    return ([r["title"] for r in rows if r["status"] == "not_fit"], [r["title"] for r in rows if r["status"] != "not_fit"])


def known_ids(conn, source: str, ext_ids: list[str]) -> set[str]:
    if not ext_ids:
        return set()
    rows = conn.execute("SELECT ext_id FROM jobs WHERE source = %s AND ext_id = ANY(%s)", (source, ext_ids)).fetchall()
    return {r["ext_id"] for r in rows}


def compute_fresh_at(job: RawJob, backfill: bool, now: datetime) -> datetime | None:
    """When this job should count as new. Returns None if it has no usable date on a board's first crawl."""
    if job.date_basis in ("posted", "alert") and job.posted_at:
        return min(job.posted_at, now)
    if backfill:
        # On a board's first crawl, "first seen" only means we just added the board.
        return min(job.posted_at, now) if job.posted_at else None
    return now


def upsert(conn, job: RawJob, result: Score, board_id: int | None, backfill: bool, max_age_days: int, now: datetime) -> str:
    """Returns inserted, updated, merged, or skipped."""
    key = dedup_key(job.company, job.title)
    existing = conn.execute("SELECT id, description, location, score, tier, reason FROM jobs WHERE source = %s AND ext_id = %s", (job.source, job.ext_id)).fetchone()
    if existing:
        if not job.description and existing["description"]:
            # This pass has no description (Workday only loads it once), so keep the fuller score.
            result = Score(existing["score"], existing["tier"], existing["reason"])
            job.location = existing["location"]
        conn.execute(
            """UPDATE jobs SET last_seen_at = %s, closed_at = NULL, title = %s, location = %s, url = %s,
               description = COALESCE(NULLIF(%s, ''), description), score = %s, tier = %s, reason = %s WHERE id = %s""",
            (now, job.title, job.location, job.url, job.description, result.score, result.tier, result.reason, existing["id"]),
        )
        return "updated"

    is_ats = job.source in ATS_SOURCES
    # The same role on another source is one job. Two postings on one employer's board stay separate.
    other = conn.execute(
        """SELECT id, url, alt_urls, source, description FROM jobs WHERE dedup_key = %s AND closed_at IS NULL AND fresh_at > %s
           AND source <> %s AND (%s OR source = ANY(%s)) ORDER BY fresh_at DESC LIMIT 1""",
        (key, now - timedelta(days=21), job.source, not is_ats, AGGREGATORS),
    ).fetchone()
    if other:
        return _merge(conn, job, result, other, board_id, is_ats, now)

    fresh = compute_fresh_at(job, backfill, now)
    if fresh is None or fresh < now - timedelta(days=max_age_days):
        return "skipped"
    repost = conn.execute(
        "SELECT 1 FROM jobs WHERE dedup_key = %s AND source = %s AND closed_at IS NOT NULL LIMIT 1", (key, job.source)
    ).fetchone()
    conn.execute(
        """INSERT INTO jobs (source, ext_id, board_id, dedup_key, company, title, location, url, description, posted_at,
           date_basis, first_seen_at, last_seen_at, fresh_at, score, tier, reason, possible_repost)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (job.source, job.ext_id, board_id, key, job.company, job.title, job.location, job.url, job.description or None,
         job.posted_at, job.date_basis, now, now, fresh, result.score, result.tier, result.reason, bool(repost)),
    )
    return "inserted"


def _merge(conn, job, result, other, board_id, is_ats, now) -> str:
    urls = list(other["alt_urls"] or [])
    if is_ats:
        # The employer's own posting wins. The row, and your tracker status on it, stays the same.
        if other["url"] not in urls:
            urls.append(other["url"])
        conn.execute(
            """UPDATE jobs SET source = %s, ext_id = %s, board_id = %s, company = %s, url = %s, alt_urls = %s, location = %s,
               description = COALESCE(NULLIF(%s, ''), description), posted_at = COALESCE(%s, posted_at),
               score = %s, tier = %s, reason = %s, last_seen_at = %s WHERE id = %s""",
            (job.source, job.ext_id, board_id, job.company, job.url, json.dumps(urls), job.location, job.description,
             job.posted_at if job.date_basis == "posted" else None, result.score, result.tier, result.reason, now, other["id"]),
        )
    else:
        if job.url not in urls and job.url != other["url"]:
            urls.append(job.url)
        conn.execute("UPDATE jobs SET alt_urls = %s WHERE id = %s", (json.dumps(urls), other["id"]))
        if job.description and not other["description"]:
            conn.execute(
                "UPDATE jobs SET description = %s, score = %s, tier = %s, reason = %s WHERE id = %s",
                (job.description, result.score, result.tier, result.reason, other["id"]),
            )
    return "merged"


def mark_closed(conn, board_id: int, source: str, open_ext_ids: list[str], now: datetime) -> int:
    cursor = conn.execute(
        """UPDATE jobs SET closed_at = %s WHERE board_id = %s AND source = %s AND closed_at IS NULL
           AND NOT (ext_id = ANY(%s))""",
        (now, board_id, source, open_ext_ids),
    )
    return cursor.rowcount


def prune(conn, now: datetime) -> None:
    """Keep the free database small. Anything you marked stays forever."""
    conn.execute(
        """DELETE FROM jobs WHERE status = 'new' AND (fresh_at < %s OR (closed_at IS NOT NULL AND closed_at < %s))""",
        (now - timedelta(days=45), now - timedelta(days=7)),
    )
    conn.execute("DELETE FROM runs WHERE started_at < %s", (now - timedelta(days=14),))


def start_run(conn, trigger: str) -> int:
    return conn.execute("INSERT INTO runs (trigger) VALUES (%s) RETURNING id", (trigger,)).fetchone()["id"]


def finish_run(conn, run_id: int, stats: dict) -> None:
    conn.execute("UPDATE runs SET finished_at = now(), stats = %s WHERE id = %s", (json.dumps(stats), run_id))


def companies_to_probe(conn, limit: int) -> list[dict]:
    return conn.execute(
        """SELECT DISTINCT ON (j.company) j.company, j.title FROM jobs j
           WHERE j.source = ANY(%s) AND j.company <> 'Unknown company'
           AND NOT EXISTS (SELECT 1 FROM company_probes p WHERE p.company_key = lower(j.company))
           ORDER BY j.company LIMIT %s""",
        (AGGREGATORS, limit),
    ).fetchall()


def record_probe(conn, company: str, found: str | None) -> None:
    conn.execute(
        """INSERT INTO company_probes (company_key, found) VALUES (lower(%s), %s)
           ON CONFLICT (company_key) DO UPDATE SET probed_at = now(), found = EXCLUDED.found""",
        (company, found),
    )


def add_discovered_board(conn, ats: str, slug: str, company: str) -> None:
    conn.execute(
        "INSERT INTO boards (ats, slug, company, origin) VALUES (%s, %s, %s, 'discovered') ON CONFLICT DO NOTHING",
        (ats, slug, company),
    )


def apify_used_today(conn, day, source: str) -> int:
    row = conn.execute("SELECT results FROM apify_usage WHERE day = %s AND source = %s", (day, source)).fetchone()
    return row["results"] if row else 0


def apify_record(conn, count: int, day, source: str, now: datetime) -> None:
    conn.execute(
        """INSERT INTO apify_usage (day, source, results, last_run_at) VALUES (%s, %s, %s, %s)
           ON CONFLICT (day, source) DO UPDATE SET results = apify_usage.results + EXCLUDED.results, last_run_at = EXCLUDED.last_run_at""",
        (day, source, count, now),
    )


def jsearch_used(conn, month) -> int:
    row = conn.execute("SELECT requests FROM jsearch_usage WHERE month = %s", (month,)).fetchone()
    return row["requests"] if row else 0


def jsearch_record(conn, month) -> None:
    conn.execute(
        """INSERT INTO jsearch_usage (month, requests) VALUES (%s, 1)
           ON CONFLICT (month) DO UPDATE SET requests = jsearch_usage.requests + 1""",
        (month,),
    )


def search_state(conn, key: str) -> dict:
    row = conn.execute("SELECT last_attempt_at, last_success_at FROM search_state WHERE key = %s", (key,)).fetchone()
    return row or {"last_attempt_at": None, "last_success_at": None}


def mark_search(conn, key: str, now: datetime, success: bool) -> None:
    conn.execute(
        """INSERT INTO search_state (key, last_attempt_at, last_success_at) VALUES (%s, %s, %s)
           ON CONFLICT (key) DO UPDATE SET last_attempt_at = EXCLUDED.last_attempt_at,
           last_success_at = COALESCE(EXCLUDED.last_success_at, search_state.last_success_at)""",
        (key, now, now if success else None),
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
