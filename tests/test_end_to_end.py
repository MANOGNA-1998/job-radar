"""Full collector passes against a fake job board server and a real Postgres. Needs TEST_DATABASE_URL."""
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


JD = "Build agents with LangGraph and RAG in Python. 3+ years of experience."


class World:
    routes: dict = {}
    calls: list = []
    bodies: list = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self):
        path = self.path.split("?")[0]
        World.calls.append(path)
        if path in World.routes:
            body = json.dumps(World.routes[path]).encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        World.bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))
        self._reply()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def gh_job(job_id, title, location="New York, NY", published=1, content=JD):
    return {"id": job_id, "title": title, "location": {"name": location}, "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
            "first_published": iso(published), "updated_at": iso(0.1), "content": content, "company_name": "Acme"}


def base_world():
    return {
        "/v1/boards/acme/jobs": {"jobs": [
            gh_job(1, "Applied AI Engineer"),
            gh_job(2, "Software Engineer", "Austin, TX", published=30),
            gh_job(3, "Machine Learning Engineer", "London, UK"),
            gh_job(4, "Staff Software Engineer"),
            gh_job(5, "Account Executive"),
            gh_job(6, "AI Engineer", published=100 * 24),
            {"id": 7, "title": "Backend Engineer", "location": {"name": "Remote"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/7",
             "updated_at": iso(40), "content": JD, "company_name": "Acme"},
        ]},
        "/v0/postings/leverco": [{
            "id": "abc", "text": "AI Engineer", "createdAt": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000),
            "hostedUrl": "https://jobs.lever.co/leverco/abc", "categories": {"location": "New York", "allLocations": ["New York"]},
            "descriptionPlain": JD, "lists": [{"text": "Needs", "content": "<li>MCP</li>"}], "workplaceType": "hybrid",
        }],
        "/posting-api/job-board/ashbyco": {"jobs": [
            {"id": "x1", "title": "Forward Deployed Engineer", "location": "San Francisco, CA", "secondaryLocations": [{"location": "New York, NY"}],
             "publishedAt": iso(3), "isListed": True, "isRemote": False, "jobUrl": "https://jobs.ashbyhq.com/ashbyco/x1", "descriptionPlain": JD},
            {"id": "x2", "title": "AI Engineer Hidden", "location": "New York, NY", "publishedAt": iso(3), "isListed": False,
             "jobUrl": "https://jobs.ashbyhq.com/ashbyco/x2", "descriptionPlain": JD},
        ]},
        # "movedco" is configured as Greenhouse but now lives on Lever.
        "/v0/postings/movedco": [{
            "id": "m1", "text": "Applied ML Engineer", "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000) - 3600000,
            "hostedUrl": "https://jobs.lever.co/movedco/m1", "categories": {"location": "Remote"}, "descriptionPlain": JD,
        }],
        "/wday/cxs/wdco/External/jobs": {"total": 2, "jobPostings": [
            {"title": "Software Engineer, AI", "externalPath": "/job/New-York/Software-Engineer--AI_R100", "locationsText": "New York, NY", "postedOn": "Posted Today"},
            {"title": "Sales Director", "externalPath": "/job/New-York/Sales-Director_R101", "locationsText": "New York, NY", "postedOn": "Posted Today"},
        ]},
        "/wday/cxs/wdco/External/job/New-York/Software-Engineer--AI_R100": {"jobPostingInfo": {"jobDescription": f"<p>{JD}</p>", "startDate": iso(5)}},
    }


CONFIG = """
greenhouse: [acme, movedco, doesnotexist]
lever: [leverco]
ashby: [ashbyco]
workday_searches: ["AI engineer"]
workday:
  - {company: WD Co, tenant: wdco, dc: wd1, site: External}
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """A copy of the code with a small test board list, so the real config stays untouched."""
    target = tmp_path_factory.mktemp("proj")
    for name in ("collector", "db", "config"):
        subprocess.run(["cp", "-r", str(ROOT / name), str(target)], check=True)
    (target / "config" / "boards.yaml").write_text(CONFIG)
    return target


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as connection:
        connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        yield connection


def collect(project, server):
    env = {**os.environ, "DATABASE_URL": DSN, "JR_GREENHOUSE_HOST": server, "JR_LEVER_HOST": server, "JR_ASHBY_HOST": server,
           "JR_WORKDAY_HOST": server, "JR_APIFY_HOST": server}
    for name in ("APIFY_TOKEN", "ALERT_EMAIL_USER", "ALERT_EMAIL_APP_PASSWORD"):
        env.pop(name, None)
    done = subprocess.run([sys.executable, "-m", "collector"], cwd=project, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def job(conn, ext_id):
    return conn.execute("SELECT * FROM jobs WHERE ext_id = %s", (ext_id,)).fetchone()


def test_first_pass_keeps_only_fitting_recent_jobs(project, server, conn):
    World.routes = base_world()
    collect(project, server)
    ids = {r["ext_id"] for r in conn.execute("SELECT ext_id FROM jobs").fetchall()}
    assert ids == {"acme:1", "acme:2", "acme:7", "leverco:abc", "ashbyco:x1", "movedco:m1", "wdco:Software-Engineer--AI_R100"}
    assert job(conn, "ashbyco:x1")["location"] == "San Francisco, CA | New York, NY"
    assert "MCP" in job(conn, "leverco:abc")["description"]
    assert "LangGraph" in job(conn, "wdco:Software-Engineer--AI_R100")["reason"]


def test_employer_post_dates_are_kept_on_first_crawl(conn):
    row = job(conn, "acme:2")
    age = datetime.now(timezone.utc) - row["fresh_at"]
    assert timedelta(hours=29) < age < timedelta(hours=31), "must not look new just because the board was just added"
    updated_only = job(conn, "acme:7")
    assert updated_only["date_basis"] == "updated"
    assert datetime.now(timezone.utc) - updated_only["fresh_at"] > timedelta(hours=39)


def test_moved_company_and_bad_slug(conn):
    moved = conn.execute("SELECT ats, last_error FROM boards WHERE slug = 'movedco'").fetchone()
    assert moved == {"ats": "lever", "last_error": None}
    bad = conn.execute("SELECT last_error FROM boards WHERE slug = 'doesnotexist'").fetchone()
    assert "not found" in bad["last_error"]


def test_tables_are_closed_to_supabase_public_api(conn):
    rows = conn.execute("SELECT relname FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace AND NOT relrowsecurity").fetchall()
    assert rows == []


def test_new_job_without_date_counts_from_when_found(project, server, conn):
    World.routes["/v1/boards/acme/jobs"]["jobs"].append(
        {"id": 8, "title": "AI Engineer II", "location": {"name": "New York"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/8",
         "updated_at": iso(200), "content": JD, "company_name": "Acme"})
    collect(project, server)
    assert datetime.now(timezone.utc) - job(conn, "acme:8")["fresh_at"] < timedelta(minutes=2)


def test_tracker_status_survives_and_closed_jobs_are_flagged(project, server, conn):
    conn.execute("UPDATE jobs SET status = 'applied', applied_at = now() WHERE ext_id = 'acme:1'")
    before = job(conn, "acme:1")
    world = World.routes["/v1/boards/acme/jobs"]["jobs"]
    collect(project, server)
    after = job(conn, "acme:1")
    assert after["status"] == "applied" and after["first_seen_at"] == before["first_seen_at"] and after["fresh_at"] == before["fresh_at"]
    World.routes["/v1/boards/acme/jobs"]["jobs"] = [j for j in world if j["id"] != 1]
    collect(project, server)
    closed = job(conn, "acme:1")
    assert closed["closed_at"] is not None and closed["status"] == "applied"


def test_reposted_role_is_flagged(project, server, conn):
    World.routes["/v1/boards/acme/jobs"]["jobs"].append(gh_job(99, "Applied AI Engineer", published=0.2))
    collect(project, server)
    assert job(conn, "acme:99")["possible_repost"] is True


def test_broken_board_does_not_stop_others_or_close_its_jobs(project, server, conn):
    saved = World.routes.pop("/v0/postings/leverco")
    collect(project, server)
    assert job(conn, "leverco:abc")["closed_at"] is None, "a failed fetch must not mark jobs closed"
    World.routes["/v0/postings/leverco"] = saved
    collect(project, server)
    assert job(conn, "leverco:abc")["closed_at"] is None


def test_linkedin_and_employer_copy_merge_keeping_status(project, server, conn):
    from collector import scoring, store
    from collector.model import RawJob
    now = datetime.now(timezone.utc)
    os.environ["DATABASE_URL"] = DSN
    profile = scoring.load_profile()
    linkedin = RawJob("linkedin", "4000000001", "Newco, Inc.", "Applied AI Engineer", "New York, NY",
                      "https://www.linkedin.com/jobs/view/4000000001", posted_at=now - timedelta(hours=1), date_basis="alert")
    assert store.upsert(conn, linkedin, scoring.score_job(linkedin.title, linkedin.location, "", profile), None, False, 21, now) == "inserted"
    conn.execute("UPDATE jobs SET status = 'skipped' WHERE ext_id = '4000000001'")

    employer = RawJob("ashby", "newco:1", "Newco", "Applied AI Engineer", "New York City", "https://jobs.ashbyhq.com/newco/1",
                      description=JD, posted_at=now - timedelta(hours=2), date_basis="posted")
    assert store.upsert(conn, employer, scoring.score_job(employer.title, employer.location, JD, profile), None, False, 21, now) == "merged"
    row = job(conn, "newco:1")
    assert row["url"] == employer.url and row["alt_urls"] == [linkedin.url] and row["status"] == "skipped" and "LangGraph" in row["reason"]
    assert job(conn, "4000000001") is None

    # The same LinkedIn alert arriving again must not create a second row.
    assert store.upsert(conn, linkedin, scoring.score_job(linkedin.title, linkedin.location, "", profile), None, False, 21, now) == "merged"
    assert conn.execute("SELECT count(*) AS c FROM jobs WHERE dedup_key = %s", (row["dedup_key"],)).fetchone()["c"] == 1


def test_two_separate_employer_jobs_with_one_title_both_survive(project, server, conn):
    World.routes["/v1/boards/acme/jobs"]["jobs"].extend([gh_job(201, "Software Engineer II"), gh_job(202, "Software Engineer II", "Remote")])
    collect(project, server)
    assert job(conn, "acme:201") and job(conn, "acme:202")


def test_workday_rerun_keeps_full_score(project, server, conn):
    row = job(conn, "wdco:Software-Engineer--AI_R100")
    assert "scored from title only" not in row["reason"] and row["description"]


PAID_ROUTE = "/v2/acts/curious_coder~linkedin-jobs-scraper/run-sync-get-dataset-items"
INDEED_ROUTE = "/v2/acts/curious_coder~indeed-scraper/run-sync-get-dataset-items"
PAID_CONFIG = CONFIG + """
search_schedule: {every_hours: 6, manual_cooldown_minutes: 30, overlap_minutes: 30}
apify_linkedin: {actor: curious_coder~linkedin-jobs-scraper, location: United States, per_search_limit: 18, daily_result_budget: 288, searches: [a, b]}
apify_indeed: {actor: curious_coder~indeed-scraper, location: United States, per_search_limit: 100, daily_result_budget: 1600, searches: [a, b]}
jsearch: {monthly_request_cap: 200, searches: [one, two]}
"""


def paid_pass(project, server, manual=False, apify=True, expect=0):
    env = {**os.environ, "DATABASE_URL": DSN, "JR_GREENHOUSE_HOST": server, "JR_LEVER_HOST": server, "JR_ASHBY_HOST": server,
           "JR_WORKDAY_HOST": server, "JR_APIFY_HOST": server, "JR_JSEARCH_HOST": server, "RAPIDAPI_KEY": "test",
           "JR_MANUAL": "true" if manual else "false"}
    if apify:
        env["APIFY_TOKEN"] = "test"
    done = subprocess.run([sys.executable, "-m", "collector"], cwd=project, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == expect, done.stderr


def last_stats(conn):
    return conn.execute("SELECT stats, trigger FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def paid_calls():
    return [c for c in World.calls if c.startswith("/v2/acts")]


def test_paid_searches_follow_schedule_window_cooldown_and_cap(project, server, conn):
    World.routes[PAID_ROUTE] = [
        {"id": str(4100000000 + n), "title": "AI Engineer", "companyName": f"Co{n}", "location": "New York, NY",
         "link": f"https://www.linkedin.com/jobs/view/{4100000000 + n}", "postedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d")} for n in range(5)]
    World.routes[INDEED_ROUTE] = [
        {"id": f"jk{n}", "title": "Applied AI Engineer", "companyDetails.name": f"Ind{n}", "formattedLocation": "Remote", "pubDate": 1793599200000,
         "location.countryCode": "US", "jobDescription": JD, "originalApplyUrl": "https://job-boards.greenhouse.io/ind1/jobs/1"} for n in range(3)]
    World.routes["/search"] = {"data": [{
        "job_id": "g1", "job_title": "AI Engineer", "employer_name": "Co1", "job_city": "New York", "job_state": "NY", "job_country": "US",
        "job_apply_link": "https://jobs.lever.co/co1/abc", "job_description": JD}]}
    (project / "config" / "boards.yaml").write_text(PAID_CONFIG)

    # First scheduled pass searches a full day back.
    World.calls.clear(); World.bodies.clear()
    paid_pass(project, server)
    assert len(paid_calls()) == 4
    first = [b for b in World.bodies if "urls" in b]
    indeed_bodies = [b for b in World.bodies if "query" in b]
    assert len(indeed_bodies) == 2 and all(b["postedWithinDays"] == "1" and b["count"] == 100 for b in indeed_bodies)
    assert all("f_TPR=r86400" in b["urls"][0] and b["limitPerSource"] == 18 and b["scrapeCompany"] is False for b in first)
    assert conn.execute("SELECT results FROM apify_usage WHERE source = 'linkedin'").fetchone()["results"] == 10
    assert conn.execute("SELECT results FROM apify_usage WHERE source = 'indeed'").fetchone()["results"] == 6
    stats = last_stats(conn)
    assert stats["stats"]["paid"] == {"linkedin": "searched", "indeed": "searched", "jsearch": "searched"} and stats["trigger"] == "schedule"
    assert conn.execute("SELECT url, date_basis FROM jobs WHERE ext_id = 'jk1'").fetchone() == {"url": "https://www.indeed.com/viewjob?jk=jk1", "date_basis": "alert"}
    assert conn.execute("SELECT ats FROM boards WHERE slug = 'ind1'").fetchone()["ats"] == "greenhouse", "employer board found from Indeed's apply link"

    # Today's date from LinkedIn must count as just found, not midnight.
    row = conn.execute("SELECT * FROM jobs WHERE ext_id = '4100000000'").fetchone()
    assert datetime.now(timezone.utc) - row["fresh_at"] < timedelta(minutes=3)

    # The same role found by both job sites is one row, and its employer board gets added.
    assert conn.execute("SELECT count(*) AS c FROM jobs WHERE company = 'Co1'").fetchone()["c"] == 1
    assert "LangGraph" in conn.execute("SELECT reason FROM jobs WHERE company = 'Co1'").fetchone()["reason"]
    assert conn.execute("SELECT ats FROM boards WHERE slug = 'co1'").fetchone()["ats"] == "lever"

    # Next 15-minute pass spends nothing.
    World.calls.clear()
    paid_pass(project, server)
    assert paid_calls() == [] and last_stats(conn)["stats"]["paid"]["linkedin"] == "not_due"

    # Check now straight after is held back by the cooldown.
    paid_pass(project, server, manual=True)
    assert paid_calls() == []
    stats = last_stats(conn)
    assert stats["stats"]["paid"] == {"linkedin": "cooldown", "indeed": "cooldown", "jsearch": "cooldown"} and stats["trigger"] == "manual"

    # An hour later Check now searches only that gap, plus the overlap.
    conn.execute("UPDATE search_state SET last_success_at = now() - interval '1 hour'")
    World.calls.clear(); World.bodies.clear()
    paid_pass(project, server, manual=True)
    assert len(paid_calls()) == 4
    window = int(re.search(r"f_TPR=r(\d+)", [b for b in World.bodies if "urls" in b][0]["urls"][0]).group(1))
    assert 5400 <= window <= 5460

    # At the daily cap nothing more is paid for, and the free sources still ran.
    conn.execute("UPDATE apify_usage SET results = CASE source WHEN 'linkedin' THEN 288 ELSE 1600 END")
    conn.execute("UPDATE search_state SET last_success_at = now() - interval '1 hour'")
    World.calls.clear()
    paid_pass(project, server, manual=True)
    stats = last_stats(conn)["stats"]
    assert paid_calls() == [] and stats["paid"]["linkedin"] == "daily_limit" and stats["boards_ok"] > 0
    conn.execute("UPDATE apify_usage SET results = 0")


def test_failed_paid_search_keeps_its_place_and_is_reported(project, server, conn):
    saved = World.routes.pop(PAID_ROUTE)
    conn.execute("UPDATE search_state SET last_success_at = now() - interval '7 hours', last_attempt_at = now() - interval '7 hours'")
    before = conn.execute("SELECT last_success_at FROM search_state WHERE key = 'linkedin:a'").fetchone()["last_success_at"]
    paid_pass(project, server, expect=1)
    stats = last_stats(conn)["stats"]
    assert stats["paid"]["linkedin"] == "failed" and any("LinkedIn search" in e for e in stats["errors"])
    assert any("LinkedIn search" in a for a in stats["alerts"])
    assert conn.execute("SELECT last_success_at FROM search_state WHERE key = 'linkedin:a'").fetchone()["last_success_at"] == before
    World.routes[PAID_ROUTE] = saved


def test_full_results_warn_that_roles_may_be_missing(project, server, conn):
    World.routes[PAID_ROUTE] = [
        {"id": str(4200000000 + n), "title": "AI Engineer", "companyName": f"Big{n}", "location": "New York, NY",
         "link": f"https://www.linkedin.com/jobs/view/{4200000000 + n}", "postedAt": "2026-11-03"} for n in range(18)]
    conn.execute("UPDATE search_state SET last_success_at = now() - interval '7 hours', last_attempt_at = now() - interval '7 hours'")
    paid_pass(project, server)
    assert any("hit its limit" in w for w in last_stats(conn)["stats"]["warnings"])


def test_jsearch_monthly_cap_keeps_requests_for_scheduled_runs(project, server, conn):
    conn.execute("UPDATE jsearch_usage SET requests = 199")
    conn.execute("UPDATE search_state SET last_success_at = now() - interval '1 hour' WHERE key = 'jsearch'")
    World.calls.clear()
    paid_pass(project, server, manual=True, apify=False)
    assert "/search" not in World.calls and last_stats(conn)["stats"]["paid"]["jsearch"] == "monthly_limit"


def test_dry_run_needs_no_database_and_reports_bad_boards(project, server):
    (project / "config" / "boards.yaml").write_text(CONFIG)
    World.routes = base_world()
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env.update({"JR_GREENHOUSE_HOST": server, "JR_LEVER_HOST": server, "JR_ASHBY_HOST": server, "JR_WORKDAY_HOST": server})
    done = subprocess.run([sys.executable, "-m", "collector", "--dry-run"], cwd=project, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "Boards working: 5 of 6" in done.stdout and "greenhouse/doesnotexist" in done.stdout
    assert "Applied AI Engineer at Acme" in done.stdout
