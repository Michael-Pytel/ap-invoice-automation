"""The simulated Business Central database (SQLite, output/bc.db).

- Master data (vendors, purchase order lines, G/L accounts, cost centres, VAT codes, exchange rates) is loaded
  from the CSVs every time BC starts. The CSVs stay the reference; nothing here edits master data.
- Incoming documents: every email the flow delivers is logged, including receipts, statements and duplicates,
  so nothing gets lost.
- Purchase invoices (drafts): created for post and hold. Status On hold -> Pending approval -> Released -> Posted.
  Nothing is paid here, and nothing is posted without a person pressing post.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from invoice_engine.reference import ROOT, load_reference_tables

DB_PATH = ROOT / "output" / "bc.db"

AUTO_APPROVAL = "Auto: PO match (rule 2)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS incoming_documents (
    no TEXT PRIMARY KEY, email_id TEXT, received_at TEXT, document_type TEXT, action TEXT,
    vendor_key TEXT, vendor_no TEXT, vendor_name TEXT, invoice_no TEXT, invoice_date TEXT, due_date TEXT,
    currency TEXT, total TEXT, fingerprint TEXT, po_numbers TEXT, shipment TEXT, po_quantities TEXT,
    flag_reason TEXT, purchase_invoice_no TEXT, booking_rows TEXT, logged_at TEXT);
CREATE TABLE IF NOT EXISTS purchase_invoices (
    no TEXT PRIMARY KEY, incoming_document_no TEXT, email_id TEXT, vendor_no TEXT, vendor_name TEXT,
    vendor_invoice_no TEXT, document_type TEXT, invoice_date TEXT, due_date TEXT, currency TEXT,
    amount_excl_vat TEXT, vat_amount TEXT, status TEXT, approver_role TEXT, approvals TEXT,
    flag_reason TEXT, history TEXT, posted_no TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS purchase_invoice_lines (
    invoice_no TEXT, line_no INTEGER, description TEXT, gl_account TEXT, cost_centre TEXT, vat_code TEXT,
    amount_excl_vat TEXT, vat_amount TEXT, po_number TEXT, confidence TEXT,
    PRIMARY KEY (invoice_no, line_no));
"""
TRANSACTION_TABLES = ("incoming_documents", "purchase_invoices", "purchase_invoice_lines")


class Conflict(Exception):
    """The requested step isn't allowed in the invoice's current status."""


class NotFound(Exception):
    pass


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BusinessCentral:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        columns = [r["name"] for r in self.db.execute("PRAGMA table_info(incoming_documents)")]
        if columns and "booking_rows" not in columns:           # database from an older version: start it fresh
            for t in TRANSACTION_TABLES:
                self.db.execute(f"DROP TABLE IF EXISTS {t}")
        self.db.executescript(SCHEMA)
        self.load_master_data()

    # ------------------------------------------------------------ master data
    def load_master_data(self) -> None:
        """(Re)load master data from the CSVs. Called on every start, so the CSVs are always the truth."""
        with self.lock, self.db:
            for table, rows in load_reference_tables().items():
                columns = list(rows[0].keys())
                self.db.execute(f'DROP TABLE IF EXISTS "{table}"')
                self.db.execute(f'CREATE TABLE "{table}" ({", ".join(f'"{c}" TEXT' for c in columns)})')
                self.db.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" * len(columns))})',
                                    [[r[c] for c in columns] for r in rows])

    def reference(self) -> dict[str, list[dict]]:
        tables = load_reference_tables().keys()
        return {t: [dict(r) for r in self.db.execute(f'SELECT * FROM "{t}"')] for t in tables}

    def reset(self) -> None:
        """Empty all documents and invoices and reload master data (start of a demo run)."""
        with self.lock, self.db:
            for t in TRANSACTION_TABLES:
                self.db.execute(f"DELETE FROM {t}")
        self.load_master_data()

    # ------------------------------------------------------------ incoming documents
    def incoming_documents(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM incoming_documents ORDER BY no"):
            d = dict(r)
            d["po_numbers"] = json.loads(d["po_numbers"] or "[]")
            d["po_quantities"] = json.loads(d["po_quantities"] or "[]")
            d["rows"] = json.loads(d.pop("booking_rows") or "[]")
            out.append(d)
        return out

    def booking_lines(self) -> list[dict]:
        """Every line the invoice check produced, in the target format (rejects included, with their reason)."""
        rows = [r for d in self.incoming_documents() for r in d["rows"]]
        return sorted(rows, key=lambda r: (r["email_id"], int(r["line_no"])))

    def log(self, result: dict) -> list[dict]:
        """Store what the rules service returned for one email: an incoming document per attachment, and a
        draft purchase invoice for each one that is to be posted or held. Rejects are logged only."""
        created = []
        with self.lock, self.db:
            for item in result["documents"]:
                doc, rows = item["document"], item["rows"]
                doc_no = self._next_no("incoming_documents", "ID")
                invoice_no = ""
                if doc["action"] in ("post", "hold") and rows:
                    invoice_no = self._create_invoice(doc_no, doc, rows)
                record = {
                    "no": doc_no, "email_id": doc["email_id"], "received_at": doc["received_at"],
                    "document_type": rows[0]["document_type"] if rows else doc["document_type"],
                    "action": doc["action"], "vendor_key": doc["vendor_key"], "vendor_no": doc["vendor_no"],
                    "vendor_name": rows[0]["vendor_name"] if rows else doc["vendor_key"],
                    "invoice_no": doc["invoice_no"], "invoice_date": doc["invoice_date"], "due_date": doc["due_date"],
                    "currency": doc["currency"], "total": doc["total"], "fingerprint": doc["fingerprint"],
                    "po_numbers": json.dumps(doc["po_numbers"]), "shipment": doc["shipment"],
                    "po_quantities": json.dumps(doc["po_quantities"]),
                    "flag_reason": rows[0]["flag_reason"] if rows else "", "purchase_invoice_no": invoice_no,
                    "booking_rows": json.dumps(rows, ensure_ascii=False), "logged_at": now()}
                self.db.execute(f"INSERT INTO incoming_documents ({', '.join(record)}) VALUES "
                                f"({', '.join('?' * len(record))})", list(record.values()))
                status = self._invoice(invoice_no)["status"] if invoice_no else "Logged only (rejected)"
                created.append({"email_id": doc["email_id"], "incoming_document_no": doc_no,
                                "purchase_invoice_no": invoice_no, "status": status, "action": doc["action"]})
        return created

    # ------------------------------------------------------------ purchase invoices
    def _create_invoice(self, doc_no: str, doc: dict, rows: list[dict]) -> str:
        no = self._next_no("purchase_invoices", "PI")
        first = rows[0]
        excl = sum((Decimal(r["amount_excl_vat"] or "0") for r in rows), Decimal("0"))
        vat = sum((Decimal(r["vat_amount"] or "0") for r in rows), Decimal("0"))
        if doc["action"] == "hold":
            status, approvals, event = "On hold", [], "Created on hold by the invoice check"
        elif first["approver_role"] == AUTO_APPROVAL:
            status, approvals, event = "Released", [AUTO_APPROVAL], "Created and approved automatically (PO match)"
        else:
            status, approvals, event = "Pending approval", [], f"Created; waiting for {first['approver_role']}"
        history = [{"at": now(), "by": "Invoice check", "event": event, "note": first["flag_reason"]}]
        self.db.execute("INSERT INTO purchase_invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (no, doc_no, doc["email_id"], first["vendor_no"], first["vendor_name"],
                         first["vendor_invoice_no"], first["document_type"], first["invoice_date"],
                         first["due_date"], first["currency"], f"{excl:.2f}", f"{vat:.2f}", status,
                         first["approver_role"], json.dumps(approvals), first["flag_reason"],
                         json.dumps(history, ensure_ascii=False), "", now()))
        self.db.executemany("INSERT INTO purchase_invoice_lines VALUES (?,?,?,?,?,?,?,?,?,?)",
                            [(no, int(r["line_no"]), r["description"], r["gl_account"], r["cost_centre"],
                              r["vat_code"], r["amount_excl_vat"], r["vat_amount"], r["po_number"], r["confidence"])
                             for r in rows])
        return no

    def purchase_invoices(self, status: str = "") -> list[dict]:
        sql, args = "SELECT no FROM purchase_invoices", ()
        if status:
            sql, args = sql + " WHERE status = ?", (status,)
        return [self._invoice(r["no"]) for r in self.db.execute(sql + " ORDER BY no", args)]

    def purchase_invoice(self, no: str) -> dict:
        return self._invoice(no)

    def approve(self, no: str, by: str) -> dict:
        """Approval by one of the roles on the invoice. When all have approved, the invoice is Released."""
        with self.lock, self.db:
            inv = self._invoice(no)
            if inv["status"] != "Pending approval":
                raise Conflict(f"{no} is '{inv['status']}'; only invoices pending approval can be approved.")
            roles = inv["approver_role"].split(" + ")
            if by not in roles:
                raise Conflict(f"{by} is not an approver of {no} (approvers: {inv['approver_role']}).")
            if by in inv["approvals"]:
                raise Conflict(f"{by} has already approved {no}.")
            approvals = inv["approvals"] + [by]
            status = "Released" if all(r in approvals for r in roles) else "Pending approval"
            self._update(inv, status=status, approvals=approvals, by=by, event=f"Approved by {by}")
        return self._invoice(no)

    def resolve(self, no: str, by: str, note: str) -> dict:
        """A person has dealt with the flag (e.g. phoned the supplier). The invoice then goes for approval."""
        if not note.strip():
            raise Conflict("Write what was checked before taking an invoice off hold.")
        with self.lock, self.db:
            inv = self._invoice(no)
            if inv["status"] != "On hold":
                raise Conflict(f"{no} is '{inv['status']}', not on hold.")
            self._update(inv, status="Pending approval", by=by, event="Taken off hold", note=note)
        return self._invoice(no)

    def post(self, no: str, by: str) -> dict:
        """Post a released invoice. Refuses anything not fully approved, and a vendor invoice number that is
        already posted for the same supplier (Business Central has a similar built-in check)."""
        with self.lock, self.db:
            inv = self._invoice(no)
            if inv["status"] != "Released":
                raise Conflict(f"{no} is '{inv['status']}'; only released (fully approved) invoices can be posted.")
            dup = self.db.execute("SELECT no FROM purchase_invoices WHERE status = 'Posted' AND vendor_no = ? AND "
                                  "vendor_no != '' AND vendor_invoice_no = ? AND document_type = ?",
                                  (inv["vendor_no"], inv["vendor_invoice_no"], inv["document_type"])).fetchone()
            if dup:
                raise Conflict(f"Vendor invoice {inv['vendor_invoice_no']} is already posted ({dup['no']}).")
            posted_no = self._next_no("purchase_invoices", "PPI", column="posted_no")
            self._update(inv, status="Posted", by=by, event=f"Posted as {posted_no}", posted_no=posted_no)
        return self._invoice(no)

    # ------------------------------------------------------------ helpers
    def _invoice(self, no: str) -> dict:
        r = self.db.execute("SELECT * FROM purchase_invoices WHERE no = ?", (no,)).fetchone()
        if not r:
            raise NotFound(f"No purchase invoice {no}.")
        inv = dict(r)
        inv["approvals"] = json.loads(inv["approvals"])
        inv["history"] = json.loads(inv["history"])
        inv["lines"] = [dict(l) for l in self.db.execute(
            "SELECT * FROM purchase_invoice_lines WHERE invoice_no = ? ORDER BY line_no", (no,))]
        return inv

    def _update(self, inv: dict, status: str, by: str, event: str, note: str = "", approvals: list | None = None,
                posted_no: str = "") -> None:
        history = inv["history"] + [{"at": now(), "by": by, "event": event, "note": note}]
        self.db.execute("UPDATE purchase_invoices SET status = ?, approvals = ?, history = ?, posted_no = "
                        "COALESCE(NULLIF(?, ''), posted_no) WHERE no = ?",
                        (status, json.dumps(approvals if approvals is not None else inv["approvals"]),
                         json.dumps(history, ensure_ascii=False), posted_no, inv["no"]))

    def _next_no(self, table: str, prefix: str, column: str = "no") -> str:
        n = self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE ?", (f"{prefix}-%",)).fetchone()[0]
        return f"{prefix}-{n + 1:04d}"
