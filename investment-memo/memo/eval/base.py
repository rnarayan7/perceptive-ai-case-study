"""Evaluator interface.

Every evaluator produces an :class:`~memo.eval.types.EvalReport`. Retrieval is the
first; faithfulness and analytical-quality evaluators (which will call real models,
never mocks) implement the same interface and plug into the same runner and CLI.
"""

from __future__ import annotations

import abc

from memo.eval.types import EvalReport


class Evaluator(abc.ABC):
    """Base class for all evaluators."""

    #: Short evaluator name, e.g. "retrieval". Set by subclasses.
    name: str = ""

    @abc.abstractmethod
    def run(self) -> EvalReport:
        """Run the evaluation and return a report."""
        raise NotImplementedError
