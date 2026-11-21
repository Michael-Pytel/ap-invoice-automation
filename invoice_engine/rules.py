"""The bookkeeping rules: turn one email into purchase invoice lines with an action, confidence and flag.

Every decision here is plain code against the reference data. Assumption numbers (Q1–Q11) refer to
docs/questions.md; rule numbers refer to approval_rules.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import extract
from .extract import CENT, Document, Line
from .reference import EU_COUNTRIES, ReferenceData, Register, Registered

CEO_LIMIT = Decimal("50000")
CEO_MARGIN_FROM = Decimal("47500")      # within 5% of the limit -> CEO added to be safe (Q7)
CEO_MARGIN_TO = Decimal("52500")
DK_VAT = Decimal("0.25")
LATE_ARRIVAL_DAYS = 7                    # invoice date this many days before arrival -> "arrived late" (Q13)
DUE_SOON_DAYS = 10                       # due within this many days of arrival -> "due soon" (Q13)
COUNTRY_ADJECTIVE = {"DE": "German", "SE": "Swedish", "IT": "Italian", "LV": "Latvian", "LT": "Lithuanian",
                     "FR": "French", "NL": "Dutch", "PL": "Polish"}
REMINDER = re.compile(r"remind|påmind|rykker|mahnung|sollecito", re.I)
DEPOSIT = re.compile(r"anzahlung|deposit|depositum|acconto|forskud|advance payment", re.I)


@dataclass
class Email:
    email_id: str
    received_at: str
    sender: str
    to: str
    subject: str
    body: str
    attachments: list[Path] = field(default_factory=list)


@dataclass
class Coded:
    line: Line
    gl_account: str = ""
    cost_centre: str = ""
    vat_code: str = ""
    amount_excl_vat: Decimal = Decimal("0")
    vat_amount: Decimal = Decimal("0")
    po_number: str = ""
    confidence: str = "high"
    notes: list[str] = field(default_factory=list)


def money(x: Decimal | None) -> str:
    return "" if x is None else f"{x.quantize(CENT):.2f}"


def fmt(x: Decimal, currency: str = "") -> str:
    s = f"{x.quantize(CENT):,.2f}"
    return f"{s} {currency}".strip()


# ---------------------------------------------------------------- entry point

def process_email(email: Email, ref: ReferenceData, register: Register) -> list[dict]:
    return [row for _, rows in process_email_documents(email, ref, register) for row in rows]


def process_email_documents(email: Email, ref: ReferenceData, register: Register) -> list[tuple[Registered, list[dict]]]:
    """One result per document in the email: the record BC keeps (incoming document) and the invoice lines."""
    received = date.fromisoformat(email.received_at[:10])
    if email.attachments:
        docs = [extract.parse_pdf(p) for p in email.attachments]
    else:
        docs = [extract.parse_email_body(email.body, email.sender, received)]
    results = []
    for doc in docs:
        rows = process_document(email, doc, ref, register, received)
        results.append((register.added[-1], rows))
    return results


def email_action(rows: list[dict]) -> str:
    """One action for the whole email: hold wins over post, post over reject."""
    actions = {r["action"] for r in rows}
    return "hold" if "hold" in actions or not actions else ("post" if "post" in actions else "reject")


def classify(doc: Document) -> str:
    t = doc.text
    if re.search(r"\bKontoudtog\b|statement of account|Åbne poster", t, re.I):
        return "statement"
    if re.search(r"^(Receipt|Kvittering)\b", t, re.M | re.I) or doc.paid_note:
        return "receipt"
    if re.search(r"nota di credito|credit note|kreditnota|gutschrift", t, re.I) or (doc.total is not None and doc.total < 0):
        return "credit_note"
    if doc.invoice_no and doc.total is not None:
        return "invoice"
    return "unknown"


def country_of(vendor: dict | None, doc: Document) -> str:
    if vendor:
        return vendor["country"]
    if m := re.match(r"([A-Z]{2})\d", doc.supplier_vat):
        return m.group(1)
    if doc.supplier_vat.isdigit():          # CVR number without prefix: Danish
        return "DK"
    return ""


def process_document(email: Email, doc: Document, ref: ReferenceData, register: Register, received: date) -> list[dict]:
    sender_domain = email.sender.split("@")[-1].lower()
    internal = sender_domain == "newworks.dk"
    doc_type = classify(doc)
    vendor = ref.vendor_by_vat(doc.supplier_vat) or ref.vendor_by_domain(doc.supplier_domain)
    vendor_key = vendor["vendor_no"] if vendor else (doc.supplier_vat or doc.supplier_name)
    if doc.from_email_body and not doc.invoice_date:
        doc.invoice_date = received.isoformat()     # not stated: use the date received (medium confidence, Q9)

    base = {
        "email_id": email.email_id,
        "document_type": doc_type,
        "vendor_no": vendor["vendor_no"] if vendor else "",
        "vendor_name": vendor["name"] if vendor else doc.supplier_name,
        "vendor_invoice_no": doc.invoice_no,
        "invoice_date": doc.invoice_date,
        "due_date": doc.due_date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.due_date or "") else "",
        "currency": doc.currency,
    }

    def register_doc(action: str, document_type: str, po_quantities: dict | None = None) -> None:
        register.add(Registered(email.email_id, vendor_key, base["vendor_no"], doc.invoice_no, document_type, action,
                                doc.invoice_date, base["due_date"], doc.currency, doc.total, doc.fingerprint,
                                doc.po_numbers, doc.shipment, po_quantities or {}, email.received_at))

    if doc_type == "receipt":
        register_doc("reject", "receipt")
        return [summary_row(base, ref, "reject", receipt_reason(email, doc, vendor), f"Receipt {doc.invoice_no}".strip())]

    if doc_type == "statement":
        register_doc("reject", "statement")
        return [summary_row(base, ref, "reject", statement_reason(doc, vendor, vendor_key, register, ref),
                            f"Statement of open invoices {doc.invoice_date}")]

    if doc_type == "unknown":
        register_doc("hold", "unknown")
        return [summary_row(base, ref, "hold", "Could not read this document (unknown layout or language); enter it "
                            "by hand. Nothing has been guessed (Q12).", doc.source, confidence="low")]

    # ---------------- invoices and credit notes

    earlier = register.find_invoice(vendor_key, doc.invoice_no)
    if earlier:
        is_reminder = bool(REMINDER.search(f"{email.subject} {email.body}"))
        same_file = " The attached file is identical." if doc.fingerprint and doc.fingerprint == earlier.fingerprint else ""
        reason = (f"Duplicate: {doc.invoice_no} from {base['vendor_name']} was already received on "
                  f"{email_date(earlier)} ({earlier.email_id}).{same_file} "
                  f"Don't book or pay it again.")
        if is_reminder:
            reason += f" This is a payment reminder: reply with the planned payment date (due {earlier.due_date})."
        base["document_type"] = "reminder" if is_reminder else doc_type
        register_doc("reject", base["document_type"])
        return [summary_row(base, ref, "reject", reason, f"{doc.invoice_no} (already received)")]

    holds: list[str] = []
    infos: list[str] = []
    country = country_of(vendor, doc)
    foreign = country != "DK"

    # Supplier, sender and bank account (Q1, rule 4)
    if not vendor:
        holds.append("New supplier: not in our supplier list. Verify before creating it in Business Central: confirm "
                     "the order with the colleague who placed it, look up the company number (CVR/VIES), and confirm "
                     "the bank details by phone with a contact you already know (rule 4).")
    else:
        if vendor["vat_no"] and doc.supplier_vat and doc.supplier_vat != vendor["vat_no"]:
            holds.append(f"VAT number on the document ({doc.supplier_vat}) differs from the one we have on file "
                         f"({vendor['vat_no']}).")
        bank_ok = ref.bank_matches(vendor, doc.bank_account) if doc.bank_account else None
        if doc.bank_account and bank_ok is False:
            holds.append(f"Don't pay: the bank account on the invoice ({doc.bank_account}) differs from the one we "
                         f"have on file for {vendor['name']} ({vendor['bank_account']}). Phone a contact you already "
                         f"know at {vendor['name']} to confirm; never use contact details from this email (rule 4).")
        if not internal and sender_domain != vendor["email_domain"]:
            holds.append(f"Sent from {sender_domain}, but {vendor['name']} writes from {vendor['email_domain']}. "
                         f"Treat it as unverified until confirmed by phone (Q1).")
        terms = int(vendor["payment_terms_days"] or 0)
        if terms and doc.invoice_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.due_date or ""):
            days = (date.fromisoformat(doc.due_date) - date.fromisoformat(doc.invoice_date)).days
            if days != terms and doc_type == "invoice":
                holds.append(f"Payment term on the invoice is {days} days; our agreement with {vendor['name']} is "
                             f"{terms} days.")

    # Addressed to New Works ApS (rule 6, Q9 strict reading)
    if "newworksaps" not in re.sub(r"\s", "", doc.addressee.lower()):   # PDF text can lose spaces
        if doc.from_email_body:
            trusted = ""
            if vendor and doc.bank_account and ref.bank_matches(vendor, doc.bank_account):
                trusted = " Supplier and bank account match our records."
            due = f"; it's due {doc.due_date}" if doc.due_date else ""
            holds.append(f"Invoice written in the email doesn't name New Works ApS as the buyer (rule 6), and has no "
                         f"invoice date or VAT number.{trusted} Ask {vendor['name'] if vendor else 'the supplier'} "
                         f"to resend it addressed to New Works ApS{due} (Q9).")
        else:
            holds.append("The invoice isn't addressed to New Works ApS (rule 6).")

    if not doc.currency:
        holds.append("Currency not found on the document.")

    # Coding
    coded = [code_line(l, doc, vendor, country, ref) for l in doc.lines]
    if not coded:
        holds.append("No invoice lines could be read from the document.")
    po_quantities: dict = {}
    po_matched = False

    if doc_type == "credit_note":
        credit_note_checks(doc, coded, vendor_key, register, ref, holds)
    elif vendor and vendor["po_required"] == "yes":
        po_matched = po_checks(doc, coded, register, ref, holds, po_quantities)

    # Arithmetic
    arithmetic_checks(doc, holds)

    # VAT (Q8 for foreign VAT, Q11 for mixed lines)
    vat_checks(doc, coded, country, holds, base["vendor_name"])
    if foreign and doc.vat_amount and DEPOSIT.search(doc.text):
        infos.append("This is a deposit (advance payment) for something delivered later: Finance decides whether to "
                     "book it as a cost now or as a prepayment.")

    # Links between documents (forwarder <-> goods)
    if doc.shipment:
        for other in register.by_shipment(doc.shipment, vendor_key):
            infos.append(f"Info: same shipment {doc.shipment} as {other.document_type} {other.invoice_no} from "
                         f"{vendor_name(ref, other)} ({other.email_id}, action: {other.action}).")
    if vendor and vendor["po_required"] != "yes" and doc.po_numbers:
        for po in doc.po_numbers:
            po_lines = ref.po(po)
            supplier = vendor_name_by_no(ref, po_lines[0]["vendor_no"]) if po_lines else "unknown supplier"
            if not register.by_shipment(doc.shipment, vendor_key):
                infos.append(f"Info: costs for {po} ({supplier}); the goods invoice hasn't arrived yet.")

    # Arrived late or due soon (Q13): information only, never changes the action
    if doc_type == "invoice":
        infos += [n for n in timing_notes(doc, received) if not (doc.due_date and doc.due_date in " ".join(holds)
                                                                  and n.startswith("Due "))]

    # CEO limit (rule 3, Q7)
    ceo = False
    excl = sum((c.amount_excl_vat for c in coded if c.vat_code != "IMPORT-VAT"), Decimal("0"))
    dkk = ref.to_dkk(excl, doc.currency) if doc.currency else None
    if doc_type == "invoice" and dkk is not None:
        approx = f"≈ DKK {dkk.quantize(Decimal('1')):,}" if doc.currency != "DKK" else f"DKK {fmt(dkk)}"
        if dkk > CEO_MARGIN_TO:
            ceo = True
            infos.append(f"Above DKK 50,000 excl. VAT ({approx}): CEO approval needed too (rule 3).")
        elif dkk >= CEO_MARGIN_FROM:
            ceo = True
            infos.append(f"Close to the DKK 50,000 limit after currency conversion ({approx}): CEO approval added to "
                         f"be safe (Q7).")
    elif doc_type == "invoice" and doc.currency:
        holds.append(f"No exchange rate for {doc.currency}: can't check the CEO limit.")

    # Action
    low = [c for c in coded if c.confidence != "high"]
    if low and not holds:
        holds.append("Some fields could not be cross-checked against our records; check them before approving.")
    if doc_type == "credit_note" and not holds:
        holds.append("Credit note: confirm it before it's deducted.")
    action = "hold" if holds else "post"

    roles: list[str] = []
    for c in coded:
        role = ref.budget_owner(c.cost_centre)
        if role and role not in roles:
            roles.append(role)
    if action == "post" and po_matched:
        roles = ["Auto: PO match (rule 2)"]
        infos.insert(0, "Approved automatically: price and quantity match the purchase order and what was received "
                        "(rule 2).")
    if ceo and "CEO" not in roles:
        roles.append("CEO")
    approver = " + ".join(roles)

    register_doc(action, doc_type, po_quantities)

    reason = " ".join(holds + infos)
    rows = []
    for i, c in enumerate(coded, start=1):
        line_reason = " ".join(holds + c.notes + infos) if c.notes else reason
        rows.append({
            **base,
            "action": action,
            "line_no": str(i),
            "description": f"{c.line.item_no} {c.line.description}".strip(),
            "gl_account": c.gl_account,
            "cost_centre": c.cost_centre,
            "vat_code": c.vat_code,
            "amount_excl_vat": money(c.amount_excl_vat),
            "vat_amount": money(c.vat_amount),
            "po_number": c.po_number,
            "approver_role": approver,
            "confidence": c.confidence,
            "flag": "yes" if action != "post" else "no",
            "flag_reason": line_reason,
        })
    return rows


# ---------------------------------------------------------------- coding of one line

def code_line(line: Line, doc: Document, vendor: dict | None, country: str, ref: ReferenceData) -> Coded:
    c = Coded(line=line, amount_excl_vat=line.amount)
    if doc.from_email_body:
        c.confidence = "medium"
    eu = country in EU_COUNTRIES and country != "DK"

    if vendor and vendor["po_required"] == "yes":
        # Goods from a factory: account and cost centre come from the PO line (filled in by po_checks).
        c.gl_account, c.cost_centre = vendor["default_gl_account"], vendor["default_cost_centre"]
        c.vat_code = "DK25" if country == "DK" else ("EU-GOODS" if eu else "IMPORT")
    elif vendor and (rules := [r for r in ref.line_rules if r["vendor_no"] == vendor["vendor_no"]]):
        rule = next((r for r in rules if re.search(r["pattern"], line.description, re.I)), None)
        if rule:
            c.gl_account, c.vat_code, c.cost_centre = rule["gl_account"], rule["vat_code"], rule["cost_centre"]
        else:
            c.gl_account, c.cost_centre = vendor["default_gl_account"], vendor["default_cost_centre"]
            c.vat_code, c.confidence = "", "low"
            c.notes.append(f"Line '{line.description}' isn't in the table for this supplier: account and VAT code "
                           f"are only a suggestion.")
    elif vendor:
        c.gl_account, c.cost_centre = vendor["default_gl_account"], vendor["default_cost_centre"]
        c.vat_code = "DK25" if country == "DK" else ("EU-SERV" if eu else "NONEU-SERV")
    else:
        # Unknown supplier: suggestions only (Q3).
        c.confidence = "low"
        text = f"{doc.reference} {line.description}"
        for s in ref.suggestions:
            if re.search(s["pattern"], text, re.I):
                c.gl_account = c.gl_account or s["gl_account"]
                c.cost_centre = c.cost_centre or s["cost_centre"]
                if s["flag_reason"] and s["flag_reason"] not in c.notes:
                    c.notes.append(s["flag_reason"])
        c.vat_code = "DK25" if country == "DK" else ("EU-SERV" if eu else "")
        c.notes.insert(0, f"Suggested coding only (low confidence): G/L {c.gl_account or '?'}, cost centre "
                          f"{c.cost_centre or '?'}.")

    if c.vat_code == "DK25":
        c.vat_amount = (line.amount * DK_VAT).quantize(CENT)
    elif c.vat_code == "IMPORT-VAT":
        c.amount_excl_vat, c.vat_amount = Decimal("0"), line.amount   # the whole line is VAT (Q11)
    return c


# ---------------------------------------------------------------- checks

def po_checks(doc: Document, coded: list[Coded], register: Register, ref: ReferenceData, holds: list[str],
              po_quantities: dict) -> bool:
    """Rule 2 and Q5: price and quantity must match the PO and what was received. Returns True if all match."""
    if not doc.po_numbers:
        holds.append("No purchase order number on the invoice; goods from this supplier need a PO (rule 2).")
        return False
    po_lines = [l for po in doc.po_numbers for l in ref.po(po)]
    ok = True
    for c in coded:
        pl = next((l for l in po_lines if c.line.item_no and l["supplier_item_no"] == c.line.item_no), None)
        if not pl:
            holds.append(f"'{c.line.description}' is not on purchase order {', '.join(doc.po_numbers)}. Head of "
                         f"Operations to check (rule 2).")
            ok = False
            continue
        c.po_number, c.gl_account, c.cost_centre = pl["po_number"], pl["gl_account"], pl["cost_centre"]
        po_price, qty = Decimal(pl["unit_price"]), c.line.quantity or Decimal("0")
        if c.line.unit_price is not None and c.line.unit_price.quantize(CENT) != po_price.quantize(CENT):
            diff = c.line.unit_price - po_price
            pct = (diff / po_price * 100).quantize(Decimal("0.1"))
            holds.append(f"Unit price {fmt(c.line.unit_price, doc.currency)} vs PO {fmt(po_price, doc.currency)}: "
                         f"{'+' if diff > 0 else ''}{fmt(diff)} per unit, {'+' if diff > 0 else ''}"
                         f"{fmt(diff * qty, doc.currency)} in total ({'+' if diff > 0 else ''}{pct}%). Head of "
                         f"Operations: accept, or ask the supplier for a credit note (rule 2, Q5).")
            ok = False
        already = register.invoiced_quantity(pl["po_number"], pl["line"])
        received, ordered = Decimal(pl["qty_received"]), Decimal(pl["qty_ordered"])
        if qty + already > received or qty + already > ordered:
            holds.append(f"Quantity {qty} for '{c.line.description}' is more than received ({received}, ordered "
                         f"{ordered}, already invoiced {already}). Head of Operations to check (rule 2, Q5).")
            ok = False
        po_quantities[(pl["po_number"], pl["line"])] = qty
    return ok


def credit_note_checks(doc: Document, coded: list[Coded], vendor_key: str, register: Register, ref: ReferenceData,
                       holds: list[str]) -> None:
    """Q6: link the credit note to the invoice it corrects; always confirmed by a person."""
    m = re.search(r"(?:fattura|faktura|invoice|rechnung)\s+(?:n(?:r|o)\.?\s*)?([\w/.-]+)", doc.reference, re.I)
    ref_no = m.group(1) if m else ""
    linked = register.find_invoice(vendor_key, ref_no) if ref_no else None
    if linked:
        for c in coded:
            for po in linked.po_numbers:
                pl = next((l for l in ref.po(po) if c.line.item_no and l["supplier_item_no"] == c.line.item_no), None)
                if pl:
                    c.po_number, c.gl_account, c.cost_centre = pl["po_number"], pl["gl_account"], pl["cost_centre"]
        total = doc.total if doc.total is not None else sum((c.amount_excl_vat for c in coded), Decimal("0"))
        due = f" (due {linked.due_date})" if linked.due_date else ""
        holds.append(f"Credit note of {fmt(total, doc.currency)} on invoice {ref_no}{due}, booked in {linked.email_id}. "
                     f"Head of Operations: confirm with the warehouse that the goods were damaged or returned, and "
                     f"check that the PO's received quantity reflects it. Then deduct {fmt(abs(total), doc.currency)} "
                     f"when paying {ref_no} (Q6).")
    else:
        holds.append(f"Credit note refers to invoice {ref_no or '(not stated)'}, which we have no record of. Find the "
                     f"original invoice before booking it.")


def timing_notes(doc: Document, received: date) -> list[str]:
    """Q13: say when an invoice arrived long after its date, or must be dealt with soon."""
    notes = []
    iso = r"\d{4}-\d{2}-\d{2}"
    if re.fullmatch(iso, doc.invoice_date or ""):
        late = (received - date.fromisoformat(doc.invoice_date)).days
        if late > LATE_ARRIVAL_DAYS:
            notes.append(f"Dated {doc.invoice_date} but arrived {received.isoformat()} ({late} days later): it may "
                         f"have gone to someone else first. Check it wasn't already received or paid (Q13).")
    if re.fullmatch(iso, doc.due_date or ""):
        left = (date.fromisoformat(doc.due_date) - received).days
        if left < 0:
            notes.append(f"Already overdue since {doc.due_date} when it arrived (Q13).")
        elif left <= DUE_SOON_DAYS:
            notes.append(f"Due {doc.due_date} (in {left} days): resolve before the next payment run (Q13).")
    return notes


def arithmetic_checks(doc: Document, holds: list[str]) -> None:
    problems = []
    for l in doc.lines:
        if l.quantity is not None and l.unit_price is not None and abs(l.quantity * l.unit_price - l.amount) > CENT:
            problems.append(f"'{l.description}': {l.quantity} × {l.unit_price} ≠ {l.amount}")
    lines_sum = sum((l.amount for l in doc.lines), Decimal("0"))
    if doc.subtotal is not None and abs(lines_sum - doc.subtotal) > CENT:
        problems.append(f"lines add up to {fmt(lines_sum)}, subtotal says {fmt(doc.subtotal)}")
    if doc.subtotal is not None and doc.total is not None:
        if abs(doc.subtotal + (doc.vat_amount or Decimal("0")) - doc.total) > CENT:
            problems.append(f"subtotal + VAT ≠ total {fmt(doc.total)}")
    if problems:
        holds.append("Amounts don't add up: " + "; ".join(problems) + ".")


def vat_checks(doc: Document, coded: list[Coded], country: str, holds: list[str], supplier: str) -> None:
    doc_vat = doc.vat_amount or Decimal("0")
    if country and country != "DK":
        if doc_vat != 0:
            adjective = COUNTRY_ADJECTIVE.get(country, "Foreign")
            rate = f" {doc.vat_rate}%" if doc.vat_rate else ""
            holds.append(f"{adjective} VAT{rate} ({fmt(doc_vat, doc.currency)}) is on the invoice, but invoices from "
                         f"suppliers outside Denmark are normally without VAT (reverse charge). Don't book it as "
                         f"deductible VAT. Ask {supplier} for a corrected invoice before paying, or confirm with the "
                         f"CFO why foreign VAT applies (Q8).")
            for c in coded:
                c.confidence = "low"
        return
    if country == "DK":
        expected = sum((c.vat_amount for c in coded if c.vat_code == "DK25"), Decimal("0"))
        if abs(expected - doc_vat) > Decimal("0.02"):
            holds.append(f"VAT on the invoice ({fmt(doc_vat, doc.currency)}) doesn't match 25% of the lines that "
                         f"carry Danish VAT ({fmt(expected, doc.currency)}).")


# ---------------------------------------------------------------- receipts and statements

def receipt_reason(email: Email, doc: Document, vendor: dict | None) -> str:
    card = re.search(r"card ending\s+(\d{4})|\*{4}(\d{4})", doc.text)
    card_txt = f" ending {card.group(1) or card.group(2)}" if card else ""
    private = bool(re.search(r"eget kort|own card|privat", email.body, re.I))
    total = f"{fmt(doc.total, doc.currency)}, " if doc.total is not None else ""
    name = vendor["name"] if vendor else doc.supplier_name
    if private:
        text = (f"Receipt from {name} ({total}paid with a private card{card_txt}): not a supplier invoice (rule 5). "
                f"Send it to the expense tool for reimbursement.")
    else:
        text = (f"Receipt from {name} ({total}already paid by card{card_txt}): not a supplier invoice (rule 5). "
                f"Send it to the expense tool; don't book it as a supplier invoice, or it could be paid twice (Q4).")
    if vendor and vendor["country"] != "DK":
        code = "EU-SERV" if vendor["country"] in EU_COUNTRIES else "NONEU-SERV"
        text += (f" When it's booked, remember reverse charge VAT ({code}): it's a service from a supplier outside "
                 f"Denmark.")
    return text


def statement_reason(doc: Document, vendor: dict | None, vendor_key: str, register: Register,
                     ref: ReferenceData) -> str:
    name = vendor["name"] if vendor else doc.supplier_name
    found, missing, mismatched = [], [], []
    for row in doc.statement_rows:
        rec = register.find_invoice(vendor_key, row.invoice_no)
        if not rec:
            overdue = f", overdue since {row.due_date}" if row.due_date < doc.invoice_date else f", due {row.due_date}"
            missing.append(f"{row.invoice_no} ({fmt(row.amount_incl_vat, doc.currency)}{overdue})")
        elif rec.total is not None and abs(rec.total - row.amount_incl_vat) > CENT:
            mismatched.append(f"{row.invoice_no}: statement says {fmt(row.amount_incl_vat)}, we have {fmt(rec.total)}")
        else:
            found.append(f"{row.invoice_no} ({rec.email_id})")
    text = f"Statement from {name}, not an invoice: nothing to book, and don't pay from it (Q10)."
    if missing:
        text += (f" It lists {len(missing)} invoice{'s' if len(missing) > 1 else ''} we have no record of: "
                 f"{'; '.join(missing)}. Check in Business Central whether they were received or paid; if not, ask "
                 f"{name} for copies.")
    if mismatched:
        text += f" Amounts differ: {'; '.join(mismatched)}."
    if found:
        text += f" On record and matching: {', '.join(found)}."
    if vendor and doc.bank_account and ref.bank_matches(vendor, doc.bank_account) is False:
        text += f" The bank account on the statement differs from the one on file: don't use it (rule 4)."
    return text


def summary_row(base: dict, ref: ReferenceData, action: str, reason: str, description: str,
                confidence: str = "high") -> dict:
    return {**base, "action": action, "line_no": "1", "description": description, "gl_account": "",
            "cost_centre": "", "vat_code": "", "amount_excl_vat": "", "vat_amount": "", "po_number": "",
            "approver_role": "", "confidence": confidence, "flag": "yes", "flag_reason": reason}


# ---------------------------------------------------------------- small helpers

def vendor_name_by_no(ref: ReferenceData, vendor_no: str) -> str:
    return next((v["name"] for v in ref.vendors if v["vendor_no"] == vendor_no), vendor_no)


def vendor_name(ref: ReferenceData, rec: Registered) -> str:
    return vendor_name_by_no(ref, rec.vendor_no) if rec.vendor_no else rec.vendor_key


def email_date(rec: Registered) -> str:
    return rec.received_at[:10]
