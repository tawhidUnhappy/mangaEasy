"""mediaconductor.assist — local-LLM production helpers for small driver agents.

The manga pipeline was designed for a strong multimodal agent (it reads verify
sheets, writes grounded narration, fixes crops). Weaker or text-only agents
kept skipping exactly those steps in production. This package moves the
vision-and-judgement work into the toolkit itself, powered by the isolated
Gemma 4 tool env (``mediaconductor install-tool gemma-4``):

- ``characters``   — per-project character registry (name/appearance/role)
  that grounds narration and speaker attribution; can be auto-drafted.
- ``crop-qa``      — reviews crop verification artifacts with Gemma vision,
  flags bad cuts/boxes, and prints the exact override commands to fix them.
- ``narrate-auto`` — drafts grounded ``narration.json`` from panels + OCR +
  the character registry; always exits 3 (review required).
- ``manga-auto``   — one-command orchestrator: download → style-detect →
  the correct splitter → crop QA → transcript → narration draft, then
  (after review) ``--stage build`` for audio/render/validate.

Everything here proposes and gates; nothing publishes. Human/agent review of
the produced sheets remains a required step of the workflow.
"""
