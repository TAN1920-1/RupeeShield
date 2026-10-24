"""Security boundary helpers: input validation, rate limiting, audit logging.

This package is deliberately small and dependency-free. It does **not** implement
authentication: RupeeShield is a local teaching cyber range with a single trusted
operator, so pretending to have "bank-grade" auth would be misleading. What it
does implement is every control that actually matters for a local app that accepts
input and writes history:

* :mod:`app.security.validation` — reject malformed/oversized input at the edge;
* :mod:`app.security.ratelimit`  — bound request volume so a runaway script cannot
  exhaust the process;
* :mod:`app.security.audit`      — append-only record of state changes.

Secrets: the platform needs **no** API keys, tokens or credentials. The only
configuration is non-sensitive (paths, thresholds). See ``app/core/config.py``.
"""
