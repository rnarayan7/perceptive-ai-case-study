"""Stage 1 evaluation-corpus ingestion.

The figure-extraction system needs many more figures than the six in the brief,
each paired with recoverable ground-truth values, to measure accuracy and
calibration. This package collects them from public sources.

The orchestration pattern (a rate-limited stdlib HTTP client, a base ingester
that fetches then persists then writes a manifest, a source registry) is modeled
loosely on the memo ingestion, but this package is self-contained: the unit here
is a *figure* (an image plus its true numbers, tagged by figure type), not a
company filing, so the data model is built fresh.

Each source is a :class:`~corpus.base.BaseFigureIngester` that fetches figures
and writes :class:`~corpus.base.FigureRecord` objects to disk. Discover sources
through :data:`corpus.sources.REGISTRY`.
"""

from corpus.base import (
    BaseFigureIngester,
    FigureRecord,
    FigureStorage,
    FigureType,
    GroundTruthValue,
    IngestManifest,
)

__all__ = [
    "BaseFigureIngester",
    "FigureRecord",
    "FigureStorage",
    "FigureType",
    "GroundTruthValue",
    "IngestManifest",
]
