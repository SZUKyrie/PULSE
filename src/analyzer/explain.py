"""
ExplainParser — Parses PostgreSQL EXPLAIN (FORMAT JSON) output into a typed tree.

PostgreSQL EXPLAIN FORMAT JSON returns a structure like:
[
  {
    "Plan": {
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Startup Cost": 0.00,
      "Total Cost": 1234.56,
      "Plan Rows": 100,
      "Plan Width": 64,
      "Plans": [ ... child plans ... ]
    },
    "Planning Time": 0.5,
    "Execution Time": 12.3   // only with ANALYZE
  }
]

This module converts that JSON into a PlanNode tree for downstream analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlanNode:
    """Represents a single operator node in a PostgreSQL query plan tree."""

    node_type: str
    relation: Optional[str] = None
    schema: Optional[str] = None
    alias: Optional[str] = None
    startup_cost: float = 0.0
    total_cost: float = 0.0
    plan_rows: int = 0
    plan_width: int = 0
    actual_rows: Optional[int] = None
    actual_loops: Optional[int] = None
    filter: Optional[str] = None
    rows_removed_by_filter: Optional[int] = None
    index_name: Optional[str] = None
    index_cond: Optional[str] = None
    join_type: Optional[str] = None
    sort_key: Optional[list[str]] = None
    sort_method: Optional[str] = None
    sort_space_used: Optional[int] = None
    sort_space_type: Optional[str] = None
    hash_cond: Optional[str] = None
    merge_cond: Optional[str] = None
    parent_relationship: Optional[str] = None
    subplan_name: Optional[str] = None
    output: Optional[list[str]] = None
    workers_planned: Optional[int] = None
    workers_launched: Optional[int] = None
    children: list[PlanNode] = field(default_factory=list)

    @property
    def is_scan(self) -> bool:
        return "Scan" in self.node_type

    @property
    def is_join(self) -> bool:
        return self.node_type in (
            "Nested Loop",
            "Hash Join",
            "Merge Join",
        )

    @property
    def is_sort(self) -> bool:
        return self.node_type == "Sort"

    @property
    def is_subplan(self) -> bool:
        return self.parent_relationship == "SubPlan"

    @property
    def effective_rows(self) -> int:
        """Return actual_rows if available (ANALYZE), else estimated plan_rows."""
        if self.actual_rows is not None:
            return self.actual_rows
        return self.plan_rows

    @property
    def effective_loops(self) -> int:
        """Return actual_loops if available, else 1."""
        return self.actual_loops if self.actual_loops is not None else 1


class ExplainParser:
    """Parses PostgreSQL EXPLAIN (FORMAT JSON) output into a PlanNode tree."""

    def parse(self, explain_json: dict | list) -> PlanNode:
        """
        Parse the top-level EXPLAIN JSON into a PlanNode tree.

        Args:
            explain_json: The raw JSON output from PostgreSQL EXPLAIN FORMAT JSON.
                          Can be a list (standard format) or a dict (Plan key).

        Returns:
            Root PlanNode of the query plan tree.

        Raises:
            ValueError: If the JSON structure is not recognized.
        """
        # PostgreSQL wraps the plan in a list with one element
        if isinstance(explain_json, list):
            if len(explain_json) == 0:
                raise ValueError("Empty EXPLAIN JSON output")
            top = explain_json[0]
        elif isinstance(explain_json, dict):
            top = explain_json
        else:
            raise ValueError(
                f"Expected list or dict, got {type(explain_json).__name__}"
            )

        if "Plan" not in top:
            raise ValueError("No 'Plan' key found in EXPLAIN output")

        return self._parse_node(top["Plan"])

    def _parse_node(self, raw: dict) -> PlanNode:
        """Recursively parse a single plan node from the JSON dict."""
        node = PlanNode(
            node_type=raw.get("Node Type", "Unknown"),
            relation=raw.get("Relation Name"),
            schema=raw.get("Schema"),
            alias=raw.get("Alias"),
            startup_cost=float(raw.get("Startup Cost", 0.0)),
            total_cost=float(raw.get("Total Cost", 0.0)),
            plan_rows=int(raw.get("Plan Rows", 0)),
            plan_width=int(raw.get("Plan Width", 0)),
            actual_rows=_opt_int(raw.get("Actual Rows")),
            actual_loops=_opt_int(raw.get("Actual Loops")),
            filter=raw.get("Filter"),
            rows_removed_by_filter=_opt_int(raw.get("Rows Removed by Filter")),
            index_name=raw.get("Index Name"),
            index_cond=raw.get("Index Cond"),
            join_type=raw.get("Join Type"),
            sort_key=raw.get("Sort Key"),
            sort_method=raw.get("Sort Method"),
            sort_space_used=_opt_int(raw.get("Sort Space Used")),
            sort_space_type=raw.get("Sort Space Type"),
            hash_cond=raw.get("Hash Cond"),
            merge_cond=raw.get("Merge Cond"),
            parent_relationship=raw.get("Parent Relationship"),
            subplan_name=raw.get("Subplan Name"),
            output=raw.get("Output"),
            workers_planned=_opt_int(raw.get("Workers Planned")),
            workers_launched=_opt_int(raw.get("Workers Launched")),
        )

        # Recurse into child plans
        for child_raw in raw.get("Plans", []):
            child_node = self._parse_node(child_raw)
            node.children.append(child_node)

        return node

    def flatten(self, root: PlanNode) -> list[PlanNode]:
        """
        Flatten the plan tree into a list via pre-order traversal.

        This is useful for iterating over all nodes without recursion,
        e.g., to run pattern detection on every operator.
        """
        result: list[PlanNode] = []
        self._flatten_recursive(root, result)
        return result

    def _flatten_recursive(self, node: PlanNode, acc: list[PlanNode]) -> None:
        acc.append(node)
        for child in node.children:
            self._flatten_recursive(child, acc)


def _opt_int(val) -> Optional[int]:
    """Safely convert a value to int, returning None if not present."""
    if val is None:
        return None
    return int(val)
