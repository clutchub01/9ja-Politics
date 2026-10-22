#!/usr/bin/env python3
"""9ja Politics - evidence-based rating engine (governorship, methodology gov-0.1-draft).

Scores are computed HERE, in code, from human-verified evidence files.
Nothing in this module estimates a score from age, party, or free text.

Evidence file:  data/evidence/<state_slug>/<candidate_id>.json
Commands:
    python ratings.py init      # create empty draft evidence stubs for every governor candidate
    python ratings.py validate  # check every evidence file against the rules
    python ratings.py report    # status / coverage table
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

METHODOLOGY_VERSION = "gov-0.1-draft"
MIN_COVERAGE = 0.5  # share of the 6 domains that must be assessed before a component is publishable

# Order = radar axis order on the governor chart.
DOMAINS = [
    ("economy_jobs", "State revenue & jobs"),
    ("education", "Education & schools"),
    ("health", "Primary healthcare"),
    ("infrastructure", "Roads & power"),
    ("security", "Security & rule of law"),
    ("youth", "Youth & skills"),
]
DOMAIN_KEYS = [k for k, _ in DOMAINS]

# Only these can justify a score. Journalism and candidate statements are context for finding
# claims to verify, never proof on their own.
SCORING_TYPES = {"official_record", "primary_document", "independent_analysis"}
CONTEXT_TYPES = {"journalism", "candidate_statement"}
ALL_SOURCE_TYPES = SCORING_TYPES | CONTEXT_TYPES
INTEGRITY_STATUSES = {"allegation", "proceeding", "final_judgment", "overturned"}


# ---------------------------------------------------------------- ids and paths
def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def state_slug(state):
    return re.sub(r"[^a-z0-9]", "_", str(state).lower())


def candidate_id(state, office, name):
    return f"{slugify(state)}-{slugify(office)}-{slugify(name)}"


def evidence_path(root, state, cid):
    return os.path.join(root, state_slug(state), cid + ".json")


# ---------------------------------------------------------------- stub
def new_evidence_stub(cid, state, party, name):
    return {
        "candidate_id": cid,
        "candidate": {"name": name, "party": party},
        "state": state,
        "office": "Governor",
        "methodology_version": METHODOLOGY_VERSION,
        "review": {"status": "draft", "reviewer": "", "reviewed_on": ""},
        "manifesto": {"found": False, "source_ids": []},
        "offices_held": [],
        "sources": [],
        "domains": {
            k: {
                "plan": {"level": None, "locator": "", "source_ids": [], "note": ""},
                "record": {"level": None, "responsibility_basis": "", "source_ids": [], "note": ""},
            }
            for k in DOMAIN_KEYS
        },
        "integrity_items": [],
    }


# ---------------------------------------------------------------- validation
def _is_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def _grade_sources(ids, sources):
    return [sources[i] for i in ids if i in sources and sources[i].get("type") in SCORING_TYPES]


def _check_ids(errors, label, ids, sources):
    missing = [i for i in ids if i not in sources]
    if missing:
        errors.append(f"{label}: unknown source ids {missing}")
    return not missing


def _check_component(errors, label, comp, kind, doc, sources):
    lvl = comp.get("level")
    if lvl is None:
        return
    if isinstance(lvl, bool) or not isinstance(lvl, int) or not 0 <= lvl <= 5:
        errors.append(f"{label}: level must be an integer 0-5 or null")
        return
    ids = comp.get("source_ids") or []
    if not ids:
        errors.append(f"{label}: a scored level needs source_ids")
        return
    if not _check_ids(errors, label, ids, sources):
        return
    if not _grade_sources(ids, sources):
        errors.append(f"{label}: needs at least one scoring-grade source "
                      f"({', '.join(sorted(SCORING_TYPES))}); journalism and candidate statements are context only")
    if kind == "plan":
        if not (doc.get("manifesto") or {}).get("found"):
            errors.append(f"{label}: a plan level needs manifesto.found = true")
        if not (comp.get("locator") or "").strip():
            errors.append(f"{label}: a plan level needs a locator (page or section of the manifesto)")
    else:
        if not doc.get("offices_held"):
            errors.append(f"{label}: a record level needs offices_held (record applies only to office actually held)")
        if not (comp.get("responsibility_basis") or "").strip():
            errors.append(f"{label}: a record level needs responsibility_basis (role, years, what they were responsible for)")


def validate_evidence(doc):
    """Return a list of error strings. Empty list means the file is valid."""
    if not isinstance(doc, dict):
        return ["file is not a JSON object"]
    errors = []
    if not isinstance(doc.get("candidate_id"), str) or not doc.get("candidate_id"):
        errors.append("candidate_id missing")
    if doc.get("methodology_version") != METHODOLOGY_VERSION:
        errors.append(f"methodology_version must be '{METHODOLOGY_VERSION}'")

    review = doc.get("review") or {}
    status = review.get("status")
    if status not in ("draft", "verified"):
        errors.append("review.status must be 'draft' or 'verified'")
    if status == "verified":
        if not (review.get("reviewer") or "").strip():
            errors.append("verified requires review.reviewer")
        if not _is_date(review.get("reviewed_on")):
            errors.append("verified requires review.reviewed_on (YYYY-MM-DD)")

    sources = {}
    for i, s in enumerate(doc.get("sources") or []):
        if not isinstance(s, dict) or not s.get("id") or s["id"] in sources:
            errors.append(f"sources[{i}]: missing or duplicate id")
            continue
        sources[s["id"]] = s
        tag = f"source {s['id']}"
        if s.get("type") not in ALL_SOURCE_TYPES:
            errors.append(f"{tag}: type must be one of {sorted(ALL_SOURCE_TYPES)}")
        if not str(s.get("url", "")).startswith(("http://", "https://")):
            errors.append(f"{tag}: url must start with http:// or https://")
        if not (s.get("title") or "").strip() or not (s.get("publisher") or "").strip():
            errors.append(f"{tag}: title and publisher are required")
        if not _is_date(s.get("accessed")):
            errors.append(f"{tag}: accessed must be YYYY-MM-DD")

    man = doc.get("manifesto") or {}
    if man.get("found"):
        mids = man.get("source_ids") or []
        if not mids:
            errors.append("manifesto.found is true but manifesto.source_ids is empty")
        elif _check_ids(errors, "manifesto", mids, sources) and not any(
                sources[i].get("type") == "primary_document" for i in mids):
            errors.append("manifesto: needs a primary_document source (the manifesto itself)")

    for i, o in enumerate(doc.get("offices_held") or []):
        label = f"offices_held[{i}]"
        if not isinstance(o, dict) or not (o.get("role") or "").strip():
            errors.append(f"{label}: role required")
            continue
        oids = o.get("source_ids") or []
        if not oids:
            errors.append(f"{label}: source_ids required")
        elif _check_ids(errors, label, oids, sources) and not _grade_sources(oids, sources):
            errors.append(f"{label}: needs a scoring-grade source")

    domains = doc.get("domains") or {}
    for k in domains:
        if k not in DOMAIN_KEYS:
            errors.append(f"unknown domain '{k}'")
    for k in DOMAIN_KEYS:
        d = domains.get(k) or {}
        _check_component(errors, f"{k}.plan", d.get("plan") or {}, "plan", doc, sources)
        _check_component(errors, f"{k}.record", d.get("record") or {}, "record", doc, sources)

    for i, item in enumerate(doc.get("integrity_items") or []):
        label = f"integrity_items[{i}]"
        if item.get("status") not in INTEGRITY_STATUSES:
            errors.append(f"{label}: status must be one of {sorted(INTEGRITY_STATUSES)}")
        ids = item.get("source_ids") or []
        if not ids:
            errors.append(f"{label}: source_ids required")
        elif _check_ids(errors, label, ids, sources) and item.get("status") != "allegation" \
                and not any(sources[i].get("type") == "official_record" for i in ids):
            errors.append(f"{label}: anything beyond an allegation needs an official_record source (court or agency record)")
    return errors


# ---------------------------------------------------------------- scoring
def score_evidence(doc):
    """Compute the rating from a validated evidence document. Invalid files are never scored."""
    errors = validate_evidence(doc)
    if errors:
        return {"status": "invalid", "methodology_version": METHODOLOGY_VERSION, "errors": errors}

    status = doc["review"]["status"]
    sources = {s["id"]: s for s in doc.get("sources", [])}
    out = {
        "methodology_version": METHODOLOGY_VERSION,
        "status": status,
        "reviewed_on": doc["review"].get("reviewed_on") or None,
        "domains": DOMAIN_KEYS,
    }
    for kind in ("plan", "record"):
        scores, confs = [], []
        for k in DOMAIN_KEYS:
            comp = ((doc.get("domains") or {}).get(k) or {}).get(kind) or {}
            lvl = comp.get("level")
            if lvl is None:
                scores.append(None)  # N/A: missing evidence is never a zero
                continue
            scores.append(round(lvl / 5 * 100))
            publishers = {(sources[i].get("publisher") or i).strip().lower()
                          for i in comp["source_ids"] if sources[i]["type"] in SCORING_TYPES}
            confs.append("high" if len(publishers) >= 2 else "medium")
        assessed = sum(s is not None for s in scores)
        coverage = round(assessed / len(DOMAIN_KEYS), 2)
        applicable = True if kind == "plan" else bool(doc.get("offices_held"))
        out[kind] = {
            "scores": scores,
            "coverage": coverage,
            "confidence": None if not confs else ("high" if all(c == "high" for c in confs) else "medium"),
            "applicable": applicable,
            "publishable": status == "verified" and applicable and coverage >= MIN_COVERAGE,
        }
    # Integrity items are displayed with their legal status, never scored in this version.
    out["integrity_items"] = doc.get("integrity_items", []) if status == "verified" else []
    return out


def load_rating(state, cid, root="data/evidence"):
    path = evidence_path(root, state, cid)
    if not os.path.exists(path):
        return {"status": "not_rated"}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return {"status": "invalid", "errors": [f"cannot read file: {e}"]}
    return score_evidence(doc)


def public_rating(rating):
    """What may appear on the website: verified ratings only. Drafts and invalid files stay private."""
    if rating.get("status") == "verified":
        return rating
    return {"status": "not_rated", "methodology_version": METHODOLOGY_VERSION}


# ---------------------------------------------------------------- CLI
def _governor_candidates(states_dir):
    for path in sorted(glob.glob(os.path.join(states_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for c in data.get("offices", {}).get("Governor", {}).get("candidates", []):
            yield data["state"], c


def cmd_init(args):
    created = skipped = 0
    for state, c in _governor_candidates(args.states):
        cid = c.get("id") or candidate_id(state, "Governor", c["n"])
        path = evidence_path(args.evidence, state, cid)
        if os.path.exists(path):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_evidence_stub(cid, state, c["p"], c["n"]), f, indent=2)
        created += 1
    print(f"created {created} stubs, left {skipped} existing files untouched")


def _evidence_files(root):
    return sorted(glob.glob(os.path.join(root, "*", "*.json")))


def cmd_validate(args):
    bad = 0
    files = _evidence_files(args.evidence)
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                errs = validate_evidence(json.load(f))
        except ValueError as e:
            errs = [f"invalid JSON: {e}"]
        if errs:
            bad += 1
            print(f"[x] {path}")
            for e in errs:
                print(f"      - {e}")
    print(f"{len(files)} files checked, {bad} with errors")
    return 1 if bad else 0


def cmd_report(args):
    print(f"{'candidate':58} {'status':10} {'plan cov':8} {'record cov':10} publishable")
    for path in _evidence_files(args.evidence):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        r = score_evidence(doc)
        if r["status"] == "invalid":
            print(f"{doc.get('candidate_id', path)[:58]:58} INVALID ({len(r['errors'])} errors)")
            continue
        pub = [k for k in ("plan", "record") if r[k]["publishable"]]
        print(f"{doc['candidate_id'][:58]:58} {r['status']:10} {r['plan']['coverage']:<8} "
              f"{r['record']['coverage']:<10} {','.join(pub) or '-'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--states", default="data/states")
    p.add_argument("--evidence", default="data/evidence")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("report").set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
