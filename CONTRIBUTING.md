# Contributing

Use Python 3.11. Install the locked runtime and requirements-dev.txt, then run `python -X utf8 -m pytest -q` from the checkout. Tests use synthetic PDFs, fixtures and fake remote responses; they must not require credentials or local models.

Preserve evidence-fidelity prompts, the distinction between service errors and evidence-based refusal, per-user data isolation, and source provenance. Scientific wrappers must call reviewed original functions, with meaningful comparisons rather than startup-only checks.

Do not commit personal PDFs, API responses containing sensitive material, environment files, keys, DPAPI files, model weights, environments or generated workspaces. Keep raw acceptance outputs outside the source checkout and publish only explicitly reviewed summaries.

Main workbench contributions are AGPL-3.0-only. Separately licensed examples retain their notices.
