"""Decide whether a job fits, and score it 0 to 100 with a readable reason."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config"

STATES = "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
US_ABBREVIATIONS = re.compile(rf",\s*(?:{STATES})\b|\b(?:{STATES})\s*$|\bU\.?S\.?A?\.?\b")
US_PLACES = re.compile(
    r"United States|\bAmericas?\b|\b(?:New York|NYC|Manhattan|Brooklyn|San Francisco|Bay Area|Palo Alto|Mountain View|Sunnyvale|San Jose|"
    r"San Mateo|Santa Clara|Seattle|Bellevue|Redmond|Austin|Boston|Chicago|Los Angeles|Denver|Atlanta|Dallas|Houston|Washington|Philadelphia|"
    r"Pittsburgh|Miami|San Diego|Jersey City|McLean|Richmond|Plano|Charlotte|Baltimore|Alabama|Alaska|Arizona|Arkansas|California|Colorado|"
    r"Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|"
    r"Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
    r"Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|West Virginia|Wisconsin|Wyoming)\b",
    re.I,
)
NON_US = re.compile(
    r"\b(?:Canada|Toronto|Vancouver|Montreal|Ontario|Mexico|Brazil|Argentina|Colombia|UK|U\.K\.|United Kingdom|England|London|Ireland|Dublin|"
    r"Germany|Berlin|Munich|France|Paris|Spain|Madrid|Barcelona|Portugal|Lisbon|Italy|Netherlands|Amsterdam|Belgium|Sweden|Stockholm|Norway|"
    r"Denmark|Copenhagen|Finland|Poland|Warsaw|Romania|Czech|Prague|Switzerland|Zurich|Austria|Vienna|Israel|Tel Aviv|UAE|Dubai|India|"
    r"Bengaluru|Bangalore|Hyderabad|Pune|Mumbai|Gurgaon|Gurugram|Chennai|Delhi|Noida|China|Shanghai|Beijing|Hong Kong|Taiwan|Taipei|Japan|Tokyo|"
    r"Korea|Seoul|Singapore|Malaysia|Philippines|Vietnam|Indonesia|Thailand|Australia|Sydney|Melbourne|New Zealand|South Africa|Nigeria|Kenya|"
    r"EMEA|APAC|LATAM|Europe|Asia)\b",
    re.I,
)
NYC = re.compile(r"\b(?:New York|NYC|Manhattan|Brooklyn|Jersey City|Hoboken)\b", re.I)
REMOTE = re.compile(r"\bremote\b|\banywhere\b|\bvirtual\b|\b(?:work|working) (?:at|from) home\b", re.I)
PART_SPLIT = re.compile(r"\s*(?:\||;|/|\bor\b|\band\b)\s*")
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b[^.\n]{0,60}?\bexperience|experience[^.\n]{0,30}?(\d{1,2})\s*\+\s*(?:years?|yrs?)|(?:minimum|at least)\s+(?:of\s+)?(\d{1,2})\s+(?:years?|yrs?)", re.I)


@dataclass
class Score:
    score: int
    tier: str
    reason: str


def load_profile() -> dict:
    profile = yaml.safe_load((CONFIG / "profile.yaml").read_text())
    profile["_exclude"] = re.compile(profile["exclude_titles"], re.I)
    for tier in profile["title_tiers"]:
        tier["_re"] = re.compile(tier["pattern"], re.I)
    for skill in profile["skills"]:
        skill["_re"] = re.compile(skill["pattern"], re.I)
    return profile


def classify_location(location: str) -> str:
    """Returns nyc, remote, other_us, unknown, or non_us. A job is non_us only if every listed place is."""
    if not (location or "").strip():
        return "unknown"
    verdicts = set()
    for part in PART_SPLIT.split(location):
        if not part.strip():
            continue
        if NON_US.search(part):
            verdicts.add("non_us")
        elif NYC.search(part):
            verdicts.add("nyc")
        elif REMOTE.search(part):
            verdicts.add("remote")
        elif US_ABBREVIATIONS.search(part) or US_PLACES.search(part):
            verdicts.add("other_us")
        else:
            verdicts.add("unknown")
    for verdict in ("nyc", "remote", "other_us", "unknown"):
        if verdict in verdicts:
            return verdict
    return "non_us"


def title_tier(title: str, profile: dict) -> Optional[dict]:
    if profile["_exclude"].search(title or ""):
        return None
    for tier in profile["title_tiers"]:
        if tier["_re"].search(title or ""):
            return tier
    return None


def passes_prefilter(title: str, location: str, profile: dict) -> bool:
    return title_tier(title, profile) is not None and classify_location(location) != "non_us"


def required_years(description: str) -> Optional[int]:
    values = []
    for match in YEARS.finditer(description or ""):
        value = next((int(g) for g in match.groups() if g), None)
        if value is not None and value <= 15:
            values.append(value)
    return max(values) if values else None


def title_penalties(rejected_titles: list[str], applied_titles: list[str]) -> dict[str, int]:
    """Words that keep appearing in jobs you marked not a fit, and rarely in jobs you applied to."""
    def words(title):
        return set(re.findall(r"[a-z][a-z+#]{2,}", title.lower())) - {"engineer", "software", "senior", "and", "the", "for"}
    rejected, applied = {}, {}
    for title in rejected_titles:
        for word in words(title):
            rejected[word] = rejected.get(word, 0) + 1
    for title in applied_titles:
        for word in words(title):
            applied[word] = applied.get(word, 0) + 1
    return {w: 5 for w, count in rejected.items() if count >= 3 and count >= 3 * applied.get(w, 0)}


def score_job(title: str, location: str, description: str, profile: dict, penalties: dict[str, int] | None = None) -> Optional[Score]:
    tier = title_tier(title, profile)
    place = classify_location(location)
    if tier is None or place == "non_us":
        return None
    total = tier["points"]
    notes = [f"{tier['label']} title"]

    points = profile["location_points"][place]
    total += points
    notes.append({"nyc": "NYC", "remote": "remote", "other_us": "US", "unknown": "location not listed"}[place])

    if description:
        matched = [s for s in profile["skills"] if s["_re"].search(description)]
        total += min(profile["skills_cap"], sum(s["points"] for s in matched))
        if matched:
            names = [s["name"] for s in sorted(matched, key=lambda s: -s["points"])[:5]]
            notes.append("mentions " + ", ".join(names))
        years = required_years(description)
        limits = profile["years"]
        if years is None:
            total += 5
        elif years <= limits["comfortable"]:
            total += 10
            notes.append(f"asks for {years}+ years")
        elif years <= limits["stretch"]:
            total += 2
            notes.append(f"asks for {years}+ years, a stretch")
        else:
            total -= 15
            notes.append(f"asks for {years}+ years, likely too senior")
    else:
        # Alert emails have no description. Credit an average skill match so they aren't buried.
        total += 12
        notes.append("scored from title only")

    if penalties:
        hits = [w for w in penalties if re.search(rf"\b{re.escape(w)}\b", title.lower())]
        if hits:
            cut = min(15, sum(penalties[w] for w in hits))
            total -= cut
            notes.append("similar to roles you marked not a fit")

    if re.search(r"\blead\b", title, re.I):
        total -= 8
    return Score(max(0, min(100, total)), tier["label"], ", ".join(notes))
