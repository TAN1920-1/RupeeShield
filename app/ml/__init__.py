"""Machine-learning pipeline (data, features, training, inference, evaluation).

Import graph: this package only depends on ``app.core``, ``app.utils`` and
third-party ML libraries. It never imports API routes or services, so it can be
used from a CLI (``python -m app.ml.train``) without starting the web app.
"""
