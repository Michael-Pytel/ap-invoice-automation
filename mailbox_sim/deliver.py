"""Mailbox simulator, step 2: deliver emails from output/mailbox/ to the flow, as if they arrived in invoice@.

Usage:
  python -m mailbox_sim.deliver E10                 one email
  python -m mailbox_sim.deliver E01 E03 E06         several, in the order given
  python -m mailbox_sim.deliver --all               all 16, in the order they arrived
  python -m mailbox_sim.deliver --all --reset       empty the simulated Business Central first (start of a demo)
  python -m mailbox_sim.deliver E10 --direct        skip n8n: call the invoice check and BC directly (fallback if n8n breaks)

Emails are sent one at a time and each waits until the flow has finished, so a reminder or credit note is
always checked after the invoice it refers to has been logged.
In production this script disappears: Power Automate's "When a new email arrives" on invoice@ is the trigger.
"""
from __future__ import annotations

import argparse
import base64
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from invoice_engine.httpjson import HttpError, call
from mailbox_sim.build import MAILBOX

WEBHOOK = "http://localhost:5678/webhook/invoice-mailbox"
RULES = "http://127.0.0.1:8101"
BC = "http://127.0.0.1:8100"


def arrival(path) -> object:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes(), headersonly=True)
    return parsedate_to_datetime(msg["Date"])


def deliver_direct(payload: dict) -> dict:
    """Exactly the n8n flow's steps, without n8n."""
    result = call("POST", f"{RULES}/process-eml", payload)
    logged = call("POST", f"{BC}/incoming-documents", result)["logged"]
    return {"email_id": result["email_id"], "action": result["action"], "logged": logged,
            "flag_reason": result["rows"][0]["flag_reason"] if result["rows"] else ""}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("emails", nargs="*", help="email ids, e.g. E10")
    p.add_argument("--all", action="store_true")
    p.add_argument("--reset", action="store_true", help="empty the simulated BC first")
    p.add_argument("--direct", action="store_true", help="bypass n8n")
    p.add_argument("--webhook", default=WEBHOOK)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.all:
        files = sorted(MAILBOX.glob("*.eml"), key=arrival)
    else:
        files = [MAILBOX / f"{e.upper()}.eml" for e in args.emails]
    if not files or any(not f.exists() for f in files):
        sys.exit("Nothing to deliver. Run 'python -m mailbox_sim.build' first, and give email ids like E10 or --all.")

    if args.reset:
        call("POST", f"{BC}/reset")
        print("Simulated Business Central reset: no documents, master data reloaded from the CSVs.\n")

    for f in files:
        deliver_and_print(f, args.direct, args.webhook)


def deliver(path, direct: bool = False, webhook: str = WEBHOOK) -> dict:
    """Deliver one .eml file and wait for the flow to finish. Raises HttpError/OSError if it wasn't processed."""
    payload = {"filename": path.name, "eml_base64": base64.b64encode(path.read_bytes()).decode()}
    return deliver_direct(payload) if direct else call("POST", webhook, payload, timeout=120)


def deliver_and_print(path, direct: bool = False, webhook: str = WEBHOOK, width: int = 150) -> dict | None:
    try:
        r = deliver(path, direct, webhook)
    except (HttpError, OSError) as e:
        print(f"{path.stem:4} NOT PROCESSED: {e}. It stays in the mailbox; nothing was booked.")
        return None
    bc = ", ".join(f"{l['purchase_invoice_no'] or l['incoming_document_no']} {l['status']}" for l in r.get("logged", []))
    reason = r.get("message") or r.get("flag_reason", "")    # message: what the flow did next (n8n)
    print(f"{r['email_id']:4} {r['action'].upper():6} → BC: {bc}")
    if reason and width:
        print(f"       {reason[:width]}{'…' if len(reason) > width else ''}")
    return r


if __name__ == "__main__":
    main()
