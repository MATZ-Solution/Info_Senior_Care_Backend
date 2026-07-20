"""
Phase 3 -- per-facility_type templates turning Layer 1 (infomary_facilities) +
Layer 2 (infomary_facility_detail.attributes) fields into the prose that gets
embedded and pushed to Qdrant (architecture docs, Section 4).

Two deliberate departures from the docs' worked examples in Section 4, both
argued explicitly rather than accidental:

1. Facility NAME is never included, per Section 5 ("Why Facility Names Are
   Never Embedded") -- shared generic words in names (e.g. "Family," "Care,"
   "Hospice") cause false-positive vector clustering unrelated to real
   similarity. Section 4's example sentences open with the name, but Section
   5's argued position wins over that prose; the name still lives in Supabase
   for display, it's just never fed to the embedding model.
2. city/state are also omitted here -- they are structured *filter* fields
   (Qdrant payload / Postgres), not semantic content; Section 4's own example
   sentences never mention them either, only ownership/scale/quality fields.

Each builder takes (facility: dict, attributes: dict) -> str and must never
raise for missing/null fields -- absent data is omitted from the sentence, not
rendered as "None". schemas.py is the source of truth for which attributes
keys can even exist per type; hospice/irf intentionally have none (_EMPTY).
"""

TYPE_LABELS = {
    "nursing_home": "nursing home",
    "home_health": "home health agency",
    "hospice": "hospice provider",
    "irf": "inpatient rehabilitation facility",
    "ltch": "long-term care hospital",
}

OWNERSHIP_ADJ = {
    "for_profit": "for-profit",
    "nonprofit": "nonprofit",
    "government": "government-operated",
}


def _opening(facility_type: str, facility: dict) -> str:
    label = TYPE_LABELS[facility_type]
    adj = OWNERSHIP_ADJ.get(facility.get("ownership_type"))
    first_word = adj if adj else label
    article = "An" if first_word[0].lower() in "aeiou" else "A"
    return f"{article} {adj} {label}" if adj else f"{article} {label}"


def _cert_year(facility: dict):
    d = facility.get("certification_date")
    return getattr(d, "year", None)


def _join_facts(opening: str, facts: list) -> str:
    parts = [opening] + [f for f in facts if f]
    return ". ".join(parts) + "."


def _nursing_home(facility: dict, attributes: dict) -> str:
    opening = _opening("nursing_home", facility)
    beds = attributes.get("certified_beds")
    avg = attributes.get("avg_residents_per_day")
    if beds is not None and avg is not None:
        opening += f" with {beds} certified beds, averaging {avg} residents daily"
    elif beds is not None:
        opening += f" with {beds} certified beds"

    facts = []

    ratings = attributes.get("ratings") or {}
    overall = ratings.get("overall")
    if overall is not None:
        sub = [
            f"{label} {ratings[key]}/5"
            for key, label in (("staffing", "staffing"), ("health_inspection", "health inspections"), ("qm", "quality measures"))
            if ratings.get(key) is not None
        ]
        facts.append(f"Overall CMS rating {overall}/5 ({', '.join(sub)})" if sub else f"Overall CMS rating {overall}/5")

    staffing_hours = attributes.get("staffing_hours") or {}
    rn = staffing_hours.get("rn")
    if rn is not None:
        facts.append(f"Reported RN staffing of {rn} hours per resident per day")

    fines = attributes.get("number_of_fines")
    penalties = attributes.get("total_penalties")
    if fines is not None or penalties is not None:
        bits = []
        if fines is not None:
            bits.append(f"{fines} fine{'s' if fines != 1 else ''} on record")
        if penalties is not None:
            bits.append(f"{penalties} total penalt{'ies' if penalties != 1 else 'y'}")
        facts.append(", ".join(bits).capitalize())

    special = attributes.get("special_focus_status")
    if special:
        facts.append(f"Special focus status: {special}")
    abuse = attributes.get("abuse_icon")
    if abuse:
        facts.append(f"Abuse citation flag: {abuse}")

    year = _cert_year(facility)
    if year:
        facts.append(f"certified since {year}")

    return _join_facts(opening, facts)


def _home_health(facility: dict, attributes: dict) -> str:
    opening = _opening("home_health", facility)
    facts = []

    services = attributes.get("services") or {}
    offered = [name.replace("_", " ") for name, val in services.items() if val]
    if offered:
        facts.append(f"offering {', '.join(offered)} services")

    rating = attributes.get("quality_star_rating")
    if rating is not None:
        facts.append(f"quality of patient care rating {rating}/5")

    for key, label in (
        ("discharge_to_community_category", "discharge-to-community performance"),
        ("readmission_category", "readmission performance"),
        ("preventable_hospitalization_category", "preventable hospitalization performance"),
    ):
        val = attributes.get(key)
        if val:
            facts.append(f"{label} categorized as '{val}'")

    outcomes = attributes.get("outcomes") or {}
    for name, val in outcomes.items():
        if val:
            facts.append(f"{name.replace('_', ' ')}: {val}")

    year = _cert_year(facility)
    if year:
        facts.append(f"certified since {year}")

    return _join_facts(opening, facts)


def _thin_type(facility_type: str):
    def build(facility: dict, attributes: dict) -> str:
        opening = _opening(facility_type, facility)
        year = _cert_year(facility)
        facts = [f"certified since {year}"] if year else []
        return _join_facts(opening, facts)
    return build


def _ltch(facility: dict, attributes: dict) -> str:
    opening = _opening("ltch", facility)
    beds = attributes.get("total_beds")
    if beds is not None:
        opening += f" with {beds} total beds"
    year = _cert_year(facility)
    facts = [f"certified since {year}"] if year else []
    return _join_facts(opening, facts)


CONTENT_BUILDERS = {
    "nursing_home": _nursing_home,
    "home_health": _home_health,
    "hospice": _thin_type("hospice"),
    "irf": _thin_type("irf"),
    "ltch": _ltch,
}


def build_content(facility_type: str, facility: dict, attributes: dict) -> str:
    try:
        builder = CONTENT_BUILDERS[facility_type]
    except KeyError:
        raise ValueError(f"no content builder registered for facility_type={facility_type!r}")
    return builder(facility, attributes)
