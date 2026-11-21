# Approval rules (Finance)

1. Every supplier invoice is approved by the budget owner of its cost centre (see cost_centres.csv) before it is paid.
2. Invoices for goods with a purchase order (all factories, see `po_required` in vendors.csv) are checked against the PO:
   price per unit and quantity must match what we ordered and what the warehouse has received (`qty_received`).
   If they match, the invoice counts as approved by Operations. Any difference goes to the Head of Operations.
3. Invoices above DKK 50,000 excluding VAT (or the same in foreign currency) also need approval by the CEO.
4. A new supplier, or a supplier asking us to pay to a different bank account, is never paid before Finance has confirmed it
   by phone with a contact we already know. Never use contact details from the same email.
5. Receipts for things bought with a company card or paid privately are not supplier invoices. They go in the expense tool.
6. Only invoices addressed to New Works ApS can be posted.
