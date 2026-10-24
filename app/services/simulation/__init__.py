"""Synthetic attack simulation for the RupeeShield cyber range.

The package is split so that each piece can be tested on its own:

``catalog``
    The four scenarios, their objectives and their default size.
``accounts``
    The synthetic account roster, created fresh for every run from the run seed.
``scenarios``
    :func:`~app.services.simulation.scenarios.build_plan` turns
    ``(scenario, seed, n_events)`` into a deterministic :class:`ScenarioPlan`.
    It contains no run id and no dependency on the defense configuration.
``engine``
    Replays a plan against one defense configuration and persists the result.

The split matters for the research question: because the plan is produced before the
defense is chosen, the same attack can be replayed under "no defense", "rules only"
and "rules + ML + correlation", and the only variable is the defense itself.

Everything is local, in-process and seeded. No real bank, payment rail or external
system is ever contacted.
"""
