"""When job-site searches are due, and how far back each one should look."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/New_York")
RETRY_AFTER = timedelta(hours=1)
MIN_WINDOW = 3600
MAX_WINDOW = 7 * 86400
FIRST_WINDOW = 86400


def local_day(now: datetime) -> date:
    return now.astimezone(ZONE).date()


def local_month(now: datetime) -> date:
    return local_day(now).replace(day=1)


def latest_slot(now: datetime, every_hours: int) -> datetime:
    """The most recent scheduled time: with 6 hours, the last of midnight, 6am, noon, or 6pm in New York."""
    local = now.astimezone(ZONE)
    return local.replace(hour=local.hour - local.hour % every_hours, minute=0, second=0, microsecond=0)


def search_decision(state: dict, schedule: dict, now: datetime, manual: bool) -> str:
    """Returns due, not_due, or cooldown."""
    success, attempt = state["last_success_at"], state["last_attempt_at"]
    if manual:
        if success and now - success < timedelta(minutes=schedule["manual_cooldown_minutes"]):
            return "cooldown"
        return "due"
    if success and success >= latest_slot(now, schedule["every_hours"]):
        return "not_due"
    if attempt and now - attempt < RETRY_AFTER:
        return "not_due"
    return "due"


def window_seconds(state: dict, schedule: dict, now: datetime) -> int:
    """Look back to the last successful search, so a failed or skipped run leaves no gap."""
    success = state["last_success_at"]
    if not success:
        return FIRST_WINDOW
    seconds = int((now - success).total_seconds()) + schedule["overlap_minutes"] * 60
    return max(MIN_WINDOW, min(MAX_WINDOW, seconds))


def scheduled_runs_left(now: datetime, schedule: dict) -> int:
    local = now.astimezone(ZONE)
    days_left = calendar.monthrange(local.year, local.month)[1] - local.day + 1
    return days_left * (24 // schedule["every_hours"])


def summarize(outcomes: list[str]) -> str:
    for preferred in ("searched", "failed", "daily_limit", "cooldown"):
        if preferred in outcomes:
            return preferred
    return "not_due"
