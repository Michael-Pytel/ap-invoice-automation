"""Reference data (suppliers, POs, accounts, VAT codes, cost centres) and the register of processed documents.

The CSVs in the case folder are the reference data and are never modified. They play the role of Business
Central. The register is the tool's memory of documents it has already seen (for duplicates, credit notes,
statements and PO quantities already invoiced). With the simulated Business Central running, both come from BC
(see bc_client.py); the rules only use the methods below, so they don't change.
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASE = ROOT / "case_B_invoices_without_typing"
CONFIG = ROOT / "config"

EU_COUNTRIES = {"AT", "BE", "BG", "HR", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HU", "IE", "IT", "LT",
                "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalise_account(text: str) -> str:
    """Compare bank accounts without spaces, dashes or labels."""
    return re.sub(r"[^A-Z0-9]", "", text.upper().replace("CN-ACC", ""))


# The master data that lives in Business Central. Offline it is read from the CSVs; with the simulated BC
# running, the same tables come from BC (which seeds itself from these files).
REFERENCE_FILES = {
    "vendors": CASE / "vendors.csv",
    "purchase_order_lines": CASE / "open_purchase_orders.csv",
    "gl_accounts": CASE / "chart_of_accounts.csv",
    "cost_centres": CASE / "cost_centres.csv",
    "vat_codes": CASE / "vat_codes.csv",
    "currency_rates": CONFIG / "rates.csv",
}


def load_reference_tables() -> dict[str, list[dict]]:
    return {name: read_csv(path) for name, path in REFERENCE_FILES.items()}


class ReferenceData:
    """Master data from BC (tables) plus our own rule tables in config/ (line rules, suggestions)."""

    def __init__(self, tables: dict[str, list[dict]] | None = None, config_dir: Path = CONFIG):
        tables = tables if tables is not None else load_reference_tables()
        self.vendors = tables["vendors"]
        self.po_lines = tables["purchase_order_lines"]
        self.accounts = {r["gl_account"]: r for r in tables["gl_accounts"]}
        self.cost_centres = {r["cost_centre"]: r for r in tables["cost_centres"]}
        self.vat_codes = {r["vat_code"]: r for r in tables["vat_codes"]}
        self.rates = {r["currency"]: Decimal(r["rate_to_dkk"]) for r in tables["currency_rates"]}
        self.target_columns = list(read_csv(CASE / "target_format_purchase_invoice_lines.csv")[0].keys())
        self.line_rules = read_csv(config_dir / "line_rules.csv")
        self.suggestions = read_csv(config_dir / "suggestions.csv")

    # Suppliers
    def vendor_by_vat(self, vat: str) -> dict | None:
        return next((v for v in self.vendors if vat and v["vat_no"] == vat), None)

    def vendor_by_domain(self, domain: str) -> dict | None:
        return next((v for v in self.vendors if domain and v["email_domain"] == domain.lower()), None)

    def bank_matches(self, vendor: dict, account_on_document: str) -> bool | None:
        """True/False, or None when the supplier isn't paid by bank transfer (e.g. card)."""
        known = vendor["bank_account"]
        if not known or "card" in known.lower():
            return None
        doc = account_on_document
        if m := re.match(r"reg\.\s*(\d{4})\s*konto\s*(\d+)", doc):  # Danish reg. + account, written in an email
            return normalise_account(known)[4:] == m.group(1) + m.group(2).zfill(10)
        return normalise_account(known) == normalise_account(doc)

    # Purchase orders
    def po(self, po_number: str) -> list[dict]:
        return [l for l in self.po_lines if l["po_number"] == po_number]

    # Coding
    def budget_owner(self, cost_centre: str) -> str:
        return self.cost_centres.get(cost_centre, {}).get("budget_owner_role", "")

    def to_dkk(self, amount: Decimal, currency: str) -> Decimal | None:
        rate = self.rates.get(currency)
        return None if rate is None else amount * rate


@dataclass
class Registered:
    email_id: str
    vendor_key: str
    vendor_no: str
    invoice_no: str
    document_type: str
    action: str
    invoice_date: str
    due_date: str
    currency: str
    total: Decimal | None
    fingerprint: str
    po_numbers: list[str] = field(default_factory=list)
    shipment: str = ""
    po_quantities: dict = field(default_factory=dict)   # (po_number, line) -> quantity invoiced
    received_at: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["total"] = None if self.total is None else str(self.total)
        d["po_quantities"] = [[po, line, str(q)] for (po, line), q in self.po_quantities.items()]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Registered":
        names = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in names}
        d["total"] = None if d.get("total") in (None, "") else Decimal(d["total"])
        d["po_quantities"] = {(po, line): Decimal(q) for po, line, q in d.get("po_quantities") or []}
        return cls(**d)


class Register:
    """Documents already received: for duplicates, credit notes, statements and PO quantities already invoiced.

    Offline it starts empty and fills up during the batch. With the simulated BC it is loaded from BC's incoming
    documents for each email, so the rules service itself keeps no state.
    """

    def __init__(self, documents: list[Registered] | None = None):
        self.documents: list[Registered] = list(documents or [])
        self.added: list[Registered] = []      # added while processing the current email

    def add(self, record: Registered) -> None:
        self.documents.append(record)
        self.added.append(record)

    def find_invoice(self, vendor_key: str, invoice_no: str) -> Registered | None:
        key = invoice_no.replace(" ", "").lower()
        return next((d for d in self.documents if d.vendor_key == vendor_key and d.action != "reject"
                     and d.document_type in ("invoice", "credit_note")
                     and d.invoice_no.replace(" ", "").lower() == key), None)

    def invoiced_quantity(self, po_number: str, line: str) -> Decimal:
        return sum((d.po_quantities.get((po_number, line), Decimal("0")) for d in self.documents
                    if d.action != "reject"), Decimal("0"))

    def by_shipment(self, shipment: str, exclude_vendor: str) -> list[Registered]:
        return [d for d in self.documents if shipment and d.shipment == shipment and d.vendor_key != exclude_vendor]
