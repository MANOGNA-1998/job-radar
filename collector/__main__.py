"""Run one collection pass. `python -m collector --dry-run` needs no database or accounts."""
from __future__ import annotations

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import yaml

from . import scoring, store
from .model import BoardNotFound, BoardResult, RawJob, norm_company, norm_title
from .sources import apify_linkedin, ats, indeed, jsearch, linkedin_email, workday
from . import timing

CONFIG = Path(__file__).resolve().parent.parent / "config"
BOARD_WORKERS = 8
DESCRIPTIONS_PER_WORKDAY_BOARD = 40
PROBES_PER_RUN = 5


def fetch_board(board: dict, config: dict) -> BoardResult:
    if board["ats"] == "workday":
        entry = board["config"]
        return workday.fetch(entry["company"], entry["tenant"], entry["dc"], entry["site"], config["workday_searches"])
    return ats.FETCHERS[board["ats"]](board["slug"], board.get("company"))


def fetch_with_move(board: dict, config: dict) -> tuple[BoardResult | None, str | None, str | None]:
    """Returns (result, new system if the company moved, error)."""
    try:
        return fetch_board(board, config), None, None
    except BoardNotFound:
        if board["ats"] == "workday":
            return None, None, "not found"
        for other in ats.FETCHERS:
            if other == board["ats"]:
                continue
            try:
                return ats.FETCHERS[other](board["slug"], board.get("company")), other, None
            except BoardNotFound:
                continue
            except Exception as exc:  # noqa: BLE001
                return None, None, f"{other}: {type(exc).__name__}"
        return None, None, "not found on Greenhouse, Lever, or Ashby"
    except Exception as exc:  # noqa: BLE001 - one broken board must never stop the others
        return None, None, type(exc).__name__ + ": " + str(exc)[:200]


def load_descriptions(jobs: list[RawJob]) -> None:
    for job in jobs[:DESCRIPTIONS_PER_WORKDAY_BOARD]:
        if job.load_description is None:
            continue
        try:
            text, published, place = job.load_description()
        except Exception:  # noqa: BLE001 - keep the job, scored from its title
            continue
        job.description = text
        if published:
            job.posted_at, job.date_basis = published, "posted"
        if place:
            job.location = place


def discover(conn, stats: dict) -> None:
    """If a LinkedIn alert names a company with its own board, watch that board directly from then on."""
    for row in store.companies_to_probe(conn, PROBES_PER_RUN):
        slug = norm_company(row["company"])
        found = None
        if slug:
            for system, fetcher in ats.FETCHERS.items():
                try:
                    result = fetcher(slug, row["company"])
                except Exception:  # noqa: BLE001
                    continue
                # Only trust the match if that board really lists this exact role.
                if any(norm_title(j.title) == norm_title(row["title"]) for j in result.jobs):
                    store.add_discovered_board(conn, system, slug, row["company"])
                    found = system
                    stats["boards_discovered"] += 1
                    break
        store.record_probe(conn, row["company"], found)


BOARD_LINK = re.compile(r"https://(?:(?:job-)?boards\.greenhouse\.io/(?P<gh>[\w-]+)/jobs|jobs\.lever\.co/(?P<lever>[\w-]+)/|jobs\.ashbyhq\.com/(?P<ashby>[\w-]+)/)")
BOARD_GROUPS = {"gh": "greenhouse", "lever": "lever", "ashby": "ashby"}


def board_from_link(url: str) -> tuple[str, str] | None:
    match = BOARD_LINK.match(url or "")
    if not match:
        return None
    for group, system in BOARD_GROUPS.items():
        if match.group(group):
            return system, match.group(group).lower()
    return None


def ingest(conn, jobs, board_id, backfill, profile, penalties, now, stats) -> None:
    for job in jobs:
        result = scoring.score_job(job.title, job.location, job.description, profile, penalties)
        if result is None or result.score < profile["min_score"] or not job.url.startswith("https://"):
            continue
        stats[store.upsert(conn, job, result, board_id, backfill, profile["max_age_days"], now)] += 1
        for link in job.apply_links:
            found = board_from_link(link)
            if found:
                store.add_discovered_board(conn, found[0], found[1], job.company)


PAID_SOURCES = {
    # name: (config section, label shown to you, search function)
    "linkedin": ("apify_linkedin", "LinkedIn", apify_linkedin.search),
    "indeed": ("apify_indeed", "Indeed", indeed.search),
}


def collect_apify(conn, config, profile, penalties, now, manual, stats) -> None:
    token = os.environ.get("APIFY_TOKEN")
    schedule = config.get("search_schedule") or {}
    day = timing.local_day(now)
    for name, (section, label, searcher) in PAID_SOURCES.items():
        settings = config.get(section) or {}
        if not token or not settings:
            stats["paid"][name] = "off"
            continue
        outcomes = []
        for phrase in settings["searches"]:
            key = f"{name}:{phrase}"
            state = store.search_state(conn, key)
            decision = timing.search_decision(state, schedule, now, manual)
            if decision != "due":
                outcomes.append(decision)
                continue
            remaining = settings["daily_result_budget"] - store.apify_used_today(conn, day, name)
            if remaining < 1:
                outcomes.append("daily_limit")
                continue
            limit = min(settings["per_search_limit"], remaining)
            seconds = timing.window_seconds(state, schedule, now)
            store.mark_search(conn, key, now, False)
            try:
                jobs = searcher(token, settings["actor"], phrase, settings["location"], seconds, limit, now)
            except Exception as exc:  # noqa: BLE001 - the window stays put, so the next try covers this gap
                stats["errors"].append(f"{label} search ({phrase}): {type(exc).__name__}")
                # Only the first failure in a streak raises an alert, so a spend cap does not send an email every hour.
                last_try, last_ok = state.get("last_attempt_at"), state.get("last_success_at")
                if last_try is None or (last_ok is not None and last_try <= last_ok):
                    stats["alerts"].append(f"{label} search ({phrase}) failed")
                outcomes.append("failed")
                continue
            store.apify_record(conn, len(jobs), day, name, now)
            store.mark_search(conn, key, now, True)
            stats[f"{name}_apify"] += len(jobs)
            outcomes.append("searched")
            # The very first search reaches back a full day, so hitting the cap then is expected and not worth a warning.
            if len(jobs) >= limit and state.get("last_success_at") is not None:
                stats["warnings"].append(f'{label} search "{phrase}" hit its limit of {limit} results, so some roles may be missing.')
            ingest(conn, jobs, None, False, profile, penalties, now, stats)
        stats["paid"][name] = timing.summarize(outcomes)


def collect_jsearch(conn, config, profile, penalties, now, manual, stats) -> None:
    key_value = os.environ.get("RAPIDAPI_KEY")
    settings = config.get("jsearch") or {}
    schedule = config.get("search_schedule") or {}
    if not key_value or not settings:
        stats["paid"]["jsearch"] = "off"
        return
    state = store.search_state(conn, "jsearch")
    decision = timing.search_decision(state, schedule, now, manual)
    if decision != "due":
        stats["paid"]["jsearch"] = decision
        return
    month = timing.local_month(now)
    used = store.jsearch_used(conn, month)
    # Check now can't use requests that the remaining scheduled runs this month will need.
    reserve = timing.scheduled_runs_left(now, schedule) if manual else 0
    if used + reserve >= settings["monthly_request_cap"]:
        stats["paid"]["jsearch"] = "monthly_limit"
        return
    phrase = settings["searches"][used % len(settings["searches"])]
    store.mark_search(conn, "jsearch", now, False)
    store.jsearch_record(conn, month)
    try:
        jobs = jsearch.search(key_value, phrase, timing.window_seconds(state, schedule, now), now)
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"Google for Jobs search: {type(exc).__name__}")
        stats["paid"]["jsearch"] = "failed"
        return
    store.mark_search(conn, "jsearch", now, True)
    stats["jsearch"] += len(jobs)
    stats["paid"]["jsearch"] = "searched"
    ingest(conn, jobs, None, False, profile, penalties, now, stats)


def collect_email(conn, profile, penalties, now, stats) -> None:
    user, password = os.environ.get("ALERT_EMAIL_USER"), os.environ.get("ALERT_EMAIL_APP_PASSWORD")
    if not user or not password:
        return
    host = os.environ.get("ALERT_EMAIL_HOST", "imap.gmail.com")
    try:
        jobs = linkedin_email.fetch(host, user, password)
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"LinkedIn email: {type(exc).__name__}")
        return
    stats["linkedin_email"] += len(jobs)
    ingest(conn, jobs, None, False, profile, penalties, now, stats)


def run(config: dict, profile: dict) -> dict:
    now = store.now_utc()
    manual = os.environ.get("JR_MANUAL") == "true"
    stats = {"inserted": 0, "updated": 0, "merged": 0, "skipped": 0, "closed": 0, "boards_ok": 0, "boards_failed": 0,
             "boards_discovered": 0, "linkedin_email": 0, "linkedin_apify": 0, "indeed_apify": 0, "jsearch": 0, "errors": [], "warnings": [], "alerts": [], "paid": {}}
    with store.connect() as conn:
        store.init(conn)
        store.seed_boards(conn, config)
        run_id = store.start_run(conn, "manual" if manual else "schedule")
        rejected, applied = store.learned_titles(conn)
        penalties = scoring.title_penalties(rejected, applied)
        boards = store.active_boards(conn)
        with ThreadPoolExecutor(BOARD_WORKERS) as pool:
            outcomes = list(pool.map(lambda b: fetch_with_move(b, config), boards))
        for board, (result, moved_to, error) in zip(boards, outcomes):
            if result is None:
                store.board_failed(conn, board["id"], error or "unknown error")
                stats["boards_failed"] += 1
                continue
            if moved_to and not store.move_board(conn, board, moved_to):
                continue
            wanted = [j for j in result.jobs if scoring.passes_prefilter(j.title, j.location, profile)]
            source = moved_to or board["ats"]
            if any(j.load_description for j in wanted):
                known = store.known_ids(conn, source, [j.ext_id for j in wanted])
                load_descriptions([j for j in wanted if j.ext_id not in known])
            ingest(conn, wanted, board["id"], not board["first_crawl_done"], profile, penalties, now, stats)
            if result.complete:
                stats["closed"] += store.mark_closed(conn, board["id"], source, [j.ext_id for j in result.jobs], now)
            store.board_ok(conn, board["id"], len(result.jobs))
            stats["boards_ok"] += 1
        collect_email(conn, profile, penalties, now, stats)
        collect_apify(conn, config, profile, penalties, now, manual, stats)
        collect_jsearch(conn, config, profile, penalties, now, manual, stats)
        discover(conn, stats)
        store.prune(conn, now)
        store.finish_run(conn, run_id, stats)
    return stats


def dry_run(config: dict, profile: dict) -> int:
    """Check every board live and print the matches. Touches nothing."""
    names = config.get("display_names") or {}
    boards = [{"ats": a, "slug": s, "company": names.get(s), "config": {}} for a in ("greenhouse", "lever", "ashby") for s in config.get(a) or []]
    boards += [{"ats": "workday", "slug": e["tenant"], "company": e["company"], "config": e} for e in config.get("workday") or []]
    now = store.now_utc()
    rows, failed, working = [], [], 0
    dates: dict[str, int] = {}
    with ThreadPoolExecutor(BOARD_WORKERS) as pool:
        outcomes = list(pool.map(lambda b: fetch_with_move(b, config), boards))
    for board, (result, moved_to, error) in zip(boards, outcomes):
        name = f"{board['ats']}/{board['slug']}"
        if result is None:
            failed.append(f"{name}: {error}")
            continue
        working += 1
        note = f" (moved to {moved_to})" if moved_to else ""
        print(f"ok   {name}{note}: {len(result.jobs)} open jobs", file=sys.stderr)
        wanted = [j for j in result.jobs if scoring.passes_prefilter(j.title, j.location, profile)]
        load_descriptions([j for j in wanted if j.load_description])
        for job in wanted:
            scored = scoring.score_job(job.title, job.location, job.description, profile)
            fresh = store.compute_fresh_at(job, True, now)
            if scored and scored.score >= profile["min_score"] and fresh and fresh >= now - timedelta(days=profile["max_age_days"]):
                rows.append((fresh, scored.score, job, scored))
                label = f"{job.source}/{job.date_basis}"
                dates[label] = dates.get(label, 0) + 1

    def show(chosen):
        for fresh, score, job, scored in chosen:
            age = re.sub(r"\.\d+", "", str(now - fresh))
            print(f"{age:>18} ago  {score:>3}  {job.title} at {job.company} ({job.location or 'no location'})\n{'':>28}{scored.reason}\n{'':>28}{job.url}")

    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"\nBoards working: {working} of {len(boards)}")
    for line in failed:
        print(f"  not responding: {line}")
    print(f"Matching jobs from the last {profile['max_age_days']} days: {len(rows)}")
    print(f"Where their dates came from: {dict(sorted(dates.items()))}")
    print("\nNewest 40:\n")
    show(rows[:40])
    print("\nBest 25 matches from the last 7 days:\n")
    show(sorted([r for r in rows if now - r[0] <= timedelta(days=7)], key=lambda r: r[1], reverse=True)[:25])
    return 0 if working else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="check boards live and print matches, no database needed")
    args = parser.parse_args()
    config = yaml.safe_load((CONFIG / "boards.yaml").read_text())
    profile = scoring.load_profile()
    if args.dry_run:
        return dry_run(config, profile)
    stats = run(config, profile)
    # Actions logs on a public repo are public, so this prints counts only.
    print({k: (len(v) if isinstance(v, list) else v) for k, v in stats.items()})
    # A failed run makes GitHub email the repository owner, which is how a stopped paid search reaches you.
    return 0 if stats["boards_ok"] and not stats["alerts"] else 1


if __name__ == "__main__":
    sys.exit(main())
