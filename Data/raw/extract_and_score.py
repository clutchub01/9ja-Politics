import os
import glob
import re
import json
import sys
import pdfplumber

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

# ==============================================================================
# NEW 6-DIMENSION HEURISTIC ENGINE
# Returns: [Education_Level, Advanced_Study, Youth_Energy, Completeness, Integrity, Expertise]
# ==============================================================================
def compute_candidate_radar(age, qualifications, party, office, gender):
    q_up = qualifications.upper() if qualifications else ""

    # 1. Education Level (Base: 40)
    edu_level = 40
    if re.search(r'\b(FSLC|FIRST LEAVING|PRIMARY)\b', q_up): edu_level += 10
    if re.search(r'\b(WAEC|SSCE|NECO|GCE|WASC)\b', q_up): edu_level += 20
    if re.search(r'\b(B\.?SC|B\.?A|B\.?ENG|HND|OND|LL\.?B|DEGREE|TERTIARY)\b', q_up): edu_level += 30
    edu_level = min(edu_level, 95) 

    # 2. Advanced Study (Base: 40)
    adv_study = 40
    if re.search(r'\b(PGD)\b', q_up): adv_study += 15
    if re.search(r'\b(M\.?SC|M\.?A|MBA|MASTERS|LL\.?M)\b', q_up): adv_study += 30
    if re.search(r'\b(PH\.?D|DOCTORATE|PROF|DOCTOR)\b', q_up): adv_study += 50
    adv_study = min(adv_study, 95)

    # 3. Youth & Energy (Base: 30)
    youth_energy = 30
    if age is not None:
        if age < 40: youth_energy += 65
        elif 40 <= age < 50: youth_energy += 50
        elif 50 <= age < 65: youth_energy += 30
        else: youth_energy += 10

    # 4. Declared Completeness (Base: 40)
    # Checks if the candidate submitted all major INEC fields
    completeness = 40
    if age: completeness += 15
    if gender and gender in ["M", "F"]: completeness += 15
    if party: completeness += 15
    if qualifications and qualifications.strip() and q_up != "NOT CONFIRMED": completeness += 15
    completeness = min(completeness, 100)

    # 5. Public Integrity (Base: 50)
    # Rewards chronological transparency, heavily penalizes Affidavits/Loss of forms
    integrity = 50
    if not q_up.strip() or q_up == "NOT CONFIRMED":
        integrity = 30
    else:
        if re.search(r'\b(FSLC|FIRST LEAVING|PRIMARY)\b', q_up): integrity += 15
        if re.search(r'\b(WAEC|SSCE|NECO|GCE|WASC)\b', q_up): integrity += 15
        if re.search(r'\b(B\.?SC|B\.?A|B\.?ENG|HND|OND|LL\.?B|M\.?SC|M\.?A|PH\.?D)\b', q_up): integrity += 15
        if re.search(r'\b(AFFIDAVIT|LOSS|MISPLACED|SWORN)\b', q_up): integrity -= 30
    integrity = min(max(integrity, 10), 100)

    # 6. Domain Expertise (Base: 40)
    # Rewards specific governance, legal, economic, health, and tech degrees
    expertise = 40
    if q_up.strip() and q_up != "NOT CONFIRMED":
        if re.search(r'\b(LL\.?B|B\.?L|LAW|PUBLIC ADMIN|POLITICAL|HISTORY|LL\.?M)\b', q_up): expertise += 25
        if re.search(r'\b(ECON|MBA|ACCOUNTING|FINANCE|BANKING|COMMERCE|BUSINESS)\b', q_up): expertise += 25
        if re.search(r'\b(MED|MBBS|NURSING|HEALTH|EDU|NCE|TEACHER|B\.?ED)\b', q_up): expertise += 25
        if re.search(r'\b(ENG|TECH|ARCHITECTURE|URBAN|COMPUTER|B\.?ENG|B\.?TECH|ESTATE)\b', q_up): expertise += 25
    expertise = min(expertise, 95)

    return [edu_level, adv_study, youth_energy, completeness, integrity, expertise]


# ---------------------------------------------------------------------------
# GOVERNORSHIP PDF PARSER (grid-line based)
# ---------------------------------------------------------------------------
GOV_COLS = ["sn", "state", "position", "name", "party", "pwd", "age", "gender", "qual", "remarks"]

def _gov_clean_page(page):
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
                    continue  

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
                
                gov["age"] = int(ages[0]) if len(ages) > 0 else None
                dep["age"] = int(ages[1]) if len(ages) > 1 else None
                gov["gender"] = gens[0] if len(gens) > 0 else None
                dep["gender"] = gens[1] if len(gens) > 1 else None

                if not gov["name"]: warnings.append("missing governor name")
                if not rec["party"]: warnings.append("missing party")

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
    print("9JA POLITICS - DATA INGESTION & HEURISTIC ENGINE (6-DIMENSION)")
    print("="*70)
    print(f"• Senate PDF        : {'Found (' + os.path.basename(senate_pdf) + ')' if senate_pdf else 'Not Found'}")
    print(f"• Reps PDF          : {'Found (' + os.path.basename(reps_pdf) + ')' if reps_pdf else 'Not Found'}")
    print(f"• State Assembly PDF: {'Found (' + os.path.basename(assembly_pdf) + ')' if assembly_pdf else 'Not Found'}")
    print(f"• Governorship PDFs : {len(gov_pdfs)} files found")
    print("="*70 + "\n")

    # 1. PARSE LEGISLATIVE OFFICES
    leg_jobs = [
        (senate_pdf, "Senate"),
        (reps_pdf, "House of Reps"),
        (assembly_pdf, "State Assembly")
    ]

    for pdf_path, office_key in leg_jobs:
        if not pdf_path or not os.path.exists(pdf_path):
            continue

        file_name = os.path.basename(pdf_path)
        print(f"[+] Ingesting {office_key} ({file_name})...")
        
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            current_detected_state = "Abia"
            candidates_extracted = 0

            for p_idx, page in enumerate(pdf.pages, start=1):
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

                        # Generate the new 6-axis scores
                        scores = compute_candidate_radar(age, qual, party, office_key, gender)
                        
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

            render_progress_bar(total_pages, total_pages, prefix=f"  {office_key:<14}", suffix=f"Total: {candidates_extracted} candidates")

    # 2. PARSE GOVERNORSHIP PDFs
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
                continue

            rows = parse_governorship_pdf(gov_pdf)
            for r in rows:
                g, d, party = r["gov"], r["dep"], r["party"] or "IND"
                if not g["name"]:
                    continue

                dep_label = d["name"] or "Not confirmed"
                if d["name"] and d["age"]:
                    dep_label = f"{d['name']} ({d['age']} yrs)"

                # Generate the new 6-axis scores for Governor
                scores = compute_candidate_radar(g["age"], g["qual"], party, "Governor", g.get("gender", "M"))
                
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

    # 3. EXPORT JSON FILES
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