"""Read invoices, credit notes, receipts and statements into plain fields.

No AI here: every PDF in the case has a text layer, so the text is extracted locally with
pdftotext and read with patterns that cover the five languages in the mailbox.
Nothing leaves the machine.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

CENT = Decimal("0.01")


# ---------------------------------------------------------------- numbers and text

def parse_amount(text: str) -> Decimal:
    """Parse '14.200,00', '5,376.00', '9.000', '-620,00' or '44.80' into a Decimal."""
    s = text.strip().replace(" ", "").replace("\u00a0", "")
    negative = s.startswith("-")
    s = s.lstrip("-")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # 14.200,00  (Danish/Italian/German)
            s = s.replace(".", "").replace(",", ".")
        else:  # 5,376.00  (English)
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")  # 38,70
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")  # 9.000  (Danish thousands, no decimals)
    value = Decimal(s)
    return -value if negative else value


def pdf_text(path: Path) -> str:
    """Extract the text layer of a PDF in reading order (pdftotext -raw)."""
    # Several pdftotext builds can be on PATH and they differ in spacing; prefer the one the rules were tested with.
    tested = Path(r"C:\Program Files\Git\mingw64\bin\pdftotext.exe")
    exe = os.environ.get("PDFTOTEXT") or (str(tested) if tested.exists() else shutil.which("pdftotext"))
    result = subprocess.run([exe, "-raw", "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True)
    return result.stdout.decode("utf-8").replace("\r\n", "\n").replace("\f", "\n")


def file_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- document model

@dataclass
class Line:
    description: str
    amount: Decimal
    item_no: str = ""
    quantity: Decimal | None = None
    unit: str = ""
    unit_price: Decimal | None = None


@dataclass
class StatementRow:
    invoice_no: str
    invoice_date: str
    due_date: str
    amount_incl_vat: Decimal


@dataclass
class Document:
    source: str                      # file name, or "email body"
    text: str
    supplier_name: str = ""
    supplier_vat: str = ""
    supplier_domain: str = ""
    addressee: str = ""
    title: str = ""
    invoice_no: str = ""
    invoice_date: str = ""
    due_date: str = ""               # ISO date, "Paid", or ""
    reference: str = ""
    po_numbers: list[str] = field(default_factory=list)
    shipment: str = ""
    currency: str = ""
    lines: list[Line] = field(default_factory=list)
    subtotal: Decimal | None = None
    vat_rate: Decimal | None = None
    vat_amount: Decimal | None = None
    total: Decimal | None = None
    bank_account: str = ""
    swift: str = ""
    reverse_charge_note: bool = False
    paid_note: bool = False
    statement_rows: list[StatementRow] = field(default_factory=list)
    fingerprint: str = ""
    from_email_body: bool = False


# ---------------------------------------------------------------- PDF documents

BILL_TO = re.compile(r"^(Bill to|Faktureres til|Fakturamottagare|Destinatario|Rechnungsempfänger)$", re.I)
TITLE = re.compile(r"^(COMMERCIAL INVOICE|Invoice|Faktura|Fattura|Rechnung|Nota di credito|Credit note|Kreditnota|"
                   r"Receipt|Kvittering|Kontoudtog|Statement)\b", re.I)
INVOICE_NO = re.compile(r"^(?:Invoice no\.|Fakturanr\.?|Numero|Rechnungsnr\.)\s+(.+)$", re.M)
INVOICE_DATE = re.compile(r"^(?:Invoice date|Fakturadato|Fakturadatum|Data|Rechnungsdatum)\s+(\d{4}-\d{2}-\d{2})", re.M)
DUE_DATE = re.compile(r"^(?:Due date|Forfaldsdato|Förfallodatum|Scadenza|Fällig am)\s+(\S+)", re.M)
REFERENCE = re.compile(r"^(?:Reference|Riferimento|Er referens|Betreff):\s*(.*)$", re.I)
TABLE_HEADER = re.compile(r"^(Description|Beskrivelse|Beskrivning|Descrizione|Beschreibung)\b.*\b(DKK|EUR|USD|SEK)\s*$")
TABLE_LINE = re.compile(r"^(?P<desc>.+?)\s+(?P<qty>-?\d+(?:[.,]\d+)?)\s+(?P<unit>[^\s\d][^\s]*)\s+"
                        r"(?P<price>-?[\d.,]+)\s+(?P<amount>-?[\d.,]+)$")
ITEM_NO = re.compile(r"^([A-Z]{2}-[A-Z0-9]+(?:-[A-Z0-9]+)+)\s+(.*)$")
SUBTOTAL = re.compile(r"^(?:Subtotal|Netto|Imponibile|Nettobetrag)\s+(-?[\d.,]+)$", re.M)
VAT_LINE = re.compile(r"^(?:Moms|VAT|IVA|MwSt\.?)\s*(?:(\d+)\s*%)?\s+(-?[\d.,]+)$", re.M)
TOTAL = re.compile(r"^(?:Total(?: USD| EUR| DKK| SEK)?|I alt(?: DKK)?|Att betala(?: SEK)?|Totale documento(?: EUR)?|"
                   r"Gesamtbetrag(?: EUR)?)\s+(-?[\d.,]+)$", re.M)
BANK = re.compile(r"^(?:Payment|Betaling|Betalning|Coordinate bancarie|Bankverbindung):\s*(.+)$", re.M)
STATEMENT_ROW = re.compile(r"^(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(-?[\d.,]+)$", re.M)
REVERSE_CHARGE = re.compile(r"reverse charge|inversione contabile|omvänd skattskyldighet|non imponibile", re.I)
PAID = re.compile(r"paid by card|\bDue date Paid\b|betalt med|no payment due", re.I)
PO_NUMBER = re.compile(r"NW-PO-\d{4}-\d{3}")
SHIPMENT = re.compile(r"NHS-[A-Z]+-\d+")


def parse_pdf(path: Path) -> Document:
    data = path.read_bytes()
    text = pdf_text(path)
    doc = parse_text(text, source=path.name)
    doc.fingerprint = file_fingerprint(data)
    return doc


def parse_text(text: str, source: str) -> Document:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    doc = Document(source=source, text=text)
    if not lines:
        return doc

    # Supplier block: the lines before the "Bill to" block.
    doc.supplier_name = lines[0]
    header = lines[:6]
    for l in header:
        if m := re.fullmatch(r"(?:VAT|CVR)\s+([A-Z]{0,2}\d{6,})", l):
            doc.supplier_vat = m.group(1)
        elif re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", l):
            doc.supplier_domain = l

    for i, l in enumerate(lines):
        if BILL_TO.match(l) and i + 1 < len(lines):
            doc.addressee = lines[i + 1]
            break

    for l in lines:
        if TITLE.match(l):
            doc.title = l
            break

    if m := INVOICE_NO.search(text):
        doc.invoice_no = m.group(1).strip()
    elif m := re.search(r"^Kvittering nr\.\s*(\S+)", text, re.M):
        doc.invoice_no = m.group(1)
    if m := INVOICE_DATE.search(text):
        doc.invoice_date = m.group(1)
    elif m := re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text):
        doc.invoice_date = m.group(1)
    if m := DUE_DATE.search(text):
        doc.due_date = m.group(1)

    # Reference, possibly continued on the next line (up to the table header).
    for i, l in enumerate(lines):
        if m := REFERENCE.match(l):
            ref = m.group(1)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and not TABLE_HEADER.match(nxt) and not nxt.startswith(("Shipment", "Description", "Faktura Dato")):
                ref += " " + nxt
            doc.reference = re.sub(r"^(Er referens:\s*)+", "", ref).strip()
            break

    doc.po_numbers = sorted(set(PO_NUMBER.findall(text)))
    if m := SHIPMENT.search(text):
        doc.shipment = m.group(0)

    # Line table.
    in_table = False
    for l in lines:
        if m := TABLE_HEADER.match(l):
            in_table, doc.currency = True, m.group(2)
            continue
        if in_table:
            if SUBTOTAL.match(l):
                break
            if m := TABLE_LINE.match(l):
                desc, item = m.group("desc"), ""
                if im := ITEM_NO.match(desc):
                    item, desc = im.group(1), im.group(2)
                doc.lines.append(Line(description=desc, item_no=item, quantity=parse_amount(m.group("qty")),
                                      unit=m.group("unit"), unit_price=parse_amount(m.group("price")),
                                      amount=parse_amount(m.group("amount"))))

    if m := SUBTOTAL.search(text):
        doc.subtotal = parse_amount(m.group(1))
    if m := VAT_LINE.search(text):
        doc.vat_rate = Decimal(m.group(1)) if m.group(1) else None
        doc.vat_amount = parse_amount(m.group(2))
    if m := TOTAL.search(text):
        doc.total = parse_amount(m.group(1))
    if not doc.currency and (m := re.search(r"\b(DKK|EUR|USD|SEK)\b", text)):
        doc.currency = m.group(1)

    if m := BANK.search(text):
        parts = m.group(1).split("·")
        doc.bank_account = parts[0].strip()
        if sm := re.search(r"SWIFT\s+(\w+)", m.group(1)):
            doc.swift = sm.group(1)

    doc.reverse_charge_note = bool(REVERSE_CHARGE.search(text))
    doc.paid_note = bool(PAID.search(text))
    doc.statement_rows = [StatementRow(r[0], r[1], r[2], parse_amount(r[3]))
                          for r in STATEMENT_ROW.findall(text) if re.search(r"\d", r[0])]
    return doc


# ---------------------------------------------------------------- invoices written in the email body

DANISH_MONTHS = {m: i for i, m in enumerate(
    ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november",
     "december"], start=1)}


def parse_email_body(body: str, sender: str, received: date) -> Document:
    """Read an invoice written directly in the email text (e.g. a freelancer's invoice)."""
    body = body.replace("\r\n", "\n")
    doc = Document(source="email body", text=body, from_email_body=True)
    doc.supplier_domain = sender.split("@")[-1].lower()
    if m := re.search(r"(?:Faktura|Invoice)\s*(?:nr\.?|no\.?)\s*(\S+)", body, re.I):
        doc.invoice_no = m.group(1)
    if "kr" in body.lower():
        doc.currency = "DKK"
    for label, amount in re.findall(r"^(.+?):\s*([\d.,]+)\s*kr\.?\s*$", body, re.M):
        value = parse_amount(amount)
        if re.match(r"moms|vat", label, re.I):
            doc.vat_amount = value
            if rm := re.search(r"(\d+)\s*%", label):
                doc.vat_rate = Decimal(rm.group(1))
        elif re.match(r"i alt|total", label, re.I):
            doc.total = value
        else:
            doc.lines.append(Line(description=label.strip(), amount=value))
    if doc.lines:
        doc.subtotal = sum((l.amount for l in doc.lines), Decimal("0"))
    if m := re.search(r"reg\.?\s*(\d{4})\s*konto\s*([\d ]*\d)", body, re.I):
        doc.bank_account = f"reg. {m.group(1)} konto {m.group(2).replace(' ', '')}"
    if m := re.search(r"senest\s+(\d{1,2})\.\s*([a-zæøå]+)", body, re.I):
        month = DANISH_MONTHS.get(m.group(2).lower())
        if month:
            doc.due_date = date(received.year, month, int(m.group(1))).isoformat()
    return doc
