"""Compare the output with the expected result agreed in docs/questions.md (the approved assumptions Q1–Q13).

Usage:  python -m tools.run_batch && python -m tools.check_expected
Exits with code 1 if anything differs.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "output" / "purchase_invoice_lines.csv"

# email: (action, document_type, approver_role, number of lines, words that must appear in the reason)
EXPECTED = {
    "E01": ("post", "invoice", "CMO", 2, []),
    "E02": ("post", "invoice", "Head of Operations", 4, ["NW-PO-2026-064"]),
    "E03": ("post", "invoice", "Auto: PO match (rule 2)", 2, ["Approved automatically"]),
    "E04": ("hold", "invoice", "Head of Operations", 1, ["44.80", "41.50", "396.00", "22 days later",
                                                         "Due 2026-11-08 (in 8 days)"]),
    "E05": ("reject", "reminder", "", 1, ["Duplicate", "E03"]),
    "E06": ("post", "invoice", "Auto: PO match (rule 2)", 1, ["Approved automatically"]),
    "E07": ("hold", "credit_note", "Head of Operations", 1, ["214/2026", "E06"]),
    "E08": ("hold", "invoice", "CMO + CEO", 2, ["German VAT", "deposit", "CEO"]),
    "E09": ("reject", "receipt", "", 1, ["expense tool", "4417", "NONEU-SERV"]),
    "E10": ("hold", "invoice", "Head of Operations + CEO", 2, ["bank account", "meza-darbnica.co", "7 days",
                                                               "Due 2026-11-12 (in 7 days)"]),
    "E11": ("hold", "invoice", "CEO", 2, ["New supplier", "4050", "ADMIN", "Due 2026-11-12 (in 8 days)"]),
    "E12": ("reject", "receipt", "", 1, ["expense tool", "private"]),
    "E13": ("reject", "statement", "", 1, ["EF-55102", "EF-55140", "E14"]),
    "E14": ("post", "invoice", "Head of Operations", 2, []),
    "E15": ("post", "invoice", "Head of Product Development", 2, []),
    "E16": ("hold", "invoice", "CMO", 1, ["New Works ApS", "rule 6", "bank account match"]),
}

# Exact coding checks on a few lines that are easy to get wrong.
LINES = {
    ("E02", "3"): {"gl_account": "6820", "vat_code": "IMPORT-VAT", "amount_excl_vat": "0.00", "vat_amount": "9870.00"},
    ("E02", "4"): {"gl_account": "2050", "vat_code": "DK25", "vat_amount": "362.50"},
    ("E02", "2"): {"gl_account": "2060", "vat_code": "NOVAT"},
    ("E08", "1"): {"vat_code": "EU-SERV", "vat_amount": "0.00", "confidence": "low"},
    ("E15", "1"): {"vat_code": "EU-SERV", "gl_account": "3510", "cost_centre": "PD"},
    ("E04", "1"): {"vat_code": "IMPORT", "po_number": "NW-PO-2026-064"},
    ("E16", "1"): {"confidence": "medium", "gl_account": "3030"},
}


def main() -> int:
    rows = list(csv.DictReader(OUTPUT.open(encoding="utf-8-sig")))
    problems = []
    for email, (action, doc_type, approver, n_lines, words) in EXPECTED.items():
        lines = [r for r in rows if r["email_id"] == email]
        if not lines:
            problems.append(f"{email}: missing from output")
            continue
        first = lines[0]
        for field, want in (("action", action), ("document_type", doc_type), ("approver_role", approver)):
            if first[field] != want:
                problems.append(f"{email}: {field} is '{first[field]}', expected '{want}'")
        if len(lines) != n_lines:
            problems.append(f"{email}: {len(lines)} lines, expected {n_lines}")
        for w in words:
            if w.lower() not in first["flag_reason"].lower():
                problems.append(f"{email}: reason doesn't mention '{w}'")
        if action != "post" and first["flag"] != "yes":
            problems.append(f"{email}: flag should be 'yes'")
    for (email, line_no), fields in LINES.items():
        row = next((r for r in rows if r["email_id"] == email and r["line_no"] == line_no), None)
        for field, want in fields.items():
            if not row or row[field] != want:
                problems.append(f"{email} line {line_no}: {field} is '{row and row[field]}', expected '{want}'")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if problems:
        print("DIFFERENCES FROM THE EXPECTED RESULT:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    print(f"All {len(EXPECTED)} emails match the expected result (actions, approvers, reasons, key coding).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
