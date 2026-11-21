"""Simulated Business Central for the demo: master data, incoming documents and draft purchase invoices.

Field names and endpoints are our own, not Business Central's real API. In production the rules service and
the flow talk to the real BC instead; the shape of the work (incoming document -> draft purchase invoice ->
approval -> posted) stays the same.
"""
