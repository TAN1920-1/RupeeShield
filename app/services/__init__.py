"""Service layer.

Routes stay thin on purpose: they validate input, call one service function and
serialise the result. Everything with a business rule in it lives here, so the
rules can be unit-tested without starting a web server and the same code can be
reused from the command line (``python -m app.ml.train``, the simulation CLI).
"""
