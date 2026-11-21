"""Read master data and earlier documents from the (simulated) Business Central.

The rules service keeps no state of its own: for every email it asks BC what it knows. In production the
same two calls would go to the real Business Central API.
"""
from __future__ import annotations

import os

from .httpjson import call
from .reference import ReferenceData, Register, Registered

BC_URL = os.environ.get("BC_URL", "http://127.0.0.1:8100")


def load_from_bc(base_url: str = BC_URL) -> tuple[ReferenceData, Register]:
    ref = ReferenceData(call("GET", f"{base_url}/reference"))
    register = Register([Registered.from_json(d) for d in call("GET", f"{base_url}/incoming-documents")])
    return ref, register
