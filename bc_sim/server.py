"""Simulated Business Central web endpoints.

Run:    python -m bc_sim.server [--host 127.0.0.1] [--port 8100] [--reset]

  GET  /                                        the review page for Finance and approvers (open in a browser)
  GET  /export/purchase-invoice-lines.csv       all booking lines in the target format
  GET  /health
  POST /reset                                  empty documents and invoices, reload master data
  GET  /reference                               master data (vendors, POs, accounts, VAT codes, rates)
  GET  /incoming-documents                      every document received so far
  POST /incoming-documents                      log a rules-service result (n8n sends it here)
  GET  /purchase-invoices?status=On hold        draft purchase invoices, optionally by status
  GET  /purchase-invoices/{no}
  POST /purchase-invoices/{no}/approve          {"by": "CMO"}
  POST /purchase-invoices/{no}/resolve          {"by": "Finance", "note": "Phoned Meža, account confirmed"}
  POST /purchase-invoices/{no}/post             {"by": "Finance"}
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

from invoice_engine.httpjson import HttpError, serve
from invoice_engine.reference import CASE, read_csv

from .store import BusinessCentral, Conflict, NotFound

PAGE = Path(__file__).resolve().parent / "page.html"
TARGET_COLUMNS = list(read_csv(CASE / "target_format_purchase_invoice_lines.csv")[0].keys())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 so n8n in Docker can reach it")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--reset", action="store_true", help="start with no documents")
    args = p.parse_args()

    bc = BusinessCentral()
    if args.reset:
        bc.reset()

    def guarded(fn):
        def wrapper(params, query, body):
            try:
                return fn(params, query, body if isinstance(body, dict) else {})
            except NotFound as e:
                raise HttpError(404, str(e))
            except Conflict as e:
                raise HttpError(409, str(e))
        return wrapper

    def need(body: dict, key: str) -> str:
        if not str(body.get(key, "")).strip():
            raise HttpError(400, f"'{key}' is required.")
        return body[key]

    def log_result(params, query, body):
        if "documents" not in body:
            raise HttpError(400, "Expected the rules service result (with 'documents').")
        return {"logged": bc.log(body)}

    def reset(params, query, body):
        bc.reset()
        return {"status": "reset", "master_data": {t: len(rows) for t, rows in bc.reference().items()}}

    def page(params, query, body):
        return (PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")

    def export_csv(params, query, body):
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=TARGET_COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(bc.booking_lines())
        return ("﻿" + out.getvalue(), "text/csv; charset=utf-8")

    routes = [
        ("GET", "/", page),
        ("GET", "/export/purchase-invoice-lines.csv", export_csv),
        ("GET", "/health", lambda p, q, b: {"status": "ok", "service": "simulated Business Central"}),
        ("POST", "/reset", reset),
        ("GET", "/reference", lambda p, q, b: bc.reference()),
        ("GET", "/incoming-documents", lambda p, q, b: bc.incoming_documents()),
        ("POST", "/incoming-documents", log_result),
        ("GET", "/purchase-invoices", lambda p, q, b: bc.purchase_invoices(q.get("status", ""))),
        ("GET", "/purchase-invoices/{no}", lambda p, q, b: bc.purchase_invoice(p["no"])),
        ("POST", "/purchase-invoices/{no}/approve", lambda p, q, b: bc.approve(p["no"], need(b, "by"))),
        ("POST", "/purchase-invoices/{no}/resolve",
         lambda p, q, b: bc.resolve(p["no"], need(b, "by"), need(b, "note"))),
        ("POST", "/purchase-invoices/{no}/post", lambda p, q, b: bc.post(p["no"], need(b, "by"))),
    ]
    serve([(m, path, guarded(fn)) for m, path, fn in routes], args.host, args.port, "simulated BC")


if __name__ == "__main__":
    main()
