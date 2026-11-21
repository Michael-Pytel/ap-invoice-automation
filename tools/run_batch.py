"""Run all emails in the invoice mailbox through the rules and write the ERP-format CSV.

Usage:
  python -m tools.run_batch               offline: rules read the CSVs directly (no services needed)
  python -m tools.run_batch --services    the same path as the n8n flow: each email goes to the rules service, and the
                                    result is logged in the simulated Business Central (which is reset first).
                                    Start both services first:
                                      python -m bc_sim.server
                                      python -m invoice_engine.service
Output: output/purchase_invoice_lines.csv  (the shape of target_format_purchase_invoice_lines.csv)
"""
from __future__ import annotations

import argparse
import base64
import csv
import sys

from invoice_engine.httpjson import HttpError, call
from invoice_engine.reference import CASE, ROOT, ReferenceData, Register, read_csv
from invoice_engine.rules import Email, process_email

OUTPUT = ROOT / "output" / "purchase_invoice_lines.csv"


def load_emails() -> list[Email]:
    emails = []
    for r in read_csv(CASE / "invoice_emails.csv"):
        attachments = [CASE / "attachments" / a.strip() for a in r["attachments"].split(";") if a.strip()]
        emails.append(Email(r["email_id"], r["received_at"], r["from"], r["to"], r["subject"], r["body"], attachments))
    return sorted(emails, key=lambda e: e.received_at)   # in the order they arrived (Q6)


def email_json(email: Email) -> dict:
    return {"email_id": email.email_id, "received_at": email.received_at, "from": email.sender, "to": email.to,
            "subject": email.subject, "body": email.body,
            "attachments": [{"filename": p.name, "content_base64": base64.b64encode(p.read_bytes()).decode()}
                            for p in email.attachments]}


def run_offline() -> tuple[list[dict], dict]:
    ref, register = ReferenceData(), Register()
    rows = []
    for email in load_emails():
        rows += process_email(email, ref, register)
    return rows, {}


def run_services(rules_url: str, bc_url: str) -> tuple[list[dict], dict]:
    call("POST", f"{bc_url}/reset")
    rows, bc_status = [], {}
    for email in load_emails():                          # one at a time, in arrival order
        result = call("POST", f"{rules_url}/process", email_json(email))
        logged = call("POST", f"{bc_url}/incoming-documents", result)["logged"]
        rows += result["rows"]
        bc_status[email.email_id] = ", ".join(f"{l['purchase_invoice_no'] or l['incoming_document_no']} "
                                              f"{l['status']}" for l in logged)
    return rows, bc_status


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--services", action="store_true", help="go through the rules service and simulated BC")
    p.add_argument("--rules", default="http://127.0.0.1:8101")
    p.add_argument("--bc", default="http://127.0.0.1:8100")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        rows, bc_status = run_services(args.rules, args.bc) if args.services else run_offline()
    except (HttpError, OSError) as e:
        sys.exit(f"Could not reach the services ({e}). Are both running? Or run without --services.")
    rows.sort(key=lambda r: (r["email_id"], int(r["line_no"])))

    columns = ReferenceData().target_columns
    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    # Readable summary: one line per email.
    seen = set()
    print(f"{'email':6} {'action':7} {'type':12} {'supplier':32} {'approver':38} {'in BC' if bc_status else ''}")
    for r in rows:
        if r["email_id"] in seen:
            continue
        seen.add(r["email_id"])
        print(f"{r['email_id']:6} {r['action']:7} {r['document_type']:12} {r['vendor_name'][:31]:32} "
              f"{r['approver_role'][:37]:38} {bc_status.get(r['email_id'], '')}")
    counts = {a: len({r['email_id'] for r in rows if r['action'] == a}) for a in ("post", "hold", "reject")}
    print(f"\n{len(seen)} emails: {counts['post']} post, {counts['hold']} hold, {counts['reject']} reject")
    print(f"Written: {OUTPUT}")


if __name__ == "__main__":
    main()
