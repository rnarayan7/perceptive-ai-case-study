"""Memo structure, house style, and the rendered-section contract.

The declarative shape of the memo (what sections exist and which module feeds each),
the single house-style system prompt that governs all drafting, and the RenderedSection
contract the assembler produces and the web app consumes. No drafting logic lives here
yet (that is a later wave); this is the frozen contract those agents build against.
"""

from memo.report.render import Citation, RenderedSection, render_markdown
from memo.report.structure import MEMO_SECTIONS, Section, section_by_id
from memo.report.style import HOUSE_STYLE

__all__ = [
    "MEMO_SECTIONS",
    "Section",
    "section_by_id",
    "HOUSE_STYLE",
    "Citation",
    "RenderedSection",
    "render_markdown",
]
