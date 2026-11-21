# Questions for Finance, and the assumptions made instead

I could not talk to Finance during the case week. Below are the questions I would have asked, and the assumption
the solution uses in place of each answer. The code follows these assumptions only; each one is referenced in the
code and in the flag reasons as (Q1)–(Q13). If Finance answers differently, the change is in one place.

| # | Question, in short | What the tool assumes | Emails |
|---|---|---|---|
| [Q1](#q1-checking-that-a-new-supplier-is-legitimate) | Is a new supplier checked before it's created? | Never creates suppliers; new supplier → hold; "known" = VAT no. **and** email domain match | E10, E11 |
| [Q2](#q2-the-existing-document-capture-tool) | Why is the capture tool not enough? | Build doesn't depend on it; check it first before building more reading | E01–E15 |
| [Q3](#q3-which-cost-centre-pays-for-the-showroom) | Showroom: SALES or ADMIN? | Suggest 4050 / ADMIN, low confidence; a person decides | E11 |
| [Q4](#q4-card-receipts-from-known-suppliers) | Card receipts from known suppliers? | Not a supplier invoice: reject → expense tool | E09, E12 |
| [Q5](#q5-invoice-price-differs-from-the-purchase-order) | Any tolerance on PO price? | None (to the cent); difference → hold, Head of Operations | E04 |
| [Q6](#q6-booking-po-matched-goods-invoices-with-no-one-looking) | May PO-matched invoices pass with no one looking? | Yes, only if every other check passes too | E03, E06, E07, E10 |
| [Q7](#q7-exchange-rate-for-the-dkk-50000-ceo-limit) | Which rate for the DKK 50,000 CEO limit? | BC rate on invoice date; 5% safety margin | foreign invoices |
| [Q8](#q8-foreign-vat-on-an-invoice-from-an-eu-supplier-deposit-invoice) | Foreign VAT on an invoice? | Hold; never booked as deductible VAT | E08 |
| [Q9](#q9-invoice-written-in-the-email-text-with-no-pdf) | Invoice in the email text, no addressee? | Read it; hold under rule 6 (strict reading) | E16 |
| [Q10](#q10-supplier-statements-that-list-invoices-we-never-received) | Statements with invoices we never got? | Reject (not booked), but used as a check | E13, E14 |
| [Q11](#q11-forwarder-invoices-with-mixed-lines-freight-duty-import-vat-fees) | Forwarder invoice with mixed lines? | Split into 4 lines by a fixed table | E02, E04 |
| [Q12](#q12-invoices-in-other-languages) | Unknown languages? | Never guessed: hold; flag reasons in English | all |
| [Q13](#q13-invoices-that-arrive-late-or-are-due-soon) | Warn about late arrival or close due dates? | Yes, information only: >7 days late, due within 10 days | E04, E10, E11 |

---

## Q1. Checking that a new supplier is legitimate
**Question:** When you check whether the supplier exists in Business Central and it doesn't, do you check
anywhere that it is a legitimate supplier before you create it?

**Proposed assumption:**
- Today there is no such check: Finance's own description says "If not, we create it."
- The tool **never creates a supplier**. An invoice from a supplier not in `vendors.csv` is put on **hold** with
  the reason "New supplier: verify before creating it in Business Central."
- A supplier counts as **known** only if the invoice's VAT number **and** the sender's email domain match
  `vendors.csv`. A matching name alone is not enough, because a fake invoice can copy a real name.
- Before creating the supplier, Finance (outside the tool):
  1. Confirms with the colleague who ordered the work that the order is real.
  2. Looks up the company number in the official register (CVR for Danish companies, the EU VIES check for EU VAT numbers).
  3. Confirms the bank details by phone with a contact we already know, never using contact details from the same email (approval rule 4).

**Affects:** E11 (Kbh. Montage & Transport: not in vendors.csv), E10 (name matches Meža, but the sender domain
is `.co` instead of `.lv` and the bank account differs, so it is not treated as the known supplier).

**Status:** APPROVED

---

## Q2. The existing document capture tool
**Question:** Which document capture tool do you use today, and why do you still type or correct most of the
fields? Is it bad at reading the PDFs, or is the problem something else?

**Proposed assumption:**
- The tool's name and setup are unknown. Finance's description says it "reads some fields, but we type or correct
  most of it", and that G/L account, VAT code and cost centre are always chosen by hand.
- We assume the weak points are:
  1. **Reading:** it handles the header (invoice number, date, total) reasonably, but struggles with line items,
     the many layouts and the five languages.
  2. **Coding:** it doesn't know New Works' own rules (which account, VAT code, cost centre and approver), so that
     part is always manual. This is the bigger gap, and it's where our rules add value.
- Our build does **not** depend on that tool. It reads the PDF text itself, locally (all 15 PDFs have a text
  layer, so no OCR is needed), the same way New Works' own Python scripts already read PDFs.
- In the note we recommend **checking the existing tool first** (vendor templates, a Business Central
  integration, an upgrade) before building more reading logic. Our own build focuses on the coding rules and checks.

**Affects:** all emails with a PDF (E01–E15), and the build-or-buy section of the note.

**Status:** APPROVED

---

## Q3. Which cost centre pays for the showroom?
**Question:** Is the showroom at Bredgade 42 a Sales cost or an Admin cost? There is no showroom or facilities
cost centre, and Kbh. Montage's invoice for installing shelving in the showroom could go to either.

**Proposed assumption:**
- The cost centre decides who approves (SALES → Head of Sales, ADMIN → CEO), so a wrong guess sends the invoice
  to the wrong person. The tool therefore does **not** decide it.
- For a supplier that is **not** in `vendors.csv`, there is no default account or cost centre. The tool fills in
  a **suggestion** only, marked low confidence: G/L **4050** (Small equipment and installation) from the invoice
  text, and cost centre **ADMIN** (same as premises costs such as rent), with the flag "Check cost centre:
  showroom could be SALES or ADMIN."
- The invoice is on **hold** anyway because the supplier is new (Q1), so Finance confirms the supplier and the
  cost centre in the same check. Nothing is posted on the suggestion.

**Affects:** E11 (Kbh. Montage & Transport).

**Status:** APPROVED

---

## Q4. Card receipts from known suppliers
**Question:** Some suppliers we know are paid by card (Planform, see vendors.csv). When their receipt arrives in
the invoice mailbox, where should it go?

**Proposed assumption:**
- A receipt is already paid, so it is **not a supplier invoice** (approval rule 5), even when the supplier is in
  `vendors.csv`. Booking it as an invoice would create a debt that has already been paid, and it could be paid twice.
- Action **reject** (not booked as a supplier invoice), with the reason: "Already paid by card (card ending 4417):
  send to the expense tool, not to supplier invoices." The supplier is still recognised (V1007), so Finance sees
  where it came from. No booking coding is filled in on rejected lines.
- The reason also notes that Planform is a US software service, so **reverse charge VAT** (NONEU-SERV) still has to
  be declared when it's booked in the expense tool. That detail is easy to miss outside the invoice flow.
- We assume card ending 4417 is a company card. If it were a private card, it would still go to the expense tool
  (for reimbursement), so the action is the same.

**Affects:** E09 (Planform receipt). Same route as E12 (restaurant receipt, private card), which comes from an unknown supplier.

**Status:** APPROVED

---

## Q5. Invoice price differs from the purchase order
**Question:** When an invoice's unit price doesn't match the purchase order, is any difference allowed (a tolerance),
or should every difference be stopped? (E04: invoice says 44.80 USD per lamp, the PO says 41.50 USD.)

**Proposed assumption:**
- **No tolerance.** Approval rule 2 says price and quantity "must match" and "any difference goes to the Head of
  Operations". The only allowance is rounding to the cent.
- The line is booked at the **invoiced** amount (the tool never changes what the supplier billed), but the action
  is **hold**, approver **Head of Operations**, with the difference spelled out: "Unit price 44.80 USD vs PO 41.50 USD:
  +3.30 per unit, +396.00 USD in total (+8.0%). Accept, or ask the supplier for a credit note."
- Quantity is checked the same way: invoiced quantity must not exceed what was ordered **or** what the warehouse
  has received (`qty_received`).
- If price and quantity match, the line counts as approved by Operations (rule 2).

**Affects:** E04 (price difference). Matches cleanly: E03, E06. E10 matches PO-075, but is held for the bank account anyway.

**Status:** APPROVED

---

## Q6. Booking PO-matched goods invoices with no one looking
**Question:** Can goods invoices that match the purchase order be booked with no one looking (approval rule 2),
and what if a credit note for the same delivery is still pending?

**Proposed assumption:**
- **Yes, but only when every other check also passes.** A goods invoice is approved automatically
  ("approved by Operations, PO match, rule 2") only if:
  1. unit price and quantity match the PO, and quantity is within what the warehouse has received;
  2. the supplier is known, the sender domain matches, and the bank account is the one on file;
  3. it is not a duplicate and is addressed to New Works ApS;
  4. the totals add up and the VAT code is certain;
  5. it is under DKK 50,000 excluding VAT after currency conversion (otherwise the CEO must approve, rule 3).
  If any one fails, the invoice goes on hold. E10 is the reason for this: it matches its PO perfectly, but the bank account is not ours.
- Emails are processed in the order they arrived. An invoice is judged on what is known when it arrives.
- **A credit note does not stop the invoice it corrects.** E06 matches the 18 tops received, so it is approved
  automatically. When E07 arrives later, it is linked to E06 and goes on **hold** for the Head of Operations,
  with the reason: "Credit note for 2 damaged tops on invoice 214/2026 (due 23 Dec, not yet paid). Confirm the damage
  with the warehouse; then deduct 620 EUR when paying 214/2026."
- The flag also asks Operations to update what was received: the supplier says the remaining 12 tops follow in
  December, so the 2 damaged tops may not be replaced.
- Nothing is ever paid by the tool, so there is no risk of paying E06 in full before E07 is settled, as long as
  Finance deducts it at payment time.

**Affects:** E03 and E06 (approved automatically), E07 (hold, linked to E06), E10 (matches its PO but held for the bank account).

**Status:** APPROVED

---

## Q7. Exchange rate for the DKK 50,000 CEO limit
**Question:** Which exchange rate should be used to check whether a foreign-currency invoice is above the
DKK 50,000 CEO limit (approval rule 3)?

**Proposed assumption:**
- In production: the **exchange rate already in Business Central** for that currency on the **invoice date**
  (BC keeps a currency exchange rate table, normally fed from Danmarks Nationalbank's official rates). One source
  of truth, so the approval check uses the same rate as the booking.
- In the demo: a small fixed table, `rates.csv`, standing in for BC's table. No live lookup, so the demo works
  offline and gives the same result every run. Rates are illustrative and labelled as such:
  EUR 7.46 (the krone is pegged to the euro), USD 6.60, SEK 0.68.
- The limit is checked on the invoice total **excluding VAT** (rule 3). Credit notes never trigger CEO approval.
- **Safety margin:** if the converted amount is within 5% of the limit (DKK 47,500–52,500), the invoice is treated
  as above it and the CEO is added, with the reason "Close to the DKK 50,000 limit after currency conversion:
  CEO approval added to be safe." When in doubt, more approval, not less.
- With these rates: E08 (24,000 EUR ≈ DKK 179,000) and E10 (7,480 EUR ≈ DKK 55,800) need the CEO.
  E03 (5,920 EUR ≈ DKK 44,200), E06 (5,580 EUR ≈ DKK 41,600), E04 (5,376 USD ≈ DKK 35,500) and
  E15 (48,000 SEK ≈ DKK 32,600) do not.

**Affects:** E03, E04, E06, E08, E10, E15 (all foreign-currency invoices). E15 is the trap: 48,000 looks like it's
under 50,000, but only because the number isn't in DKK. The check must always convert first.

**Status:** APPROVED

---

## Q8. Foreign VAT on an invoice from an EU supplier (deposit invoice)
**Question:** Messebau Krämer (Germany) has charged 19% German VAT on its deposit invoice for the Paris trade fair
stand. How should we handle foreign VAT on an invoice, and how do you book a deposit for an event that happens later?

**Proposed assumption:**
- **General rule in the tool:** an invoice from a supplier outside Denmark should show **no VAT** (reverse charge:
  EU-GOODS, EU-SERV, NONEU-SERV or IMPORT in vat_codes.csv). If a foreign invoice **does** show VAT, the invoice
  goes on **hold**. The tool doesn't try to decide the VAT law; it stops and says what it saw.
- German VAT cannot be reclaimed in the Danish VAT return, so the 4,560 EUR is **never booked as deductible input VAT**.
- For E08 the lines are proposed as on the invoice, excluding VAT: stand build 21,500 EUR + transport and storage
  2,500 EUR, G/L **3020** (trade fairs), cost centre **MKT**, VAT code **EU-SERV**, marked low confidence. Reason:
  "German VAT 19% (4,560 EUR) is on the invoice, but services from an EU supplier are normally invoiced without
  VAT (reverse charge). Ask Messebau Krämer for a corrected invoice before paying, or confirm with the CFO why German VAT applies."
- **Deposit:** this is a 50% advance for a fair in January 2027. Whether it is booked as a cost now or as a
  prepayment until the fair is Finance's decision; the chart of accounts has no prepayment account. The flag says
  so; the tool doesn't choose.
- Approvers: **CMO** (cost centre MKT) **and CEO** (24,000 EUR ≈ DKK 179,000, above the limit, Q7).

**Affects:** E08 (Messebau Krämer). The same rule confirms that E03, E06 and E15 are fine: foreign invoices with 0
VAT and a reverse charge note.

**Status:** APPROVED

---

## Q9. Invoice written in the email text, with no PDF
**Question:** Do you accept an invoice written directly in the email (no PDF), when it has no invoice date, no
addressee and no VAT number? (E16, the freelance copywriter.)

**Proposed assumption:**
- What the rules say: nothing about email-body invoices, dates or VAT numbers. Finance's own description treats
  them as normal ("Most have a PDF, some have the invoice in the mail text"), and so does the brief. The only rule
  that applies is **rule 6: only invoices addressed to New Works ApS can be posted.**
- The tool **reads email-body invoices** just like PDFs (the email text is the document). Fields read from the text
  that can't be cross-checked are marked **medium** confidence.
- What checks out for E16: the supplier is known (Tekstværkstedet v/ Anna Holt, V1011), the sender domain matches,
  the bank account (reg. 0040, account 6655 44) matches the one on file, 9,000 + 25% VAT 2,250 = 11,250 adds up,
  and the due date (16 Nov) fits the supplier's 8-day terms. So there is **no sign of fraud**.
- **The reason to hold is rule 6:** the email was sent to invoice@newworks.dk, but the text never names
  New Works ApS as the buyer. Read strictly, it is not "addressed to New Works ApS".
- Also noted, but not the reason for the hold (it comes from Danish invoicing law, not New Works' rules): no invoice
  date and no VAT/CVR number, which may matter for deducting the 2,250 DKK VAT.
- Action **hold**. Proposed coding: G/L **3030** (print and copywriting), cost centre **MKT**, VAT **DK25**,
  approver **CMO**, invoice no. 7, invoice date = date received (8 Nov), marked medium confidence. Reason:
  "Invoice written in the email doesn't name New Works ApS as the buyer (rule 6), and has no invoice date or VAT number.
  Supplier and bank account match our records. Ask Anna to resend it addressed to New Works ApS; it's due 16 Nov."
- The hold is about the document, not about trust. Once it is addressed correctly, it goes through as a normal post.

**Affects:** E16 (Tekstværkstedet).

**Status:** APPROVED (strict reading of rule 6)

---

## Q10. Supplier statements that list invoices we never received
**Question:** When a supplier sends a statement of open invoices, what should happen with it, and what if it lists
invoices that never reached the invoice mailbox? (E13: Emballagefabrikken Fyn lists EF-55102, EF-55140 and EF-55187.)

**Proposed assumption:**
- What the rules say: nothing about statements. Finance's description only says "Reminders and statements also land here."
- A statement is **not an invoice**: there is nothing to book from it, and paying from a statement risks paying
  twice and booking VAT without a valid invoice. Action **reject** (not booked), document type `statement`.
- But it is **used as a check**: every invoice on the statement is compared with what we have on record
  (simulated BC: invoices already booked or received).
  - EF-55187 (15,425.00 DKK) = E14. Found, and the amount matches.
  - EF-55102 (9,862.50 DKK, due 29 Oct, **overdue**) and EF-55140 (4,125.00 DKK, due 13 Nov): **not found**.
- Flag on the statement: "The statement lists 2 invoices we have no record of: EF-55102 (9,862.50 DKK, overdue since
  29 Oct) and EF-55140 (4,125.00 DKK, due 13 Nov). Check in Business Central whether they were received or paid; if not,
  ask Emballagefabrikken for copies. Don't pay from the statement."
- The case data has no history of earlier invoices, so the tool can't know whether they were booked before. It flags; it doesn't guess.
  They may also have gone to a colleague directly (Finance mentions this happens).
- The statement's bank account matches the one on file, so there's no bank warning.

**Affects:** E13 (statement), E14 (matched against it).

**Status:** APPROVED (action stays reject, with flag)

---

## Q11. Forwarder invoices with mixed lines (freight, duty, import VAT, fees)
**Question:** The freight forwarder's invoice mixes freight, customs duty and import VAT paid on our behalf, and its
own fee. How should each line be booked, and should it be linked to the goods invoice for the same shipment?
(E02 Nordhavn 88213, shipment NHS-SEA-55190, which is the Brightway goods on PO-064, E04.)

**Proposed assumption:**
- One invoice becomes **four booking lines**, each coded by a fixed table of the forwarder's line texts (rules, not AI),
  all with cost centre **OPS** (vendor default):

  | Line on the invoice | G/L | VAT code | Excl. VAT | VAT |
  |---|---|---|---|---|
  | Søfragt Ningbo – Aarhus (sea freight) | 2050 Freight in | NOVAT (international freight) | 14,200.00 | 0.00 |
  | Told, udlæg (customs duty, paid on our behalf) | 2060 Customs duty | NOVAT | 3,180.00 | 0.00 |
  | Importmoms, udlæg (import VAT, paid on our behalf) | 6820 Import VAT | IMPORT-VAT (the whole line is VAT) | 0.00 | 9,870.00 |
  | Terminalhåndtering og fortoldning (terminal and clearance fee) | 2050 Freight in | DK25 | 1,450.00 | 362.50 |

- **VAT check:** the invoice's 25% VAT (362.50) must equal 25% of the fee line only (1,450.00). It does. If the VAT
  didn't match exactly one combination of lines, the invoice would go on hold. The lines must also add up to the total
  (28,700.00 + 362.50 = 29,062.50).
- **Import VAT** is booked as all VAT (excl. VAT 0.00, VAT 9,870.00) on 6820, as vat_codes.csv says: "The whole line is
  VAT. Post to 6820, deductible."
- **Link to the goods:** the invoice is linked to PO-064 / Brightway BW-26-0931 by its shipment number and PO reference,
  for information only. Reason shown: "Freight and import costs for shipment NHS-SEA-55190 (Brightway, PO-064; the goods
  invoice is on hold for a price difference)." The forwarder's invoice is valid on its own, so the goods dispute doesn't stop it.
- A line text that isn't in the table gets a **low-confidence suggestion** and the invoice goes on hold.
- Action **post**, one click by the **Head of Operations** (DKK 28,700 excl. VAT, under the CEO limit). No PO is needed
  for the forwarder (`po_required` = no).

**Affects:** E02 (Nordhavn), linked to E04 (Brightway).

**Status:** APPROVED

> Note from the build: emails are processed in the order they arrived (Q6), and E02 (30 Oct) arrives before E04
> (31 Oct). So E02 says "costs for NW-PO-2026-064 (Ningbo Brightway); the goods invoice hasn't arrived yet", and E04
> refers back to E02 by its shipment number. The coding is exactly as above.

---

## Q12. Invoices in other languages
**Question:** Invoices arrive in Danish, English, Italian, German and Swedish today. Which other languages or
countries do suppliers send from, and what should happen when an invoice comes in a language the tool doesn't know?
And which language should the flag reasons be written in for Finance?

**Proposed assumption:**
- The tool reads the five languages in the case by their labels (e.g. "Fakturanr.", "Numero", "Rechnungsnr.",
  "Invoice no."). Adding a language means adding its labels to the reading rules; nothing else changes.
- **An unknown language never leads to a guess.** If the tool can't find the invoice number, total or lines, the
  document goes on **hold** with "Could not read this document; enter it by hand", or "No invoice lines could be read".
- **The arithmetic check is the safety net:** lines × price, lines vs. subtotal, and subtotal + VAT vs. total must add up.
  If a number was misread (for example "1,234" meaning one thousand in one country and 1.234 in another), the totals
  won't match and the invoice goes on hold.
- Supplier, bank account, PO and VAT checks work the same in every language, because they compare numbers and codes
  (VAT numbers, IBANs, PO numbers, item numbers), not words.
- **Optional AI fallback** (only if new languages become common): an AI model reads an unknown layout and proposes
  the fields; the same code checks then decide, and anything that doesn't add up is held. It would use a local model
  or a business API without data retention, with bank details masked, so invoice data doesn't leave the building
  unprotected. Not used in the case, because all five languages are read reliably by rules.
- Line descriptions are kept in the supplier's language (that's what the invoice says). Flag reasons are written in
  **English**, like the brief; switching them to Danish would be a text change only.

**Affects:** all emails (E01–E16). Today: Danish (E01, E02, E11, E12, E13, E14, E16), English (E03, E04, E05, E09, E10),
Italian (E06, E07), German (E08), Swedish (E15).

**Status:** APPROVED (flag reasons in English; descriptions kept as on the invoice)

---

## Q13. Invoices that arrive late or are due soon
**Question:** Finance says invoices get paid late "because the invoice sat in someone's mail". Should the tool warn
when an invoice arrives long after its invoice date, or is due within a few days? (E04: dated 9 Oct, arrived 31 Oct,
due 8 Nov.)

**Proposed assumption:**
- **Information only: it never changes the action.** A late or urgent invoice is not more or less trustworthy; it
  just needs to be dealt with sooner. The checks still decide post, hold or reject.
- **Arrived late:** invoice date more than **7 days** before it reached invoice@ → "Dated 9 Oct but arrived 31 Oct
  (22 days later): it may have gone to someone else first. Check it wasn't already received or paid."
- **Due soon:** due date within **10 days** of arrival (payment runs are twice a week, so that leaves about
  two runs) → "Due 8 Nov (in 8 days): resolve before the next payment run." Already overdue on arrival → "Already
  overdue since …".
- The review view sorts holds by due date, so the most urgent problem is at the top.
- Reminders and statements already show due and overdue dates (E05, E13).

**Affects:** E04 (arrived 22 days late, due in 8 days, on hold). Due soon: E10 (7 days) and E11 (8 days), both on
hold. E16 (8 days) already names its due date in its hold reason, so no second note. E01, E08 and the others have
14 days or more.

**Status:** APPROVED (sorting holds by due date applies to the review view, not built yet)
