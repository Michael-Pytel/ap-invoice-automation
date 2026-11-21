"""The live demo: one Finance morning, from the invoice mailbox to posted invoices, in 7 steps.

Usage:
  python demo.py               run the demo, pausing after each part (press Enter to go on)
  python demo.py --step 5      start again at step 5 (resets BC and replays steps 1-4 quietly)
  python demo.py --direct      without n8n (same steps, if n8n breaks live)
  python demo.py --auto        no pauses (rehearsal / test)

Before: Docker Desktop running and `docker compose up -d` (n8n). The two Python services are started by this
script if they aren't running. Open http://localhost:8100 (Business Central) and http://localhost:5678 (n8n).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

from invoice_engine.httpjson import HttpError, call
from mailbox_sim.build import MAILBOX
from mailbox_sim.build import main as build_mailbox
from mailbox_sim.deliver import BC, RULES, WEBHOOK, deliver_and_print
from tools import check_expected
from invoice_engine.reference import ROOT

N8N = "http://localhost:5678"
# The order the steps deliver in. It must be the order the emails arrived (Q6); checked at start.
DEMO_ORDER = ["E03", "E06", "E15", "E01",            # step 2
              "E02", "E04", "E14", "E09",            # step 3
              "E08", "E07", "E11", "E10", "E05", "E12", "E13",   # step 4
              "E16"]                                 # step 6
os.system("")                                     # enable colours in the Windows terminal
BOLD, DIM, CYAN, YELLOW, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[33m", "\033[32m", "\033[31m", "\033[0m"


class Demo:
    def __init__(self, auto: bool, direct: bool, quiet: bool = False):
        self.auto, self.direct, self.quiet = auto, direct, quiet
        self.rules_proc = None

    # ------------------------------------------------------------ printing
    def out(self, text: str = "") -> None:
        if not self.quiet:
            print(text)

    def step(self, n: int, title: str, minutes: str) -> None:
        self.out(f"\n{BOLD}{'━' * 78}\n STEP {n}/7 · {title}{RESET}{DIM}   ({minutes}){RESET}\n{BOLD}{'━' * 78}{RESET}")

    def show(self, text: str) -> None:
        self.out(f"{YELLOW}  SHOW ▸ {text}{RESET}")

    def say(self, text: str) -> None:
        self.out(f"{CYAN}  SAY  ▸ {text}{RESET}")

    def pause(self, label: str = "Enter to continue") -> None:
        if not self.auto and not self.quiet:
            input(f"{DIM}  ⏎ {label} {RESET}")

    def deliver(self, *emails: str, width: int = 130) -> None:
        for e in emails:
            if self.quiet:
                from mailbox_sim.deliver import deliver
                try:
                    deliver(MAILBOX / f"{e}.eml", self.direct, WEBHOOK)
                except (HttpError, OSError):
                    pass                                  # the deliberate failure in step 6
            else:
                deliver_and_print(MAILBOX / f"{e}.eml", self.direct, WEBHOOK, width)

    # ------------------------------------------------------------ Business Central helpers
    def invoice_no(self, email_id: str) -> str:
        return next(d["purchase_invoice_no"] for d in call("GET", f"{BC}/incoming-documents") if d["email_id"] == email_id)

    def bc_action(self, email_id: str, action: str, **body) -> None:
        no = self.invoice_no(email_id)
        try:
            inv = call("POST", f"{BC}/purchase-invoices/{no}/{action}", body)
            self.out(f"  {GREEN}✓{RESET} {email_id} {no}: {action} by {body.get('by')} → {BOLD}{inv['status']}{RESET}"
                     + (f" as {inv['posted_no']}" if inv.get("posted_no") else ""))
        except HttpError as e:
            self.out(f"  {RED}✗ refused{RESET} {email_id} {no}: {e.message}")

    def summary(self) -> None:
        docs = call("GET", f"{BC}/incoming-documents")
        counts = {a: len({d["email_id"] for d in docs if d["action"] == a}) for a in ("post", "hold", "reject")}
        self.out(f"\n  {BOLD}{len({d['email_id'] for d in docs})} emails{RESET}: {GREEN}{counts['post']} post{RESET}, "
                 f"{YELLOW}{counts['hold']} hold{RESET}, {RED}{counts['reject']} reject{RESET}")

    # ------------------------------------------------------------ services
    def preflight(self) -> None:
        print(f"{BOLD}Pre-flight check{RESET}")
        for name, url, module in (("Business Central (simulated)", BC, "bc_sim.server"),
                                  ("Invoice check (rules)", RULES, "invoice_engine.service")):
            if not healthy(url):
                proc = start_service(module)
                if module == "invoice_engine.service":
                    self.rules_proc = proc
                wait_for(url)
            print(f"  {GREEN}✓{RESET} {name:30} {url}")
        if not self.direct:
            if not healthy(N8N, "/healthz"):
                sys.exit(f"  {RED}✗ n8n is not running.{RESET} Start Docker Desktop, then `docker compose up -d`. "
                         f"Or run with --direct.")
            print(f"  {GREEN}✓{RESET} {'n8n flow':30} {N8N}")
        if len(list(MAILBOX.glob("*.eml"))) < 16:
            build_mailbox()
        arrival_order = [f.stem for f in sorted(MAILBOX.glob("*.eml"), key=_arrival)]
        if DEMO_ORDER != arrival_order:
            sys.exit(f"  {RED}✗ The demo order {DEMO_ORDER} is not the arrival order {arrival_order} (Q6).{RESET}")
        print(f"  {GREEN}✓{RESET} {'Mailbox: 16 emails':30} {MAILBOX}")
        print(f"\n  Open {BOLD}http://localhost:8100{RESET} (Business Central) and {BOLD}{N8N}{RESET} (n8n) side by side.")

    def stop_rules(self) -> None:
        if self.rules_proc:
            self.rules_proc.terminate()
            self.rules_proc.wait()
        else:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { "
                            "$_.CommandLine -match 'invoice_engine.service' } | ForEach-Object { "
                            "Stop-Process -Id $_.ProcessId -Force }"], check=False, capture_output=True)
        for _ in range(20):
            if not healthy(RULES):
                return
            time.sleep(0.25)

    def start_rules(self) -> None:
        self.rules_proc = start_service("invoice_engine.service")
        wait_for(RULES)

    # ------------------------------------------------------------ the story
    def step1(self) -> None:
        self.step(1, "Monday morning: an empty Business Central", "0:30 + 0:30 intro")
        self.say("Today Finance opens every email, types the invoice, chooses account, VAT and cost centre by hand,")
        self.say("emails the PDF to the approver and chases them. Duplicates, wrong foreign VAT and late payments.")
        call("POST", f"{BC}/reset")
        self.out(f"  {GREEN}✓{RESET} Business Central reset: no invoices; suppliers, POs, accounts reloaded from the CSVs.")
        if not self.direct:
            self.show("The n8n flow: new email → invoice check → log in Business Central → route on post / hold / reject.")
        self.say("n8n only moves the email and follows the answer. Every rule is in plain Python, on this machine.")
        self.pause("Enter: the first email arrives")

    def step2(self) -> None:
        self.step(2, "Late October: invoices that just work", "1:30")
        self.deliver("E03")
        self.say("Latvian factory, in EUR. Price and quantity match the purchase order and what the warehouse received,")
        self.say("so it counts as approved by Operations (rule 2). It still waits for Finance to press post.")
        self.show("Business Central → Ready to post. Then the green run in n8n.")
        self.pause()
        self.deliver("E06", "E15", "E01")
        self.say("E06: Italian marble tops, a partial delivery: 18 invoiced, 18 received → also approved automatically.")
        self.say("E15: 48,000 SEK looks close to the CEO limit, but the check converts first: about DKK 32,600.")
        self.say("E01: Danish photographer, account 3010, VAT DK25, cost centre MKT → waits for the CMO.")
        self.show("Business Central → Waiting for approval: approver from the cost centre, the part Finance types today.")
        self.pause("Enter: end of October")

    def step3(self) -> None:
        self.step(3, "End of October: freight, a price difference, a receipt", "2:00")
        self.deliver("E02")
        self.say("The forwarder's invoice becomes 4 lines (freight, duty, import VAT, fee), each coded by a fixed table.")
        self.say("It notes that the goods invoice for this shipment hasn't arrived yet.")
        self.pause()
        self.deliver("E04")
        self.say("Here it is: 44.80 USD per lamp vs 41.50 on the PO → +396 USD, to the Head of Operations.")
        self.say("And it arrived 22 days after its invoice date, with 8 days left to pay: it sat with someone.")
        self.show("Business Central → On hold → E04.")
        self.pause()
        self.deliver("E14", "E09")
        self.say("E14 is a clean Danish invoice. E09 is a card receipt from a known supplier: already paid,")
        self.say("so it goes to the expense tool, not booked as a supplier invoice.")
        self.pause("Enter: early November, the hard ones")

    def step4(self) -> None:
        self.step(4, "Early November: the hard ones", "3:00")
        self.deliver("E08", "E07")
        self.say("E08: German VAT on a German invoice, which should be reverse charge. A deposit, above DKK 50,000:")
        self.say("CMO and CEO. E07: a credit note, linked to E06 from 24 Oct: deduct 620 EUR when paying it.")
        self.pause()
        self.deliver("E11")
        self.say("A supplier we don't know: never created automatically. The coding is only a suggestion.")
        self.pause()
        self.deliver("E10")
        self.say("Same supplier name as E03 and a perfect PO match, but: a new bank account, sent from meza-darbnica.co")
        self.say("instead of .lv, and 7 days to pay instead of 30. On hold: phone a contact we already know (rule 4).")
        self.say("This is the one that must never be paid on autopilot.")
        self.show("Business Central → On hold → E10: the reasons, in plain words.")
        self.pause()
        self.deliver("E05", "E12", "E13")
        self.say("E05: a reminder for E03 with the identical PDF: not booked twice. E12: a private card receipt.")
        self.say("E13: a statement is not an invoice, but a free check: E14 is on record, two invoices never reached us.")
        self.show("Business Central → All emails: what was not booked, and why. On hold is sorted by due date.")
        self.summary()
        self.say("15 of 16 in arrival order. E16 arrives last, in step 6.")
        self.pause("Enter: a person approves")

    def step5(self) -> None:
        self.step(5, "A person approves, Finance posts", "2:00")
        self.say("You can click these in Business Central instead; the page uses the same checks.")
        self.bc_action("E01", "approve", by="CMO")
        self.bc_action("E01", "post", by="Finance")
        self.pause()
        self.say("Can Finance post an invoice that is on hold? No:")
        self.bc_action("E04", "post", by="Finance")
        self.say("Can someone else approve the CMO's invoice? No:")
        self.bc_action("E15", "approve", by="Head of Sales")
        self.pause()
        self.say("E10: Finance phoned the contact we already have at Meža. Only then off hold, with a note:")
        self.bc_action("E10", "resolve", by="Finance",
                       note="Phoned our known contact at Meža (number from our records): new bank account confirmed.")
        self.bc_action("E10", "approve", by="Head of Operations")
        self.bc_action("E10", "approve", by="CEO")
        self.bc_action("E10", "post", by="Finance")
        self.show("Business Central → Posted → E10 → History: who did what, and the phone-call note.")
        self.pause("Enter: Monday morning goes wrong")

    def step6(self) -> None:
        self.step(6, "When something breaks", "1:00")
        self.stop_rules()
        self.out(f"  {RED}■{RESET} The invoice check service is stopped (simulating a crash).")
        self.deliver("E16")
        self.say("The flow stops, nothing is booked, the email is not lost. Who notices? The red run in n8n")
        self.say("(in production: Power Automate run history and a failure alert to Finance).")
        if not self.direct:
            self.show("n8n → Executions: the red run, and the step that failed.")
        self.pause("Enter: restart and deliver again")
        self.start_rules()
        self.out(f"  {GREEN}■{RESET} Service back.")
        self.deliver("E16")
        self.say("A freelancer's invoice written in the email: supplier and bank account check out, but it doesn't")
        self.say("name New Works ApS as the buyer (rule 6). On hold: ask her to resend it.")
        self.pause()
        self.check()
        self.pause("Enter: wrap-up")

    def check(self) -> None:
        with urllib.request.urlopen(f"{BC}/export/purchase-invoice-lines.csv", timeout=30) as r:
            text = r.read().decode("utf-8-sig")
        check_expected.OUTPUT.write_text("﻿" + text, encoding="utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
        self.summary()
        buf = io.StringIO()
        stdout, sys.stdout = sys.stdout, buf
        try:
            ok = check_expected.main() == 0
        finally:
            sys.stdout = stdout
        mark = f"{GREEN}✓" if ok else f"{RED}✗"
        self.out(f"  {mark} {buf.getvalue().strip()}{RESET}")
        self.out(f"  {GREEN}✓{RESET} {len(rows)} booking lines in the target format → {check_expected.OUTPUT.relative_to(ROOT)}")
        if not ok:
            raise SystemExit(1)
        backup = ROOT / "output" / "demo_backup"
        backup.mkdir(exist_ok=True)
        (backup / "purchase_invoice_lines.csv").write_text("﻿" + text, encoding="utf-8")
        for name, path in (("purchase_invoices", "/purchase-invoices"), ("incoming_documents", "/incoming-documents")):
            (backup / f"{name}.json").write_text(json.dumps(call("GET", BC + path), ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        self.out(f"  {GREEN}✓{RESET} Backup of this run saved in {backup.relative_to(ROOT)}")

    def step7(self) -> None:
        self.step(7, "Wrap-up", "0:30")
        self.say("AI is not used: every check is a bookkeeping rule that must give the same answer every time.")
        self.say("It could later help read unknown layouts, and only propose; the same checks decide.")
        self.say("Nothing leaves the building: PDFs are read here, n8n runs locally with telemetry off.")
        self.say("In production: Power Automate on invoice@ + Business Central approvals. The steps map one to one.")
        self.say("Build only our checks (tables Finance can edit); buy the mailbox, approvals, posting and payments.")
        self.out(f"\n{BOLD}  Demo complete.{RESET} Business Central stays open at http://localhost:8100 for questions.")


# ---------------------------------------------------------------- small helpers

def _arrival(path):
    from mailbox_sim.deliver import arrival
    return arrival(path)


def healthy(base: str, path: str = "/health") -> bool:
    try:
        with urllib.request.urlopen(base + path, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def start_service(module: str) -> subprocess.Popen:
    logs = ROOT / "output" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = open(logs / f"{module.split('.')[0]}.log", "a", encoding="utf-8")
    return subprocess.Popen([sys.executable, "-m", module, "--host", "0.0.0.0"], cwd=ROOT, stdout=log,
                            stderr=subprocess.STDOUT)


def wait_for(url: str, seconds: float = 15) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if healthy(url):
            return
        time.sleep(0.25)
    sys.exit(f"{RED}Service at {url} did not start; see output/logs/{RESET}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--step", type=int, default=1, help="start at this step (earlier steps replay quietly)")
    p.add_argument("--auto", action="store_true", help="no pauses")
    p.add_argument("--direct", action="store_true", help="without n8n")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    demo = Demo(args.auto, args.direct)
    demo.preflight()
    steps = [demo.step1, demo.step2, demo.step3, demo.step4, demo.step5, demo.step6, demo.step7]
    if args.step > 1:
        print(f"\n{DIM}  Replaying steps 1–{args.step - 1} quietly…{RESET}")
        demo.quiet = True
        for s in steps[:args.step - 1]:
            s()
        demo.quiet = False
    demo.pause("Enter to start" if args.step == 1 else f"Enter: step {args.step}")
    for s in steps[args.step - 1:]:
        s()


if __name__ == "__main__":
    main()
