"""Memo composition: run the analysis, persist claims, draft and synthesize the memo.

``compose_memo`` is the orchestrator that ties the analysis modules, the ledger, and the
report layer together into one rendered memo and returns its ``memo_id``. It runs every
module, maps their grounded claims into ledger rows, drafts each section's prose under
the house style, synthesizes the cross-module thesis, folds in the deterministic rNPV
valuation and any figures (both imported defensively), and writes the rendered-memo JSON
the web app consumes. ``render_markdown_for`` renders a written memo to Markdown.
"""

from memo.compose.engine import (
    compose_memo,
    load_artifact,
    render_markdown_for,
)

__all__ = ["compose_memo", "render_markdown_for", "load_artifact"]
