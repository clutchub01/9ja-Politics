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
        total_gov_files = len(gov_pdfs)
        gov_total_cands = 0

        for f_idx, gov_pdf in enumerate(gov_pdfs, start=1):
            pdf_state = normalize_state_name(gov_pdf)
            active_state = pdf_state or "Abia"
            state_obj = all_states.get(active_state)
            
            render_progress_bar(f_idx, total_gov_files, prefix="  Governorship  ", suffix=f"{active_state:<12} (Total: {gov_total_cands})")
            
            if not state_obj:
                continue

            with pdfplumber.open(gov_pdf) as pdf:
                pending_gov = None
                
                for page in pdf.pages:
                    # Strategy 1: Table extraction
                    tables = page.extract_tables({
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text"
                    })
                    
                    rows_found = False
                    if tables:
                        for table in tables:
                            for row in table:
                                if not row or len(row) < 4:
                                    continue
                                row_str = " ".join([clean_str(c) for c in row if c]).upper()
                                if "NAME OF CANDIDATE" in row_str or "POSITION" in row_str:
                                    continue

                                pos_match = re.search(r'(DEPUTY[\s\-]GOVERNOR|GOVERNOR)', row_str, re.IGNORECASE)
                                if not pos_match:
                                    continue
                                
                                rows_found = True
                                pos_text = pos_match.group(1).upper()
                                
                                party = "IND"
                                for p in PARTY_BASELINES.keys():
                                    if re.search(r'\b' + p + r'\b', row_str):
                                        party = p
                                        break
                                
                                age = parse_age(row_str)
                                gender_match = re.search(r'\b([MF])\b', row_str)
                                gender = gender_match.group(1) if gender_match else "M"

                                cleaned_cells = [clean_str(c) for c in row if c and len(clean_str(c)) > 3]
                                name = ""
                                for c in cleaned_cells:
                                    c_up = c.upper()
                                    if not any(k in c_up for k in ["GOVERNOR", "INDEPENDENT", "COMMISSION", "STATE", "CERTIFICATE", "WAEC", "SSCE"]):
                                        name = c
                                        break
                                
                                if not name or len(name) < 3:
                                    continue

                                qual = row_str

                                if "DEPUTY" in pos_text:
                                    if pending_gov is not None:
                                        pending_gov["rm"] = f"{name} ({age} yrs)" if age else name
                                        pending_gov = None
                                else:
                                    scores = compute_age_weighted_radar(age, qual, party, "Governor")
                                    pending_gov = {
                                        "n": name,
                                        "p": party,
                                        "age": str(age) if age else "Not confirmed",
                                        "gender": gender,
                                        "rm": "Not confirmed",
                                        "edu": "Tertiary / Secondary",
                                        "bg": f"Governorship candidate for {active_state} under {party}.",
                                        "scores": scores
                                    }
                                    state_obj["offices"]["Governor"]["candidates"].append(pending_gov)
                                    gov_total_cands += 1

                    # Strategy 2: Text stream fallback
                    if not rows_found:
                        text = page.extract_text() or ""
                        lines = [clean_str(l) for l in text.split("\n") if clean_str(l)]
                        
                        for line in lines:
                            line_up = line.upper()
                            if "NAME OF CANDIDATE" in line_up or "PLOT 436" in line_up or "SECRETARY" in line_up:
                                continue
                            
                            pos_match = re.search(r'(DEPUTY[\s\-]GOVERNOR|GOVERNOR)', line_up)
                            if not pos_match:
                                continue

                            pos_text = pos_match.group(1)
                            
                            party = "IND"
                            for p in PARTY_BASELINES.keys():
                                if re.search(r'\b' + p + r'\b', line_up):
                                    party = p
                                    break

                            age = parse_age(line_up)
                            gender_match = re.search(r'\b([MF])\b', line_up)
                            gender = gender_match.group(1) if gender_match else "M"

                            name_match = re.search(r'(?:GOVERNORSHIP|GOVERNOR)\s+([A-Z\s]{4,35})\s+(?:' + '|'.join(PARTY_BASELINES.keys()) + r')\b', line_up)
                            name = name_match.group(1).strip() if name_match else "Candidate Name"

                            if "DEPUTY" in pos_text:
                                if pending_gov is not None:
                                    pending_gov["rm"] = f"{name} ({age} yrs)" if age else name
                                    pending_gov = None
                            else:
                                scores = compute_age_weighted_radar(age, line_up, party, "Governor")
                                pending_gov = {
                                    "n": name,
                                    "p": party,
                                    "age": str(age) if age else "Not confirmed",
                                    "gender": gender,
                                    "rm": "Not confirmed",
                                    "edu": "Tertiary / Secondary",
                                    "bg": f"Governorship candidate for {active_state} under {party}.",
                                    "scores": scores
                                }
                                state_obj["offices"]["Governor"]["candidates"].append(pending_gov)
                                gov_total_cands += 1

        render_progress_bar(total_gov_files, total_gov_files, prefix="  Governorship  ", suffix=f"Done! ({gov_total_cands} total candidates)")

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