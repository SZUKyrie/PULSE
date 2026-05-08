"""
PULSE — Plan Analyzer Module

This module is the core contribution of the project. It analyzes PostgreSQL
EXPLAIN (FORMAT JSON) output to detect anti-patterns in query plans and
produce actionable feedback for the LLM-based SQL generation loop.

Architecture:
    ExplainParser  ->  PlanNode tree
    PlanNode tree  ->  AntiPattern detection (6 detectors)
    AntiPatterns   ->  PlanScorer -> PlanReport

Usage:
    from src.analyzer import PlanAnalyzer, PlanReport, AntiPattern, Severity

    analyzer = PlanAnalyzer(
        table_sizes={"lineitem": 6_000_000, "orders": 1_500_000},
        available_indexes={"lineitem": [{"name": "idx_shipdate", "columns": ["l_shipdate"]}]},
    )
    report = analyzer.analyze(explain_json)
    if not report.is_acceptable:
        feedback = report.feedback_strings()
        # Feed back to LLM for query rewriting
"""

from __future__ import annotations

from typing import Optional

from .explain import ExplainParser, PlanNode
from .patterns import (
    AnalysisContext,
    AntiPattern,
    Severity,
    detect_all,
)
from .scorer import PlanReport, PlanScorer

# Re-export legacy models for backward compatibility
from .models import AntiPatternType


class PlanAnalyzer:
    """
    High-level interface to the plan analysis pipeline.

    Combines parsing, anti-pattern detection, and scoring into a single
    analyze() call. This is the primary entry point for the feedback loop.
    """

    def __init__(
        self,
        table_sizes: Optional[dict[str, int]] = None,
        available_indexes: Optional[dict[str, list[dict]]] = None,
        cost_threshold: float = 50000.0,
        max_medium_issues: int = 3,
    ):
        """
        Args:
            table_sizes: Mapping of table name -> approximate row count.
            available_indexes: Mapping of table name -> list of index dicts.
                Each dict should have 'name' (str) and 'columns' (list[str]).
            cost_threshold: Total cost above which a plan is unacceptable.
            max_medium_issues: Max MEDIUM-severity issues before rejection.
        """
        self.parser = ExplainParser()
        self.scorer = PlanScorer(
            cost_threshold=cost_threshold,
            max_medium_issues=max_medium_issues,
        )
        self.context = AnalysisContext(
            table_sizes=table_sizes or {},
            available_indexes=available_indexes or {},
            cost_threshold=cost_threshold,
        )

    def analyze(self, explain_json: dict | list) -> PlanReport:
        """
        Analyze a PostgreSQL EXPLAIN (FORMAT JSON) output.

        Full pipeline: parse -> detect anti-patterns -> score -> report.

        Args:
            explain_json: Raw JSON from PostgreSQL EXPLAIN FORMAT JSON.

        Returns:
            PlanReport with acceptability, anti-patterns, and feedback.
        """
        # Step 1: Parse into PlanNode tree
        root = self.parser.parse(explain_json)

        # Step 2: Detect all anti-patterns
        anti_patterns = detect_all(root, self.context)

        # Step 3: Score and produce report
        report = self.scorer.score(root, anti_patterns)

        return report

    def analyze_and_compare(
        self,
        explain_json: dict | list,
        previous_report: Optional[PlanReport] = None,
    ) -> tuple[PlanReport, float]:
        """
        Analyze a plan and compare against the previous iteration.

        Args:
            explain_json: Raw JSON from EXPLAIN FORMAT JSON.
            previous_report: Report from the previous feedback iteration.

        Returns:
            Tuple of (current_report, improvement_ratio).
            improvement_ratio > 1.0 means the plan improved.
        """
        report = self.analyze(explain_json)

        if previous_report is not None:
            improvement = self.scorer.compare(previous_report, report)
        else:
            improvement = 1.0

        return report, improvement

    def should_iterate(
        self,
        current_report: PlanReport,
        previous_report: Optional[PlanReport] = None,
        iteration: int = 0,
        max_iterations: int = 3,
    ) -> bool:
        """
        Determine if another feedback iteration is needed.

        Args:
            current_report: Report from the current iteration.
            previous_report: Report from previous iteration (None for first).
            iteration: Current iteration number (0-indexed).
            max_iterations: Max allowed iterations.

        Returns:
            True if another feedback loop iteration should be attempted.
        """
        return self.scorer.should_continue_iteration(
            current_report=current_report,
            previous_report=previous_report,
            iteration=iteration,
            max_iterations=max_iterations,
        )


__all__ = [
    "PlanAnalyzer",
    "PlanReport",
    "AntiPattern",
    "AntiPatternType",
    "Severity",
    "PlanNode",
    "ExplainParser",
    "PlanScorer",
    "AnalysisContext",
]
