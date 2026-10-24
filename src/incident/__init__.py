"""Incident response core for Windows Dev Agent.

Evidence collection is read-only. Every change goes through a plan that the host
approves as a whole, validated against fresh evidence, and recorded in a manifest
so that it can be undone. See docs/INCIDENT_RESPONSE.md for the design.
"""
