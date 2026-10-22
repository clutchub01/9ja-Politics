import os
import json
import re
import pandas as pd
import pdfplumber

# 1. Define Party Manifesto Baseline Scores (0-15 scale) [Econ, Sec, Edu, Infra, Exp, Integ]
PARTY_BASELINES = {
    "APC": [12, 10, 10, 13, 10, 8],
    "ADC": [11, 10, 11, 11, 10, 9],
    "NDC": [13, 11, 13, 11, 9, 12],
    "LP":  [11, 11, 12, 10, 8, 12],
    "PDP": [11, 10, 10, 11, 10, 8],
    "AAC": [10, 14, 12, 9,  6, 14],
    "SDP": [11, 12, 11, 10, 7, 12],
}
DEFAULT_PARTY = [7, 7, 7, 7, 5, 8]

def clean_text(text):
    if not text:
        return "Not confirmed"
    return " ".join(str(text).split()).strip()

def compute_radar_scores(qual, bio, party):
    """Computes the 6-axis radar score (0-100) using INEC qualifications and bio data."""
    q_upper = str(qual).upper()
    b_upper = str(bio).upper()
    p_upper = str(party).strip().upper()
    
    p_base = PARTY_BASELINES.get(p_upper, DEFAULT_PARTY)
    scores = [40, 40, 40, 40, 40, 50]  # Base floor

    # Academic & Professional degree boosts
    has_degree = any(k in q_upper for k in ["B.SC", "B.A", "B.ENG", "LL.B", "MBBS", "HND", "DEGREE", "BSC"])
    has_postgrad = any(k in q_upper for k in ["M.SC", "MBA", "PHD", "PH.D", "MASTER", "DOCTOR", "PGD", "MSC"])

    if has_degree:
        scores = [s + 10 for s in scores]
    if has_postgrad:
        scores[0] += 5; scores[2] += 8; scores[4] += 5

    # Domain specific expertise checks
    if any(k in q_upper or k in b_upper for k in ["ECON", "FINANC", "BUS", "ACCOUNT", "BANK", "ENTREPREN"]):
        scores[0] += 12
    if any(k in q_upper or k in b_upper for k in ["LAW", "LL.B", "BL", "BARRISTER", "SAN", "ACTIVIST", "MILITARY", "SECURITY"]):
        scores[1] += 12
    if any(k in q_upper or k in b_upper for k in ["MED", "MBBS", "HEALTH", "EDU", "PROF", "LECTURER", "TEACH"]):
        scores[2] += 12
    if any(k in q_upper or k in b_upper for k in ["ENG", "ARCH", "TECH", "WORKS", "POWER", "INFRA"]):
        scores[3] += 12

    # Experience level boosts
    if any(k in b_upper for k in ["PRESIDENT", "GOVERNOR", "VICE PRESIDENT"]):
        scores[4] += 28; scores[0] += 8; scores[3] += 8
    elif any(k in b_upper for k in ["SENATOR", "MINISTER", "SPEAKER", "DEPUTY GOVERNOR"]):
        scores[4] += 20; scores[1] += 6
    elif any(k in b_upper for k in ["REP", "HONOURABLE", "COMMISSIONER", "CHAIRMAN", "MEMBER"]):
        scores[4] += 14

    # Combine with party baseline & clamp between 35 and 95
    final_scores = [
        min(95, max(35, scores[i] + p_base[i]))
        for i in range(6)
    ]
    return final_scores

def extract_inec_pdf(pdf_path, output_json_dir):
    """Extracts tables from INEC candidate list PDFs and outputs structured state JSONs."""
    os.makedirs(output_json_dir, exist_ok=True)
    state_data = {}

    print(f"Reading PDF: {pdf_path}...")
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    # Skip header rows
                    if not row or len(row) < 5:
                        continue
                    if "STATE" in str(row).upper() and "CANDIDATE" in str(row).upper():
                        continue
                    
                    # Map standard INEC PDF column layouts:
                    # [S/N, State, Constituency, Candidate Name, Party, Age, Gender, Qualifications, etc.]
                    try:
                        state = clean_text(row[1]) if len(row) > 1 else "Unknown"
                        constituency = clean_text(row[2]) if len(row) > 2 else "General"
                        name = clean_text(row[3]) if len(row) > 3 else ""
                        party = clean_text(row[4]) if len(row) > 4 else "IND"
                        age = clean_text(row[5]) if len(row) > 5 else "Not confirmed"
                        gender = clean_text(row[6]) if len(row) > 6 else "M"
                        qual = clean_text(row[7]) if len(row) > 7 else "Not confirmed"
                        
                        if not name or len(name) < 3:
                            continue

                        if state not in state_data:
                            state_data[state] = {
                                "state": state,
                                "offices": {
                                    "Senate": {"constituency": "Senatorial Districts", "candidates": []},
                                    "House of Reps": {"constituency": "Federal Constituencies", "candidates": []}
                                }
                            }

                        # Calculate automated scores
                        scores = compute_radar_scores(qual, constituency, party)

                        candidate_obj = {
                            "n": name,
                            "p": party,
                            "age": age,
                            "gender": gender,
                            "rm": "Not confirmed",
                            "edu": qual,
                            "bg": f"Contesting under {party} for {constituency}.",
                            "scores": scores
                        }

                        # Append to appropriate office slot
                        state_data[state]["offices"]["Senate"]["candidates"].append(candidate_obj)

                    except Exception as e:
                        continue

    # Write separate JSON files per state for fast mobile loading
    for state_name, payload in state_data.items():
        safe_filename = re.sub(r'[^a-zA-Z0-9]', '_', state_name.lower()) + ".json"
        out_path = os.path.join(output_json_dir, safe_filename)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Exported: {out_path}")

# Example execution (Point to your downloaded INEC PDF):
# extract_inec_pdf("2027_SENATE_FINAL_LIST_OF_CANDIDATES.pdf", "./data/states")