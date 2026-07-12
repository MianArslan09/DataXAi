# Research Harness

This directory is **not** a Django app and is never added to `INSTALLED_APPS`.
It holds the tooling that exists only to produce C1-C5 (Volume 1, Section 1.3):

- `fault_injector.py` (Volume 4+) - seeded, deterministic corruption of clean
  batches at controlled rates (0/5/10/20/30%), writing ground truth to a
  `fault_injection_log` table so recall/precision are actually computable.
- `evaluation/` (Volume 16) - the ablation study (C1), CDQI paired t-test (C2),
  the injection-rate sweeps (C3/C4), and the C5 survey analysis scripts.

Kept separate from M1-M7 on purpose: a real SME deployment never injects
synthetic faults, so the production system must never depend on this code
being present.
