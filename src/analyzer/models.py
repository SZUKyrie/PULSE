"""Legacy data models for backward compatibility.

The canonical types are now in:
- patterns.py: AntiPattern, Severity, AnalysisContext
- scorer.py: PlanReport, PlanScorer

This module retains AntiPatternType as a convenience enum for code that
categorizes anti-patterns by type name string.
"""

from __future__ import annotations

from enum import Enum


class AntiPatternType(Enum):
    """Categories of query plan anti-patterns.

    Maps to pattern_name strings used by the detectors in patterns.py:
    - SEQ_SCAN -> "sequential_scan_large_table"
    - NESTED_LOOP -> "nested_loop_high_cardinality"
    - SUBPLAN -> "correlated_subplan"
    - EXPENSIVE_SORT -> "expensive_sort"
    - MATERIALIZE_LOOPS -> "redundant_materialize"
    - HIGH_COST -> "high_cost"
    """

    SEQ_SCAN = "sequential_scan_large_table"
    NESTED_LOOP = "nested_loop_high_cardinality"
    SUBPLAN = "correlated_subplan"
    EXPENSIVE_SORT = "expensive_sort"
    MATERIALIZE_LOOPS = "redundant_materialize"
    HIGH_COST = "high_cost"

    @classmethod
    def from_pattern_name(cls, name: str) -> AntiPatternType | None:
        """Look up an AntiPatternType by its pattern_name string."""
        for member in cls:
            if member.value == name:
                return member
        return None
