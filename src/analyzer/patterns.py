"""
Anti-pattern detection for PostgreSQL query plans.

Inspired by RetroSlow's operator-level cost analysis, this module detects
structural inefficiencies in query plans that an LLM-based NL2SQL system
should correct. Each detector examines individual PlanNodes (or the full tree)
and produces AntiPattern findings with severity, description, and actionable
suggestions for the feedback loop.

Detection philosophy:
- HIGH severity: structural problems the optimizer cannot fix (wrong join type,
  correlated subquery, missing predicate for index). These require SQL rewriting.
- MEDIUM severity: potential performance issues that may or may not manifest
  depending on data volume (large sorts, redundant materializations).
- LOW severity: cost threshold violations that indicate general inefficiency
  but no specific structural flaw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .explain import PlanNode


class Severity(Enum):
    """Severity level for detected anti-patterns."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def __lt__(self, other: Severity) -> bool:
        order = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2}
        return order[self] < order[other]

    def __le__(self, other: Severity) -> bool:
        return self == other or self < other


@dataclass
class AntiPattern:
    """A detected anti-pattern in the query plan."""

    pattern_name: str
    severity: Severity
    node: PlanNode
    description: str
    suggestion: str

    def to_feedback(self) -> str:
        """Format as a feedback string suitable for LLM consumption."""
        return (
            f"[{self.severity.value.upper()}] {self.pattern_name}: "
            f"{self.description} "
            f"Suggestion: {self.suggestion}"
        )


@dataclass
class AnalysisContext:
    """
    Context information needed by pattern detectors.

    Attributes:
        table_sizes: Mapping of table name -> approximate row count.
        available_indexes: Mapping of table name -> list of index definitions.
            Each index definition is a dict with at least 'name' and 'columns' keys.
        cost_threshold: Total cost above which a plan is considered expensive.
    """

    table_sizes: dict[str, int] = field(default_factory=dict)
    available_indexes: dict[str, list[dict]] = field(default_factory=dict)
    cost_threshold: float = 50000.0


# ---------------------------------------------------------------------------
# Individual anti-pattern detectors
# ---------------------------------------------------------------------------


def detect_seq_scan(
    node: PlanNode,
    table_sizes: dict[str, int],
    available_indexes: dict[str, list[dict]],
) -> Optional[AntiPattern]:
    """
    Detect sequential scans on large tables where a usable index exists.

    A sequential scan is problematic when:
    1. The table has >10K rows (small tables are fine to seq scan)
    2. There IS at least one index on the table that COULD satisfy the filter/condition
    3. The node has a Filter predicate (otherwise there's no selective condition)

    This is the most common anti-pattern in LLM-generated SQL: the model generates
    a query with a predicate on a non-indexed column, or structures the WHERE clause
    in a way that prevents index usage (e.g., wrapping an indexed column in a function).
    """
    if node.node_type != "Seq Scan":
        return None

    relation = node.relation
    if relation is None:
        return None

    # Check table size — small tables don't benefit from index scans
    table_row_count = table_sizes.get(relation, 0)
    if table_row_count <= 10000:
        return None

    # Check if there are available indexes on this table
    indexes = available_indexes.get(relation, [])
    if not indexes:
        # No indexes exist — this isn't the query's fault
        return None

    # If the node has a filter, there's a selective predicate that an index might help
    # Even without a filter, a seq scan on a large table with indexes suggests
    # the query structure prevents index usage
    has_filter = node.filter is not None

    # Build description of available indexes for the suggestion
    index_names = [idx.get("name", "unknown") for idx in indexes]
    index_columns = []
    for idx in indexes:
        cols = idx.get("columns", [])
        if cols:
            index_columns.append(f"{idx.get('name', '?')}({', '.join(cols)})")

    if has_filter:
        description = (
            f"Sequential scan on '{relation}' ({table_row_count:,} rows) "
            f"with filter: {node.filter}. "
            f"Available indexes: {', '.join(index_columns) if index_columns else ', '.join(index_names)}."
        )
        suggestion = (
            f"Restructure the WHERE clause to enable index usage. "
            f"Avoid wrapping indexed columns in functions. "
            f"Consider adding a predicate on indexed columns: "
            f"{', '.join(index_columns) if index_columns else ', '.join(index_names)}."
        )
    else:
        description = (
            f"Sequential scan on '{relation}' ({table_row_count:,} rows) "
            f"reading all rows despite available indexes: "
            f"{', '.join(index_columns) if index_columns else ', '.join(index_names)}. "
            f"This suggests the query needs all rows or lacks a selective predicate."
        )
        suggestion = (
            f"Add a WHERE clause predicate that uses one of the available indexes "
            f"to reduce the scan scope. If all rows are genuinely needed, "
            f"consider whether the query can be restructured with a JOIN "
            f"that limits the accessed rows."
        )

    return AntiPattern(
        pattern_name="sequential_scan_large_table",
        severity=Severity.HIGH,
        node=node,
        description=description,
        suggestion=suggestion,
    )


def detect_nested_loop(node: PlanNode) -> Optional[AntiPattern]:
    """
    Detect Nested Loop joins where the inner side has high cardinality.

    Nested Loop is appropriate when:
    - The inner side produces very few rows (index lookup)
    - One side is tiny (< ~100 rows)

    It is problematic when:
    - The inner side estimates >1000 rows per loop
    - This indicates Hash Join or Merge Join would be more efficient

    The optimizer sometimes picks Nested Loop due to cardinality misestimates.
    LLM-generated SQL can cause this by structuring JOINs in a way that
    prevents the optimizer from choosing a better algorithm.
    """
    if node.node_type != "Nested Loop":
        return None

    # A Nested Loop has exactly 2 children: outer (index 0) and inner (index 1)
    if len(node.children) < 2:
        return None

    outer = node.children[0]
    inner = node.children[1]

    # The inner side is re-scanned for each outer row.
    # If inner plan_rows is high, nested loop is O(outer * inner) which is expensive.
    inner_rows = inner.effective_rows
    outer_rows = outer.effective_rows

    # Threshold: inner side producing >1000 rows suggests hash/merge join is better
    if inner_rows <= 1000:
        return None

    # Additional heuristic: if both sides are large, it's definitely wrong
    estimated_work = outer_rows * inner_rows

    description = (
        f"Nested Loop join with high-cardinality inner side. "
        f"Outer: {outer_rows:,} rows ({outer.node_type}), "
        f"Inner: {inner_rows:,} rows ({inner.node_type}). "
        f"Estimated row comparisons: {estimated_work:,}. "
        f"Hash Join or Merge Join would be more efficient here."
    )
    suggestion = (
        f"Rewrite the JOIN to enable Hash Join or Merge Join. "
        f"Ensure the join condition uses equality (=) rather than inequality "
        f"or complex expressions. If using a subquery on the inner side, "
        f"consider rewriting as a derived table or CTE with explicit join keys."
    )

    return AntiPattern(
        pattern_name="nested_loop_high_cardinality",
        severity=Severity.HIGH,
        node=node,
        description=description,
        suggestion=suggestion,
    )


def detect_correlated_subplan(node: PlanNode) -> Optional[AntiPattern]:
    """
    Detect correlated subqueries (SubPlan nodes) that could be rewritten as JOINs.

    In PostgreSQL's EXPLAIN output, correlated subqueries appear as child nodes
    with parent_relationship = "SubPlan". These are re-executed for every row
    of the outer query, making them O(N * cost_of_subquery).

    Most correlated subqueries in analytical queries can be rewritten as:
    - A JOIN (for EXISTS/IN patterns)
    - A window function (for row-level aggregation)
    - A derived table with GROUP BY (for aggregation patterns)

    This is one of the most impactful anti-patterns because the optimizer CANNOT
    decorrelate complex subqueries — it requires structural SQL rewriting.
    """
    if not node.is_subplan:
        return None

    # Determine the subplan type from the subplan_name
    subplan_name = node.subplan_name or "unnamed subplan"
    subplan_cost = node.total_cost

    description = (
        f"Correlated subquery detected ({subplan_name}). "
        f"This subplan (cost: {subplan_cost:.1f}) is re-executed for each row "
        f"of the parent query. Node type: {node.node_type}."
    )

    # Provide specific suggestion based on what the subplan contains
    if _subtree_contains_aggregate(node):
        suggestion = (
            f"Rewrite the correlated subquery as a derived table with GROUP BY, "
            f"then JOIN the result. For scalar subqueries returning an aggregate, "
            f"use a LEFT JOIN with the grouped subquery. This allows the aggregation "
            f"to run once instead of per-row."
        )
    else:
        suggestion = (
            f"Rewrite the correlated subquery as a JOIN. "
            f"For EXISTS(...) patterns, use an INNER JOIN or semi-join. "
            f"For NOT EXISTS(...), use a LEFT JOIN ... WHERE key IS NULL. "
            f"For IN(...) with a subquery, rewrite as INNER JOIN on the key."
        )

    return AntiPattern(
        pattern_name="correlated_subplan",
        severity=Severity.HIGH,
        node=node,
        description=description,
        suggestion=suggestion,
    )


def detect_expensive_sort(node: PlanNode) -> Optional[AntiPattern]:
    """
    Detect Sort operators processing a large number of rows.

    Large sorts (>10K rows) risk:
    - Spilling to disk (external sort), which is orders of magnitude slower
    - Consuming large amounts of work_mem
    - Being unnecessary if the data could be pre-sorted via index

    If ANALYZE data is available, we also check sort_space_type for "Disk"
    which confirms an actual disk spill occurred.
    """
    if node.node_type != "Sort":
        return None

    rows_to_sort = node.effective_rows
    if rows_to_sort <= 10000:
        return None

    # Check for confirmed disk spill (from ANALYZE output)
    disk_spill = node.sort_space_type == "Disk"

    if disk_spill:
        space_used = node.sort_space_used or 0
        description = (
            f"Sort on {rows_to_sort:,} rows SPILLED TO DISK "
            f"(used {space_used} kB on disk). "
            f"Sort keys: {', '.join(node.sort_key or ['unknown'])}. "
            f"This causes severe I/O overhead."
        )
    else:
        description = (
            f"Sort on {rows_to_sort:,} rows (risk of disk spill). "
            f"Sort keys: {', '.join(node.sort_key or ['unknown'])}. "
            f"Estimated cost: {node.total_cost:.1f}."
        )

    suggestion = (
        f"Consider: (1) Adding an index on the sort columns "
        f"({', '.join(node.sort_key or [])}) to avoid sorting entirely. "
        f"(2) Reducing the rows before sorting with a more selective WHERE clause. "
        f"(3) Using a LIMIT if only top-N results are needed. "
        f"(4) If sorting for GROUP BY, consider using HashAggregate instead."
    )

    return AntiPattern(
        pattern_name="expensive_sort",
        severity=Severity.HIGH if disk_spill else Severity.MEDIUM,
        node=node,
        description=description,
        suggestion=suggestion,
    )


def detect_redundant_materialize(node: PlanNode) -> Optional[AntiPattern]:
    """
    Detect Materialize nodes that are re-scanned many times.

    Materialize caches the result of a subplan so it can be re-scanned.
    When the number of loops is high (>10), it indicates the plan is
    repeatedly re-computing and caching the same data, often because:
    - A correlated subquery forces repeated evaluation
    - A nested loop re-scans its inner side many times
    - The query could be restructured to compute the result once

    RetroSlow identifies this as a "redundant re-computation" pattern where
    the same intermediate result is materialized and discarded repeatedly.
    """
    if node.node_type != "Materialize":
        return None

    # Check loops — in EXPLAIN ANALYZE, actual_loops tells us exactly
    # In EXPLAIN without ANALYZE, we infer from parent structure
    loops = node.effective_loops
    if loops <= 10:
        return None

    child_cost = node.children[0].total_cost if node.children else 0
    total_re_computation_cost = child_cost * loops

    description = (
        f"Materialize node re-scanned {loops:,} times. "
        f"Child cost per execution: {child_cost:.1f}, "
        f"total re-computation cost: {total_re_computation_cost:.1f}. "
        f"This suggests the query structure forces repeated evaluation "
        f"of the same intermediate result."
    )
    suggestion = (
        f"Restructure the query to compute the materialized result once. "
        f"Options: (1) Extract the repeated computation into a CTE (WITH clause). "
        f"(2) Rewrite the nested loop as a Hash Join. "
        f"(3) If this is inside a correlated subquery, decorrelate it into a JOIN."
    )

    return AntiPattern(
        pattern_name="redundant_materialize",
        severity=Severity.MEDIUM,
        node=node,
        description=description,
        suggestion=suggestion,
    )


def detect_high_cost(
    node: PlanNode, threshold: float = 50000.0
) -> Optional[AntiPattern]:
    """
    Detect nodes with excessively high total cost.

    This is a catch-all detector for general inefficiency. It flags the
    root node (or any high-cost subtree) when the total cost exceeds
    a configurable threshold.

    Unlike the structural detectors above, this doesn't identify a specific
    anti-pattern — it signals that the overall query is expensive and may
    benefit from restructuring, even if no single structural issue is apparent.

    The threshold should be calibrated per-database based on typical query costs.
    Default of 50,000 cost units is appropriate for TPC-H SF1 on PostgreSQL.
    """
    if node.total_cost <= threshold:
        return None

    description = (
        f"High total cost: {node.total_cost:,.1f} (threshold: {threshold:,.1f}). "
        f"Node type: {node.node_type}. "
        f"Estimated rows: {node.plan_rows:,}, width: {node.plan_width}."
    )
    suggestion = (
        f"The query's estimated cost ({node.total_cost:,.1f}) exceeds the "
        f"acceptable threshold ({threshold:,.1f}). Consider restructuring: "
        f"reduce joins, add selective predicates, or use materialized views "
        f"for repeated expensive computations."
    )

    return AntiPattern(
        pattern_name="high_cost",
        severity=Severity.LOW,
        node=node,
        description=description,
        suggestion=suggestion,
    )


# ---------------------------------------------------------------------------
# Aggregate detector: runs all patterns on the full tree
# ---------------------------------------------------------------------------


def detect_all(root: PlanNode, context: AnalysisContext) -> list[AntiPattern]:
    """
    Run all anti-pattern detectors on the full plan tree.

    Performs a single traversal, applying each detector to every node.
    The context provides table sizes and available indexes needed by
    some detectors.

    Args:
        root: Root PlanNode of the query plan tree.
        context: AnalysisContext with table metadata.

    Returns:
        List of all detected anti-patterns, ordered by severity (HIGH first).
    """
    from .explain import ExplainParser

    parser = ExplainParser()
    all_nodes = parser.flatten(root)

    patterns: list[AntiPattern] = []

    # Track whether we've already flagged the root for high cost
    root_cost_flagged = False

    for node in all_nodes:
        # 1. Sequential scan on large table
        result = detect_seq_scan(
            node, context.table_sizes, context.available_indexes
        )
        if result:
            patterns.append(result)

        # 2. Nested loop with high cardinality inner
        result = detect_nested_loop(node)
        if result:
            patterns.append(result)

        # 3. Correlated subplan
        result = detect_correlated_subplan(node)
        if result:
            patterns.append(result)

        # 4. Expensive sort
        result = detect_expensive_sort(node)
        if result:
            patterns.append(result)

        # 5. Redundant materialize
        result = detect_redundant_materialize(node)
        if result:
            patterns.append(result)

        # 6. High cost — only flag the root node to avoid duplicates
        if node is root and not root_cost_flagged:
            result = detect_high_cost(node, context.cost_threshold)
            if result:
                patterns.append(result)
                root_cost_flagged = True

    # Sort by severity (HIGH first) then by cost (most expensive first)
    severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
    patterns.sort(
        key=lambda p: (severity_order[p.severity], -p.node.total_cost)
    )

    return patterns


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _subtree_contains_aggregate(node: PlanNode) -> bool:
    """Check if a subtree contains an Aggregate node (GROUP BY / scalar agg)."""
    if "Aggregate" in node.node_type:
        return True
    for child in node.children:
        if _subtree_contains_aggregate(child):
            return True
    return False
