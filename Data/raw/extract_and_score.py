import os
import glob
import re
import json
import sys
import time
import pdfplumber

# 1. Party platform baseline vectors [Economy, Security, Education, Infrastructure, Experience, Youth & Energy]
PARTY_BASELINES = {
    "APC":  [12, 11, 10, 13, 12, 6],
    "PDP":  [11, 10, 11, 11, 11, 7],
    "LP":   [12, 11, 13, 10, 8, 14],
    "ADC":  [11, 10, 11, 11, 9, 10],
    "NNPP": [11, 12, 11, 10, 9, 9],
    "AAC":  [10, 14, 12, 9, 6, 15],
    "SDP":  [11, 11, 11, 10, 8, 9],
    "APGA": [11, 10, 10, 11, 9, 8],
    "YPP":  [10, 9, 10, 9, 6, 14],
    "PRP":  [10, 10, 12, 8, 8, 11],
    "ZLP":  [9, 9, 9, 9, 6, 10],
    "A":    [8, 8, 8, 8, 5, 10],
    "AA":   [8, 8, 8, 8, 5, 10],
    "ADP":  [8, 8, 8, 8, 6, 9],
    "APP":  [8, 8, 8, 8, 5, 10],
    "APM":  [8, 8, 8, 8, 5, 10],
    "BP":   [8, 8, 8, 8, 5, 11],
    "NRM":  [8, 8, 8, 8, 5, 10],
    "NDP":  [8, 8, 8, 8, 5, 10],
    "DLA":  [8, 8, 8, 8, 5, 10]
}
DEFAULT_BASELINE = [7, 7, 7, 7, 6, 9]

VALID_STATES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno", 
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "FCT", "Gombe", "Imo", 
    "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", 
    "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", "Taraba", 
    "Yobe", "Zamfara"
]

def render_progress_bar(current, total, prefix="Processing", suffix="Complete", length=35):
    """Renders a dynamic command line progress bar with percentage."""
    if total == 0:
        total = 1
    percent = ("{0:.1f}").format(100 * (current / float(total)))
    filled_length = int(length * current // total)
    bar = "█" * filled_length + "-" * (length - filled_length)
    sys.stdout.write(f"\r{prefix} |{bar}| {percent}% [{current}/{total}] {suffix}")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")

def clean_str(val):
    if not val:
        return ""
    return re.sub(r'\s+', ' ', str(val)).strip()

def normalize_state_name(val):
    raw = clean_str(val).upper()
    for st in VALID_STATES:
        if re.search(r'\b' + re.escape(st.upper()) + r'\b', raw):
            return st
    return None

def parse_age(age_raw):
    if not age_raw:
        return None
    matches = re.findall(r'\b(1[89]|[2-9][0-9])\b', str(age_raw))
    if matches:
        return int(matches[-1])
    return None

def compute_age_weighted_radar(age, qualifications, party, office):
    q_up = qualifications.upper()
    p_up = party.upper()
    baseline = PARTY_BASELINES.get(p_up, DEFAULT_BASELINE)

    econ, sec, edu, infra, exp, energy = 40, 40, 40, 40, 35, 35

    if age is not None:
        if age < 40:
            energy += 25; exp += 5; econ += 4
        elif 40 <= age < 50:
            energy += 18; exp += 14; econ += 8
        elif 50 <= age < 65:
            energy += 10; exp += 24; sec += 6
        else:
            energy += 4; exp += 30; sec += 8
    else:
        energy += 10; exp += 12

    has_degree = any(k in q_up for k in ["B.SC", "BSC", "BA", "B.A", "B.ENG", "LLB", "LL.B", "MBBS", "HND", "DEGREE"])
    has_postgrad = any(k in q_up for k in ["M.SC", "MSC", "MBA", "PHD", "PH.D", "MASTERS", "DOCTOR", "LLM"])
    
    if has_degree:
        econ += 6; edu += 8; exp += 4
    if has_postgrad:
        econ += 6; edu += 12; exp += 6

    if any(k in q_up for k in ["ECONOMICS", "ACCOUNT", "BANK", "FINANCE", "MANAGEMENT", "COMMERCE"]):
        econ += 14
    if any(k in q_up for k in ["LAW", "LL.B", "LLB", "BL", "BARRISTER", "LEGAL", "JUSTICE"]):
        sec += 14
    if any(k in q_up for k in ["ENG", "ENGINEERING", "TECH", "URBAN", "ESTATE", "ARCHITECTURE"]):
        infra += 14
    if any(k in q_up for k in ["MED", "SURGERY", "HEALTH", "EDU", "TEACHER", "PHD", "PROF"]):
        edu += 12

    raw_scores = [
        econ + baseline[0],
        sec + baseline[1],
        edu + baseline[2],
        infra + baseline[3],
        exp + baseline[4],
        energy + baseline[5]
    ]
    return [max(35, min(95, s)) for s in raw_scores]


# ---------------------------------------------------------------------------
# GOVERNORSHIP PDF PARSER (grid-line based)
# Each INEC row holds a governor AND a deputy stacked in one table row. We use the
# table's own vertical rules for the columns, the full-width horizontal rules for rows,
# and the short per-column rules to split governor from deputy.
# ---------------------------------------------------------------------------
GOV_COLS = ["sn", "state", "position", "name", "party", "pwd", "age", "gender", "qual", "remarks"]

def _gov_clean_page(page):
    """Drop the giant green INEC watermark letters that get glued onto real words."""
    return page.filter(lambda o: o.get("object_type") != "char" or o.get("size", 10) < 20)

def _gov_column_edges(page):
    hdr = [w for w in page.extract_words() if w["text"] == "SN"]
    if not hdr:
        return None
    top = hdr[0]["top"]
    xs = sorted({round(l["x0"]) for l in page.lines
                 if abs(l["x0"] - l["x1"]) < 1 and l["top"] <= top + 2 and l["bottom"] >= top + 8})
    merged = []
    for x in xs:
        if not merged or x - merged[-1] > 3:
            merged.append(x)
    return merged if len(merged) == len(GOV_COLS) + 1 else None

def _gov_row_bounds(page, left):
    ys = sorted({round(l["top"]) for l in page.lines
                 if abs(l["top"] - l["bottom"]) < 1 and l["x0"] <= left + 2
                 and left + 15 <= l["x1"] <= left + 40})
    merged = []
    for y in ys:
        if not merged or y - merged[-1] > 3:
            merged.append(y)
    return merged

def _gov_text(words):
    words = sorted(words, key=lambda w: (round(w["top"]), w["x0"]))
    return clean_str(" ".join(w["text"] for w in words))

def parse_governorship_pdf(path):
    """Returns [{'sn', 'party', 'gov': {name, qual, age, gender}, 'dep': {...}, 'warnings': [...]}]"""
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pg = _gov_clean_page(page)
            edges = _gov_column_edges(pg)
            if not edges:
                continue
            bounds = _gov_row_bounds(pg, edges[0])
            words_all = pg.extract_words()
            hlines = [l for l in pg.lines if abs(l["top"] - l["bottom"]) < 1]

            for top, bottom in zip(bounds, bounds[1:]):
                if bottom - top < 15:
                    continue
                cols, splits = {}, {}
                for i, cname in enumerate(GOV_COLS):
                    x0, x1 = edges[i], edges[i + 1]
                    cols[cname] = [w for w in words_all
                                   if x0 <= (w["x0"] + w["x1"]) / 2 < x1 and top + 1 < w["top"] < bottom - 1]
                    splits[cname] = sorted(
                        round(l["top"]) for l in hlines
                        if l["x0"] >= x0 - 8 and l["x1"] <= x1 + 8 and l["x1"] - l["x0"] > 15
                        and top + 3 < l["top"] < bottom - 3)

                sn_txt = _gov_text(cols["sn"])
                if not sn_txt.isdigit():
                    continue  # header / non-data row

                warnings = []
                rec = {"sn": int(sn_txt), "party": _gov_text(cols["party"])}
                gov, dep = {}, {}

                for field in ("name", "qual"):
                    if splits[field]:
                        y = splits[field][0]
                        g = [w for w in cols[field] if w["top"] < y]
                        d = [w for w in cols[field] if w["top"] >= y]
                    else:
                        g, d = cols[field], []
                        if cols[field]:
                            warnings.append(f"no {field} divider")
                    gov[field], dep[field] = _gov_text(g), _gov_text(d)

                ages = [w["text"] for w in sorted(cols["age"], key=lambda w: w["top"]) if w["text"].isdigit()]
                gens = [w["text"] for w in sorted(cols["gender"], key=lambda w: w["top"]) if w["text"] in ("M", "F")]
                if len(ages) != 2:
                    warnings.append(f"ages={ages}")
                if len(gens) != 2:
                    warnings.append(f"genders={gens}")
                gov["age"] = int(ages[0]) if len(ages) > 0 else None
                dep["age"] = int(ages[1]) if len(ages) > 1 else None
                gov["gender"] = gens[0] if len(gens) > 0 else None
                dep["gender"] = gens[1] if len(gens) > 1 else None

                if not gov["name"]:
                    warnings.append("missing governor name")
                if not dep["name"]:
                    warnings.append("missing deputy name")
                if not rec["party"]:
                    warnings.append("missing party")

                rec.update({"gov": gov, "dep": dep, "warnings": warnings})
                out.append(rec)
    return out


def run_pipeline(output_dir="./data/states"):
    os.makedirs(output_dir, exist_ok=True)
    all_states = {}

    for st in VALID_STATES:
        all_states[st] = {
            "state": st,
            "offices": {
                "Governor": {"constituency": f"{st} Statewide", "candidates": []},
                "Senate": {"constituency": "Senatorial Districts", "candidates": []},
                "House of Reps": {"constituency": "Federal Constituencies", "candidates": []},
                "State Assembly": {"constituency": "State Constituencies", "candidates": []}
            }
        }

    def find_one(pattern):
        matches = glob.glob(pattern)
        return matches[0] if matches else None

    senate_pdf = find_one("*SENATE*FINAL*LIST*.pdf")
    reps_pdf = find_one("*REPS*FINAL*LIST*.pdf")
    assembly_pdf = find_one("*STATE*HOUSES*OF*ASSEMBLY*.pdf")
    gov_pdfs = glob.glob("*GOVERNORSHIP*.pdf")

    print("\n" + "="*70)
    print("9JA POLITICS - DATA INGESTION & HEURISTIC ENGINE")
    print("="*70)
    print(f"• Senate PDF        : {'Found (' + os.path.basename(senate_pdf) + ')' if senate_pdf else 'Not Found'}")
    print(f"• Reps PDF          : {'Found (' + os.path.basename(reps_pdf) + ')' if reps_pdf else 'Not Found'}")
    print(f"• State Assembly PDF: {'Found (' + os.path.basename(assembly_pdf) + ')' if assembly_pdf else 'Not Found'}")
    print(f"• Governorship PDFs : {len(gov_pdfs)} files found")
    print("="*70 + "\n")

    # 1. PARSE LEGISLATIVE OFFICES WITH PAGE-LEVEL PROGRESS BARS
    leg_jobs = [
        (senate_pdf, "Senate"),
        (reps_pdf, "House of Reps"),
        (assembly_pdf, "State Assembly")
    ]

    for pdf_path, office_key in leg_jobs:
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"[-] Skipping {office_key}: file not found.")
            continue

        file_name = os.path.basename(pdf_path)
        print(f"[+] Ingesting {office_key} ({file_name})...")
        
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            current_detected_state = "Abia"
            candidates_extracted = 0

            for p_idx, page in enumerate(pdf.pages, start=1):
                # Update progress bar
                render_progress_bar(p_idx, total_pages, prefix=f"  {office_key:<14}", suffix=f"Found: {candidates_extracted}")
                
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or len(row) < 5:
                            continue
                        
                        row_str = " ".join([str(c) for c in row if c]).upper()
                        if "NAME OF CANDIDATE" in row_str or "CONSTITUENCY" in row_str:
                            continue

                        st_cand = normalize_state_name(row[1]) if len(row) > 1 else None
                        if st_cand:
                            current_detected_state = st_cand

                        dist_raw = clean_str(row[2]) if len(row) > 2 else "Constituency"
                        name = clean_str(row[3]) if len(row) > 3 else ""
                        party = clean_str(row[4]) if len(row) > 4 else "IND"
                        
                        if len(row) >= 9:
                            age = parse_age(row[6]) or parse_age(row[5])
                            gender = clean_str(row[7]) or clean_str(row[6])
                            qual = clean_str(row[8])
                        else:
                            age = parse_age(row[5])
                            gender = clean_str(row[6]) if len(row) > 6 else "M"
                            qual = clean_str(row[7]) if len(row) > 7 else "Not confirmed"

                        if not name or len(name) < 3:
                            continue

                        state_obj = all_states.get(current_detected_state)
                        if not state_obj:
                            continue

                        scores = compute_age_weighted_radar(age, qual, party, office_key)
                        state_obj["offices"][office_key]["candidates"].append({
                            "n": name,
                            "p": party,
                            "district": dist_raw,
                            "age": str(age) if age else "Not confirmed",
                            "gender": gender if gender in ["M", "F"] else "M",
                            "rm": "N/A",
                            "edu": qual if qual else "Not confirmed",
                            "bg": f"Contesting for {dist_raw} under {party}.",
                            "scores": scores
                        })
                        candidates_extracted += 1

            # Finalize progress bar display
            render_progress_bar(total_pages, total_pages, prefix=f"  {office_key:<14}", suffix=f"Total: {candidates_extracted} candidates")

    # 2. PARSE GOVERNORSHIP PDFs (FILE-LEVEL PROGRESS BAR)
    if gov_pdfs:
        print("\n[+] Ingesting Governorship Candidate Files...")
        gov_pdfs = sorted(gov_pdfs)
        total_gov_files = len(gov_pdfs)
        gov_total_cands = 0
        gov_issues = []

        for f_idx, gov_pdf in enumerate(gov_pdfs, start=1):
            active_state = normalize_state_name(os.path.basename(gov_pdf).replace("_", " "))
            state_obj = all_states.get(active_state) if active_state else None

            render_progress_bar(f_idx, total_gov_files, prefix="  Governorship  ", suffix=f"{(active_state or '?'):<12} (Total: {gov_total_cands})")

            if not state_obj:
                gov_issues.append(f"{os.path.basename(gov_pdf)}: could not determine state from filename - skipped")
                continue

            rows = parse_governorship_pdf(gov_pdf)

            # Sanity checks: contiguous serial numbers, no per-row warnings
            sns = [r["sn"] for r in rows]
            if sns != list(range(1, len(sns) + 1)):
                gov_issues.append(f"{active_state}: serial numbers not contiguous: {sns}")
            if not rows:
                gov_issues.append(f"{active_state}: no candidates parsed")

            for r in rows:
                for w in r["warnings"]:
                    gov_issues.append(f"{active_state} SN {r['sn']}: {w}")

                g, d, party = r["gov"], r["dep"], r["party"] or "IND"
                if not g["name"]:
                    continue

                dep_label = d["name"] or "Not confirmed"
                if d["name"] and d["age"]:
                    dep_label = f"{d['name']} ({d['age']} yrs)"

                scores = compute_age_weighted_radar(g["age"], g["qual"], party, "Governor")
                state_obj["offices"]["Governor"]["candidates"].append({
                    "n": g["name"],
                    "p": party,
                    "district": state_obj["offices"]["Governor"]["constituency"],
                    "age": str(g["age"]) if g["age"] else "Not confirmed",
                    "gender": g["gender"] or "M",
                    "rm": dep_label,
                    "edu": g["qual"] or "Not confirmed",
                    "bg": f"Governorship candidate for {active_state} under {party}.",
                    "deputy": {
                        "n": d["name"] or "Not confirmed",
                        "age": str(d["age"]) if d["age"] else "Not confirmed",
                        "gender": d["gender"] or "Not confirmed",
                        "edu": d["qual"] or "Not confirmed",
                    },
                    "scores": scores
                })
                gov_total_cands += 1

        render_progress_bar(total_gov_files, total_gov_files, prefix="  Governorship  ", suffix=f"Done! ({gov_total_cands} total candidates)")
        if gov_issues:
            print("\n[!] Governorship parse warnings (check these against the PDFs):")
            for msg in gov_issues:
                print("    -", msg)


    # 3. EXPORT JSON FILES WITH PROGRESS BAR
    print("\n[+] Writing Partitioned State JSON Files...")
    total_states = len(all_states)
    for idx, (st_name, data) in enumerate(all_states.items(), start=1):
        slug = re.sub(r'[^a-zA-Z0-9]', '_', st_name.lower())
        out_file = os.path.join(output_dir, f"{slug}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        render_progress_bar(idx, total_states, prefix="  Saving JSONs  ", suffix=f"{slug}.json")

    print("\n" + "="*70)
    print("SUCCESS: Ingestion complete. All JSON state partitions saved to:")
    print(f"  --> {os.path.abspath(output_dir)}")
    print("="*70)

if __name__ == "__main__":
    run_pipeline()