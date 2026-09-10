"""The rendered-section contract and a reference Markdown renderer.

A ``RenderedSection`` is what the assembler produces per section and what the web app
consumes: prose plus the citations it references (by evidence id) plus any attached
figures. The Markdown renderer here is a minimal reference so the contract is concrete;
the full assembler (and an HTML renderer for the app) come in a later wave.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Citation:
    """A citation marker in a section, pointing at a ledger evidence row."""

    marker: str  # e.g. "[1]" as it appears inline in the prose
    evidence_id: str
    url: str
    label: str = ""  # short human label, e.g. "KYMR 10-Q" or an NCT id


@dataclass
class Figure:
    """A figure inserted at a claim (populated by the figures wave)."""

    figure_id: str
    caption: str
    image_ref: str  # path or asset id of the (annotated) image
    claim_id: Optional[str] = None


@dataclass
class RenderedSection:
    """One composed memo section."""

    section_id: str
    title: str
    prose: str  # the body; may contain inline citation markers matching Citation.marker
    citations: List[Citation] = field(default_factory=list)
    figures: List[Figure] = field(default_factory=list)
    takeaway: str = ""  # one-line lead: the conclusion + key figure, rendered emphasized


def render_markdown(sections: List[RenderedSection], title: str) -> str:
    """Reference renderer: sections to a single Markdown document with a sources list.

    Deliberately simple and deterministic. The app will render the same RenderedSection
    objects to HTML with clickable citations; this proves the contract end to end.
    """
    lines: List[str] = [f"# {title}", ""]
    for section in sections:
        lines.append(f"## {section.title}")
        lines.append("")
        lines.append(section.prose.strip())
        lines.append("")
        for figure in section.figures:
            lines.append(f"![{figure.caption}]({figure.image_ref})")
            lines.append("")
        if section.citations:
            lines.append("_Sources: " + ", ".join(
                f"{c.marker} {c.label or c.url}" for c in section.citations
            ) + "_")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
