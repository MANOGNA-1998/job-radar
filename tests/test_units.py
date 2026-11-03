from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from collector import scoring, timing
from collector.__main__ import board_from_link
from collector.model import dedup_key, strip_html
from collector.sources import apify_linkedin, indeed, jsearch, linkedin_email, workday

PROFILE = scoring.load_profile()


@pytest.mark.parametrize("title,tier", [
    ("Applied AI Engineer", "Applied AI"),
    ("Senior Applied AI Engineer", "Applied AI"),
    ("Applied AI ML, Associate", "Applied AI"),
    ("Member of Technical Staff, Applied AI", "Applied AI"),
    ("AI Engineer 3", "AI Engineer"),
    ("Agentic AI Engineer", "AI Engineer"),
    ("Software Development Engineer II, Generative AI", "AI Engineer"),
    ("Forward Deployed Engineer (Public Sector)", "Forward Deployed"),
    ("Machine Learning Engineer, Search, Ads & Recommendations", "Applied ML"),
    ("Software Engineer, Machine Learning", "Applied ML"),
    ("Research Engineer", "Applied ML"),
    ("Software Engineer II", "Software Engineer"),
    ("Senior Backend Engineer", "Software Engineer"),
    ("Product Engineer", "Software Engineer"),
    ("Python Developer", "Software Engineer"),
    ("Associate, Data Engineer", "Data Engineer"),
])
def test_titles_from_past_applications_match(title, tier):
    assert scoring.title_tier(title, PROFILE)["label"] == tier


@pytest.mark.parametrize("title", [
    "Staff Software Engineer", "Principal AI Engineer", "Engineering Manager, AI", "Software Engineer Intern",
    "Director of Machine Learning", "Head of AI", "Sales Engineer", "iOS Software Engineer", "Account Executive",
    "Recruiter, AI Engineering", "Product Designer", "Senior Staff Machine Learning Engineer",
])
def test_unwanted_titles_are_dropped(title):
    assert scoring.title_tier(title, PROFILE) is None


@pytest.mark.parametrize("location,expected", [
    ("New York, NY", "nyc"), ("NYC", "nyc"), ("New York City | London", "nyc"), ("Remote", "remote"),
    ("Remote - US", "remote"), ("San Francisco, CA", "other_us"), ("Austin, TX", "other_us"), ("United States", "other_us"),
    ("London, UK", "non_us"), ("Bengaluru, India", "non_us"), ("Remote - EMEA", "non_us"), ("Toronto, Canada", "non_us"),
    ("Hyderabad, IN", "non_us"), ("Remote, Canada", "non_us"), ("", "unknown"), ("Dublin | Tokyo", "non_us"),
    ("San Francisco, CA | Toronto, Canada", "other_us"),
])
def test_location_classes(location, expected):
    assert scoring.classify_location(location) == expected


@pytest.mark.parametrize("text,years", [
    ("You have 3+ years of experience with Python.", 3),
    ("5-7 years of relevant experience", 5),
    ("Minimum of 8 years in software", 8),
    ("Experience: 4+ years building systems", 4),
    ("We were founded 20 years ago and our experience is deep", None),
    ("Great team, no numbers here", None),
])
def test_required_years(text, years):
    assert scoring.required_years(text) == years


def test_strong_match_outranks_weaker_ones():
    jd = "Build agents with LangGraph and RAG over LLMs in Python. Run evals. 3+ years of experience."
    best = scoring.score_job("Applied AI Engineer", "New York, NY", jd, PROFILE)
    swe = scoring.score_job("Software Engineer", "Austin, TX", "Java and Kafka. 3+ years of experience.", PROFILE)
    senior = scoring.score_job("Applied AI Engineer", "New York, NY", jd.replace("3+", "10+"), PROFILE)
    assert best.score > senior.score > 0 and best.score > swe.score
    assert "NYC" in best.reason and "LangGraph" in best.reason and "3+ years" in best.reason
    assert 0 <= best.score <= 100


def test_not_a_fit_feedback_lowers_similar_titles():
    penalties = scoring.title_penalties(["Security Engineer AI"] * 3, ["Applied AI Engineer"])
    assert "security" in penalties
    plain = scoring.score_job("AI Security Engineer", "New York, NY", "Python", PROFILE)
    lowered = scoring.score_job("AI Security Engineer", "New York, NY", "Python", PROFILE, penalties)
    assert lowered.score < plain.score and "not a fit" in lowered.reason


def test_dedup_key_ignores_company_suffix_and_location_words():
    assert dedup_key("Stripe, Inc.", "AI Engineer (Remote)") == dedup_key("Stripe", "AI Engineer - NYC")
    assert dedup_key("Stripe", "AI Engineer") != dedup_key("Plaid", "AI Engineer")


def test_strip_html_handles_double_escaped_greenhouse_content():
    assert "Python" in strip_html("&lt;p&gt;Use &amp;lt;b&amp;gt;Python&amp;lt;/b&amp;gt;&lt;/p&gt;")
    assert "<" not in strip_html("&lt;p&gt;Use &lt;b&gt;Python&lt;/b&gt;&lt;/p&gt;")


@pytest.mark.parametrize("title", [
    "Product Management, GenAI Platforms", "Security GRC Analyst, Forward Deployed Assurance", "Security GRC Lead, Forward Deployed Assurance",
    "Forward Deployed (Value Acceleration) Consultant", "ML Silicon Performance Lead", "Front End SMTS Software Engineer - MeshMesh",
    "Senior Software Engineer, GRC",
])
def test_false_positives_from_live_check_are_dropped(title):
    assert scoring.title_tier(title, PROFILE) is None


@pytest.mark.parametrize("title,tier", [
    ("Senior AI Engineer – Forward Deployed, Agent Platform (REMOTE)", "AI Engineer"),
    ("Forward Deployed Engineer", "Forward Deployed"), ("MACHINE LEARNING ENGIENEER II (REMOTE)", "Applied ML"),
    ("Software Engineering, AI Systems (SMTS)", "AI Engineer"), ("Full-stack Engineer 4", "Software Engineer"),
])
def test_real_titles_from_live_check_still_match(title, tier):
    assert scoring.title_tier(title, PROFILE)["label"] == tier


@pytest.mark.parametrize("location,path,expected", [
    ("", "/job/India-Bengaluru-Karnataka/Software-Engineer-II_JREQ203939", "non_us"),
    ("3 Locations", "/job/New-York-NY/AI-Engineer-4_R1", "nyc"),
    ("4 Locations", "/job/Richmond-VA/Full-stack-Engineer-4_R2", "other_us"),
    ("7 Locations", "/job/India-Bengaluru/Role_R3", "unknown"),
    ("7 Locations", "/job/California---San-Francisco/Role_R4", "other_us"),
    ("Plano, TX", "/job/Plano-TX/Role_R5", "other_us"),
    ("STORE SUPPORT CENTER, ATLANTA - 9090", "/job/x/y", "other_us"),
    ("GEORGIA - VIRTUAL - GA01", "/job/x/y", "remote"),
    ("Work At Home-Texas", "/job/x/y", "remote"),
    ("California - San Francisco", "/job/x/y", "other_us"),
])
def test_workday_real_locations(location, path, expected):
    assert scoring.classify_location(workday.listed_location(location, path)) == expected


def test_workday_date_without_time_is_not_shown_as_midnight():
    now = datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
    assert workday._posted("2026-11-03", now) is None
    assert workday._posted("2026-10-20", now).day == 20


def test_workday_posted_text():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert workday.parse_posted_on("Posted Today", now) == now
    assert workday.parse_posted_on("Posted Yesterday", now) == now - timedelta(days=1)
    assert workday.parse_posted_on("Posted 3 Days Ago", now) == now - timedelta(days=3)
    assert workday.parse_posted_on("Posted 30+ Days Ago", now) == now - timedelta(days=30)
    assert workday.parse_posted_on("", now) is None


ALERT_HTML = """<html><head><style>a{color:red}</style></head><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4312345678/?trackingId=abc"><img src="x"></a></td>
<td><a href="https://www.linkedin.com/comm/jobs/view/4312345678/?trackingId=abc">Applied AI Engineer</a>
<p>Finch · New York, NY (Hybrid)</p><p>Actively recruiting</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4399999999/?x=1">Software Engineer, ML</a>
<p>Plaid</p><p>San Francisco, CA</p><p>10 connections</p></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search/?x=1">See all jobs</a></td></tr>
</table></body></html>"""


def alert_message(sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", html=ALERT_HTML, text=None):
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = "Applied AI Engineer: Finch and more"
    message["Date"] = "Tue, 03 Nov 2026 14:30:00 -0500"
    message.set_content(text or "plain")
    if html:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


def test_linkedin_alert_html():
    jobs = linkedin_email.parse_message(alert_message())
    assert [(j.title, j.company, j.location, j.ext_id) for j in jobs] == [
        ("Applied AI Engineer", "Finch", "New York, NY (Hybrid)", "4312345678"),
        ("Software Engineer, ML", "Plaid", "San Francisco, CA", "4399999999"),
    ]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4312345678"
    assert jobs[0].posted_at == datetime(2026, 11, 3, 19, 30, tzinfo=timezone.utc)


def test_linkedin_alert_plain_text_fallback():
    text = """Your job alert

Applied AI Engineer
Finch
New York, NY
View job: https://www.linkedin.com/comm/jobs/view/4312345678/?trackingId=abc

---------------------------------------------------------

AI Engineer
Ramp
Remote
View job: https://www.linkedin.com/comm/jobs/view/4312345679/?trackingId=abc
"""
    jobs = linkedin_email.parse_message(alert_message(html=None, text=text))
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Applied AI Engineer", "Finch", "New York, NY"), ("AI Engineer", "Ramp", "Remote"),
    ]


def test_mail_not_from_linkedin_is_ignored():
    assert linkedin_email.parse_message(alert_message(sender="Someone <spam@example.com>")) == []


NOW = datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
# Copied from a real run of the Apify scraper on Nov 3, 2026, with the long fields trimmed.
REAL_APIFY_ITEM = {
    "id": "4486894472",
    "link": "https://www.linkedin.com/jobs/view/machine-learning-engineer-5-ic-at-capital-one-4486894472?position=3&pageNum=0",
    "title": "Machine Learning Engineer 5 (IC)", "companyName": "Capital One", "location": "Richmond, VA",
    "postedAt": "2026-11-03", "applyUrl": "", "descriptionText": "Python and RAG", "seniorityLevel": "Not Applicable",
}


def test_apify_real_item_is_not_shown_as_posted_at_midnight():
    job = apify_linkedin.to_job(REAL_APIFY_ITEM, NOW)
    assert (job.title, job.company, job.location) == ("Machine Learning Engineer 5 (IC)", "Capital One", "Richmond, VA")
    assert job.url == "https://www.linkedin.com/jobs/view/4486894472"
    assert job.posted_at == NOW and job.date_basis == "alert" and job.description == "Python and RAG"
    assert scoring.classify_location("Washington DC-Baltimore Area") == "other_us"


def test_apify_older_date_keeps_that_date():
    job = apify_linkedin.to_job({**REAL_APIFY_ITEM, "postedAt": "2026-10-20"}, NOW)
    assert job.date_basis == "posted" and job.posted_at.day == 20
    assert apify_linkedin.to_job({"title": "No id or link"}, NOW) is None


# Copied from a real run of the Apify Indeed scraper on Nov 3, 2026.
REAL_INDEED_ITEM = {
    "id": "dd80c8b4401f4bd6", "viewJobLink": "/viewjob?jk=dd80c8b4401f4bd6", "title": "AI Engineer", "formattedLocation": "Princeton, NJ",
    "pubDate": 1793599200000, "expired": False, "originalApplyUrl": "https://digitalcareers.infosys.com/global-careers/company-job/description/reqid/155393BR?Codes=Indeed",
    "companyDetails.name": "Infosys", "location.countryCode": "US", "jobDescription": "Python and RAG",
}


def test_indeed_real_item():
    job = indeed.to_job(REAL_INDEED_ITEM, NOW)
    assert (job.title, job.company, job.location) == ("AI Engineer", "Infosys", "Princeton, NJ")
    assert job.url == "https://www.indeed.com/viewjob?jk=dd80c8b4401f4bd6" and job.description == "Python and RAG"
    assert job.date_basis == "alert" and job.posted_at == NOW, "a day-only date must not show as posted at midnight"
    assert job.apply_links == [REAL_INDEED_ITEM["originalApplyUrl"]]


def test_indeed_real_time_and_skipped_listings():
    timed = indeed.to_job({**REAL_INDEED_ITEM, "pubDate": 1793734815736}, NOW)
    assert timed.date_basis == "posted" and timed.posted_at.minute == 40
    older = indeed.to_job({**REAL_INDEED_ITEM, "pubDate": 1792000000000 - 1792000000000 % 3600000}, NOW)
    assert older.date_basis == "posted"
    assert indeed.to_job({**REAL_INDEED_ITEM, "companyDetails.name": None}, NOW) is None, "no employer name"
    assert indeed.to_job({**REAL_INDEED_ITEM, "location.countryCode": "CA"}, NOW) is None
    assert indeed.to_job({**REAL_INDEED_ITEM, "expired": True}, NOW) is None


def test_indeed_day_windows():
    assert [indeed.posted_within_days(s) for s in (3600, 86400, 90000, 5 * 86400, 7 * 86400, 9 * 86400)] == ["1", "1", "3", "7", "7", "14"]


def test_search_url_uses_requested_window():
    url = apify_linkedin.search_url("Applied AI Engineer", "United States", 5400)
    assert "f_TPR=r5400" in url and "keywords=Applied%20AI%20Engineer" in url and "sortBy=DD" in url


SCHEDULE = {"every_hours": 6, "manual_cooldown_minutes": 30, "overlap_minutes": 30}


def state(success=None, attempt=None):
    return {"last_success_at": success, "last_attempt_at": attempt or success}


def test_slots_are_6_hours_in_new_york_time():
    assert timing.latest_slot(NOW, 6).hour == 12  # 4pm in New York
    assert timing.latest_slot(NOW.replace(hour=23), 6).hour == 18  # 6pm slot starts at 6pm local


def test_scheduled_search_runs_once_per_slot():
    assert timing.search_decision(state(), SCHEDULE, NOW, False) == "due"
    assert timing.search_decision(state(NOW - timedelta(minutes=10)), SCHEDULE, NOW, False) == "not_due"
    assert timing.search_decision(state(NOW - timedelta(hours=5)), SCHEDULE, NOW, False) == "due"


def test_check_now_then_next_slot_searches_only_the_gap():
    manual_at = datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)  # 5pm in New York
    six_pm = datetime(2026, 11, 3, 23, 7, tzinfo=timezone.utc)
    after = state(manual_at)
    assert timing.search_decision(after, SCHEDULE, six_pm, False) == "due"
    assert timing.window_seconds(after, SCHEDULE, six_pm) == 67 * 60 + 30 * 60


def test_check_now_cooldown():
    assert timing.search_decision(state(NOW - timedelta(minutes=10)), SCHEDULE, NOW, True) == "cooldown"
    assert timing.search_decision(state(NOW - timedelta(minutes=45)), SCHEDULE, NOW, True) == "due"


def test_failed_search_waits_an_hour_and_keeps_its_window():
    failed = state(NOW - timedelta(hours=9), NOW - timedelta(minutes=20))
    assert timing.search_decision(failed, SCHEDULE, NOW, False) == "not_due"
    failed = state(NOW - timedelta(hours=9), NOW - timedelta(minutes=70))
    assert timing.search_decision(failed, SCHEDULE, NOW, False) == "due"
    assert timing.window_seconds(failed, SCHEDULE, NOW) == 9 * 3600 + 1800


def test_window_limits():
    assert timing.window_seconds(state(), SCHEDULE, NOW) == 86400
    assert timing.window_seconds(state(NOW - timedelta(minutes=1)), SCHEDULE, NOW) == 3600
    assert timing.window_seconds(state(NOW - timedelta(days=30)), SCHEDULE, NOW) == 7 * 86400


def test_jsearch_mapping_prefers_employer_link():
    job = jsearch.to_job({
        "job_id": "abc==", "job_title": "AI Engineer", "employer_name": "Newco", "job_city": "New York", "job_state": "NY", "job_country": "US",
        "job_apply_link": "https://www.linkedin.com/jobs/view/1", "job_posted_at_datetime_utc": "2026-11-03T18:00:00.000Z",
        "apply_options": [{"publisher": "LinkedIn", "apply_link": "https://www.linkedin.com/jobs/view/1", "is_direct": False},
                          {"publisher": "Newco", "apply_link": "https://jobs.ashbyhq.com/newco/123", "is_direct": True}],
        "job_description": "RAG",
    }, NOW)
    assert job.url == "https://jobs.ashbyhq.com/newco/123" and job.location == "New York, NY" and job.date_basis == "posted"
    assert board_from_link(job.apply_links[-1]) == ("ashby", "newco")
    assert jsearch.to_job({"job_id": "x", "job_title": "AI Engineer", "employer_name": "Foo", "job_country": "IN"}, NOW) is None
    assert jsearch.date_filter(21600) == "today" and jsearch.date_filter(200000) == "3days" and jsearch.date_filter(500000) == "week"


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/stripe/jobs/123", ("greenhouse", "stripe")),
    ("https://job-boards.greenhouse.io/figma/jobs/9", ("greenhouse", "figma")),
    ("https://jobs.lever.co/Plaid/abc", ("lever", "plaid")),
    ("https://www.linkedin.com/jobs/view/1", None),
    ("", None),
])
def test_board_from_link(url, expected):
    assert board_from_link(url) == expected


def test_imap_fetch_reads_without_marking_mail_and_dedupes(monkeypatch):
    calls = {}

    class FakeImap:
        def __init__(self, host, port):
            calls["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            calls["user"] = user

        def select(self, box, readonly=False):
            calls["readonly"] = readonly

        def search(self, charset, *criteria):
            return "OK", [b"1 2 3"]

        def fetch(self, number, parts):
            calls["parts"] = parts
            raw = alert_message(sender="Someone <x@example.com>") if number == b"3" else alert_message()
            return "OK", [(b"header", raw)]

    monkeypatch.setattr(linkedin_email.imaplib, "IMAP4_SSL", FakeImap)
    jobs = linkedin_email.fetch("imap.example.com", "me@example.com", "secret")
    assert calls["readonly"] is True and "PEEK" in calls["parts"]
    assert sorted(j.ext_id for j in jobs) == ["4312345678", "4399999999"]
