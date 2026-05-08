"""FeedbackFormatter — translates plan anti-patterns into actionable LLM hints.

Converts structured PlanReport findings into natural language feedback that
guides the LLM to generate a more efficient SQL query. Each anti-pattern type
gets a specific, actionable hint derived from the analyzer's structured output.
"""

from __future__ import annotations

from ..analyzer.patterns import AntiPattern, Severity
from ..analyzer.scorer import PlanReport
from ..schema.loader import SchemaContext


# Pattern names used by the analyzer detectors
_PATTERN_SEQ_SCAN = "sequential_scan_large_table"
_PATTERN_NESTED_LOOP = "nested_loop_high_cardinality"
_PATTERN_SUBPLAN = "correlated_subplan"
_PATTERN_EXPENSIVE_SORT = "expensive_sort"
_PATTERN_MATERIALIZE = "redundant_materialize"
_PATTERN_HIGH_COST = "high_cost"


class FeedbackFormatter:
    """Formats plan analysis results into structured feedback for LLM refinement.

    Takes anti-patterns from a PlanReport and produces natural language feedback
    that the LLM can use to revise its generated SQL. The feedback includes:
    - Specific issue description for each anti-pattern
    - Actionable hints referencing available indexes and schema info
    - Current cost and improvement target
    """

    def __init__(self, cost_threshold: float = 50_000.0):
        """Initialize formatter.

        Args:
            cost_threshold: Plans below this total cost are considered acceptable.
        """
        self._cost_threshold = cost_threshold

    def format(self, report: PlanReport, schema_context: SchemaContext) -> str:
        """Format a PlanReport into structured feedback for the LLM.

        Args:
            report: The plan analysis report with detected anti-patterns.
            schema_context: Schema metadata for the relevant database.

        Returns:
            A multi-line string of actionable feedback suitable for appending
            to an LLM prompt as refinement context.
        """
        if report.pattern_count == 0:
            return ""

        sections: list[str] = []

        # Header with cost context
        sections.append(self._format_header(report))

        # Individual anti-pattern hints
        for i, pattern in enumerate(report.anti_patterns, 1):
            hint = self._format_pattern(pattern, schema_context)
            sections.append(f"  {i}. [{pattern.severity.value.upper()}] {hint}")

        # Footer with improvement target
        sections.append(self._format_footer(report))

        return "\n".join(sections)

    def _format_header(self, report: PlanReport) -> str:
        """Generate the feedback header with cost summary."""
        n_issues = report.pattern_count
        high_count = report.severity_summary.get("high", 0)
        severity_note = (
            f" ({high_count} high-severity)" if high_count > 0 else ""
        )
        return (
            f"## Query Plan Feedback\n"
            f"Current estimated cost: {report.total_cost:,.0f}\n"
            f"Issues detected: {n_issues}{severity_note}\n"
            f"Please revise the SQL to address the following:"
        )

    def _format_footer(self, report: PlanReport) -> str:
        """Generate the feedback footer with improvement target."""
        target = min(report.total_cost * 0.5, self._cost_threshold)
        return (
            f"\n## Target\n"
            f"Aim for a plan cost below {target:,.0f}. "
            f"Prioritize addressing HIGH severity issues first. "
            f"Preserve correctness — the query must still return the same results."
        )

    def _format_pattern(
        self, pattern: AntiPattern, schema_context: SchemaContext
    ) -> str:
        """Format a single anti-pattern into an actionable hint.

        Uses the analyzer's description and suggestion, enriched with schema
        context (available indexes, table sizes) when applicable.
        """
        # The analyzer already provides good descriptions and suggestions.
        # We enrich them with schema context when possible.
        enrichment = self._enrich_with_schema(pattern, schema_context)

        # Build the feedback line: description + suggestion + enrichment
        parts = [pattern.description]
        parts.append(f"Suggestion: {pattern.suggestion}")
        if enrichment:
            parts.append(enrichment)

        return " ".join(parts)

    def _enrich_with_schema(
        self, pattern: AntiPattern, schema_context: SchemaContext
    ) -> str:
        """Add schema-aware context to a pattern hint.

        For sequential scans, list available indexes from the schema context.
        For nested loops, note the table sizes involved.
        """
        node = pattern.node

        if pattern.pattern_name == _PATTERN_SEQ_SCAN and node.relation:
            table_info = schema_context.get_table(node.relation)
            if table_info and table_info.indexes:
                # List non-primary indexes that might help
                useful_indexes = [
                    f"'{idx.name}' on ({', '.join(idx.columns)})"
                    for idx in table_info.indexes
                    if not idx.is_primary
                ]
                if useful_indexes:
                    return (
                        f"Available indexes on '{node.relation}': "
                        f"{'; '.join(useful_indexes)}."
                    )

        if pattern.pattern_name == _PATTERN_NESTED_LOOP:
            # Provide table size context for the join sides
            if len(node.children) >= 2:
                outer = node.children[0]
                inner = node.children[1]
                size_hints = []
                for child in (outer, inner):
                    if child.relation:
                        table_info = schema_context.get_table(child.relation)
                        if table_info:
                            size_hints.append(
                                f"'{child.relation}' has ~{table_info.estimated_rows:,} rows"
                            )
                if size_hints:
                    return f"Table sizes: {'; '.join(size_hints)}."

        return ""
