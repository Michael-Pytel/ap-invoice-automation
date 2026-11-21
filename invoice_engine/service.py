"""The invoice check as a small local web service (what n8n, or Power Automate in production, calls).

Run:    python -m invoice_engine.service [--host 127.0.0.1] [--port 8101] [--bc http://127.0.0.1:8100]

  GET  /health
  POST /process     one email as JSON:
                    {"email_id", "received_at", "from", "to", "subject", "body",
                     "attachments": [{"filename": "x.pdf", "content_base64": "..."}]}
  POST /process-eml one real email file: {"eml_base64": "..."}  (what the n8n flow sends)

Returns the action for the email (post / hold / reject), the invoice lines in the target format, and the
record BC should keep. It does not write anything itself: the flow sends the result on to BC.

Everything runs on this machine: PDFs are read locally and nothing is sent to an outside service.
If something breaks while reading an email, the answer is hold (enter by hand), never post.
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import tempfile
import traceback
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from .httpjson import HttpError, serve

from . import bc_client
from .reference import Registered
from .rules import Email, email_action, process_email_documents, summary_row


def email_from_json(body: dict, folder: Path, paths: list[Path] | None = None) -> Email:
    missing = [k for k in ("email_id", "received_at", "from") if not body.get(k)]
    if missing:
        raise HttpError(400, f"Missing field(s): {', '.join(missing)}")
    paths = list(paths or [])
    for a in body.get("attachments") or []:
        path = folder / Path(a["filename"]).name          # never trust a path from an email
        path.write_bytes(base64.b64decode(a["content_base64"]))
        paths.append(path)
    return Email(body["email_id"], body["received_at"], body["from"], body.get("to", ""), body.get("subject", ""),
                 body.get("body", ""), paths)


def failed_result(email: Email, error: str) -> dict:
    """Something went wrong while reading the email: hold it for a person, with the error in plain words."""
    record = Registered(email.email_id, "", "", "", "unknown", "hold", "", "", "", None, "", received_at=email.received_at)
    base = {"email_id": email.email_id, "document_type": "unknown", "vendor_no": "", "vendor_name": email.sender,
            "vendor_invoice_no": "", "invoice_date": "", "due_date": "", "currency": ""}
    row = summary_row(base, None, "hold", f"The invoice check could not read this email ({error}). Enter it by "
                      f"hand; nothing has been booked.", email.subject, confidence="low")
    return {"email_id": email.email_id, "action": "hold", "documents": [{"document": record.to_json(), "rows": [row]}],
            "rows": [row]}


def email_from_eml(body: dict, folder: Path) -> Email:
    """Read a real email file (.eml, as Outlook or any mail server stores it), sent base64-encoded."""
    if not body.get("eml_base64"):
        raise HttpError(400, "Expected {'eml_base64': ...}")
    msg = BytesParser(policy=policy.default).parsebytes(base64.b64decode(body["eml_base64"]))
    email_id = msg.get("X-Case-Email-Id") or (msg.get("Message-ID") or "").strip("<>")
    received = parsedate_to_datetime(msg["Date"]).strftime("%Y-%m-%d %H:%M") if msg["Date"] else ""
    text = msg.get_body(preferencelist=("plain",))
    paths = []
    for part in msg.iter_attachments():
        path = folder / Path(part.get_filename() or f"attachment{len(paths) + 1}").name   # never trust a path
        path.write_bytes(part.get_payload(decode=True) or b"")
        paths.append(path)
    return email_from_json({"email_id": email_id, "received_at": received, "from": parseaddr(msg["From"] or "")[1],
                            "to": msg["To"] or "", "subject": msg["Subject"] or "",
                            "body": text.get_content() if text else "", "attachments": []}, folder, paths)


def process(body: dict, bc_url: str, eml: bool = False) -> dict:
    try:
        ref, register = bc_client.load_from_bc(bc_url)
    except Exception as e:
        # Without BC we can't check supplier, bank account or duplicates: refuse, so the flow retries later.
        raise HttpError(503, f"Business Central is not reachable ({e}); the email was not processed.")
    with tempfile.TemporaryDirectory() as tmp:
        email = email_from_eml(body, Path(tmp)) if eml else email_from_json(body, Path(tmp))
        try:
            results = process_email_documents(email, ref, register)
        except subprocess.CalledProcessError:
            traceback.print_exc()
            return failed_result(email, "an attachment could not be opened: damaged, protected or not a PDF")
        except Exception as e:
            traceback.print_exc()                      # full details stay in the service log for us
            return failed_result(email, f"unexpected error: {type(e).__name__}")
    rows = [r for _, doc_rows in results for r in doc_rows]
    return {"email_id": email.email_id, "action": email_action(rows),
            "documents": [{"document": rec.to_json(), "rows": doc_rows} for rec, doc_rows in results],
            "rows": rows}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 so n8n in Docker can reach it")
    p.add_argument("--port", type=int, default=8101)
    p.add_argument("--bc", default=bc_client.BC_URL, help="address of the (simulated) Business Central")
    args = p.parse_args()
    routes = [
        ("GET", "/health", lambda prm, q, b: {"status": "ok", "service": "invoice check", "bc": args.bc}),
        ("POST", "/process", lambda prm, q, b: process(b if isinstance(b, dict) else {}, args.bc)),
        ("POST", "/process-eml", lambda prm, q, b: process(b if isinstance(b, dict) else {}, args.bc, eml=True)),
    ]
    serve(routes, args.host, args.port, "invoice check")


if __name__ == "__main__":
    main()
