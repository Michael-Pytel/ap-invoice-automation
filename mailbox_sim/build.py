"""Mailbox simulator, step 1: turn each row of invoice_emails.csv into a real email file (.eml).

Usage:  python -m mailbox_sim.build
Output: output/mailbox/E01.eml ... E16.eml  (from, to, subject, date, text, PDF attachments)

These are ordinary emails, the same format Outlook stores. The invoice check reads them like a real mailbox,
so moving to the real invoice@ mailbox later only changes how the email arrives (the trigger).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from invoice_engine.reference import CASE, ROOT, read_csv

MAILBOX = ROOT / "output" / "mailbox"
COPENHAGEN = timezone(timedelta(hours=1))      # illustrative; the check reads back the time as written


def build_eml(row: dict) -> bytes:
    msg = EmailMessage()
    msg["From"] = row["from"]
    msg["To"] = row["to"]
    msg["Subject"] = row["subject"]
    msg["Date"] = format_datetime(datetime.strptime(row["received_at"], "%Y-%m-%d %H:%M").replace(tzinfo=COPENHAGEN))
    msg["Message-ID"] = f"<{row['email_id']}@mailbox-simulator.local>"
    msg["X-Case-Email-Id"] = row["email_id"]          # so results can be traced back to the case data
    msg.set_content(row["body"] or "")
    for name in (a.strip() for a in row["attachments"].split(";")):
        if name:
            msg.add_attachment((CASE / "attachments" / name).read_bytes(), maintype="application", subtype="pdf",
                               filename=name)
    return bytes(msg)


def main() -> None:
    MAILBOX.mkdir(parents=True, exist_ok=True)
    rows = read_csv(CASE / "invoice_emails.csv")
    for row in rows:
        (MAILBOX / f"{row['email_id']}.eml").write_bytes(build_eml(row))
    print(f"{len(rows)} emails written to {MAILBOX}")


if __name__ == "__main__":
    main()
