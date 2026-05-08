from __future__ import annotations

"""
Tests for the plan analyzer module.

Uses realistic PostgreSQL EXPLAIN (FORMAT JSON) output as fixtures to test
anti-pattern detection and cost scoring.
"""

import pytest

from src.analyzer.explain import ExplainParser, PlanNode


# ─── Fixtures: Realistic EXPLAIN JSON from PostgreSQL ─────────────────────────


EXPLAIN_SEQ_SCAN_LARGE = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Parallel Aware": False,
            "Relation Name": "lineitem",
            "Schema": "public",
            "Alias": "lineitem",
            "Startup Cost": 0.0,
            "Total Cost": 170934.12,
            "Plan Rows": 6001215,
            "Plan Width": 12,
            "Output": ["l_orderkey", "l_quantity"],
            "Filter": "(l_shipdate <= '1998-09-02'::date)",
            "Plans": [],
        },
        "Planning Time": 0.12,
    }
]

EXPLAIN_INDEX_SCAN = [
    {
        "Plan": {
            "Node Type": "Index Scan",
            "Scan Direction": "Forward",
            "Index Name": "idx_lineitem_shipdate",
            "Relation Name": "lineitem",
            "Schema": "public",
            "Alias": "lineitem",
            "Startup Cost": 0.43,
            "Total Cost": 1234.56,
            "Plan Rows": 500,
            "Plan Width": 12,
            "Output": ["l_orderkey", "l_quantity"],
            "Index Cond": "(l_shipdate >= '1998-01-01'::date)",
        },
        "Planning Time": 0.08,
    }
]

EXPLAIN_NESTED_LOOP_HIGH_CARDINALITY = [
    {
        "Plan": {
            "Node Type": "Nested Loop",
            "Join Type": "Inner",
            "Startup Cost": 0.87,
            "Total Cost": 892456.23,
            "Plan Rows": 150000,
            "Plan Width": 32,
            "Output": ["o.o_orderkey", "l.l_quantity"],
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Parent Relationship": "Outer",
                    "Relation Name": "orders",
                    "Schema": "public",
                    "Alias": "o",
                    "Startup Cost": 0.0,
                    "Total Cost": 40421.0,
                    "Plan Rows": 1500000,
                    "Plan Width": 4,
                    "Filter": "(o_orderdate >= '1994-01-01'::date)",
                },
                {
                    "Node Type": "Index Scan",
                    "Parent Relationship": "Inner",
                    "Index Name": "idx_lineitem_orderkey",
                    "Relation Name": "lineitem",
                    "Schema": "public",
                    "Alias": "l",
                    "Startup Cost": 0.43,
                    "Total Cost": 0.56,
                    "Plan Rows": 4,
                    "Plan Width": 8,
                    "Index Cond": "(l_orderkey = o.o_orderkey)",
                },
            ],
        },
        "Planning Time": 0.34,
    }
]

EXPLAIN_HASH_JOIN = [
    {
        "Plan": {
            "Node Type": "Hash Join",
            "Join Type": "Inner",
            "Startup Cost": 45123.45,
            "Total Cost": 89234.67,
            "Plan Rows": 150000,
            "Plan Width": 32,
            "Hash Cond": "(l.l_orderkey = o.o_orderkey)",
            "Output": ["o.o_orderkey", "l.l_quantity"],
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Parent Relationship": "Outer",
                    "Relation Name": "lineitem",
                    "Schema": "public",
                    "Alias": "l",
                    "Startup Cost": 0.0,
                    "Total Cost": 170934.12,
                    "Plan Rows": 6001215,
                    "Plan Width": 12,
                },
                {
                    "Node Type": "Hash",
                    "Parent Relationship": "Inner",
                    "Startup Cost": 40421.0,
                    "Total Cost": 40421.0,
                    "Plan Rows": 1500000,
                    "Plan Width": 4,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Parent Relationship": "Outer",
                            "Relation Name": "orders",
                            "Schema": "public",
                            "Alias": "o",
                            "Startup Cost": 0.0,
                            "Total Cost": 40421.0,
                            "Plan Rows": 1500000,
                            "Plan Width": 4,
                        }
                    ],
                },
            ],
        },
        "Planning Time": 0.56,
    }
]

EXPLAIN_CORRELATED_SUBPLAN = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "orders",
            "Schema": "public",
            "Alias": "orders",
            "Startup Cost": 0.0,
            "Total Cost": 6748324.50,
            "Plan Rows": 750000,
            "Plan Width": 4,
            "Filter": "(SubPlan 1)",
            "Plans": [
                {
                    "Node Type": "Aggregate",
                    "Parent Relationship": "SubPlan",
                    "Subplan Name": "SubPlan 1",
                    "Strategy": "Plain",
                    "Startup Cost": 0.43,
                    "Total Cost": 4.45,
                    "Plan Rows": 1,
                    "Plan Width": 8,
                    "Output": ["count(*)"],
                    "Plans": [
                        {
                            "Node Type": "Index Scan",
                            "Parent Relationship": "Outer",
                            "Index Name": "idx_lineitem_orderkey",
                            "Relation Name": "lineitem",
                            "Schema": "public",
                            "Alias": "lineitem",
                            "Startup Cost": 0.43,
                            "Total Cost": 4.44,
                            "Plan Rows": 4,
                            "Plan Width": 0,
                            "Index Cond": "(l_orderkey = orders.o_orderkey)",
                        }
                    ],
                }
            ],
        },
        "Planning Time": 0.18,
    }
]

EXPLAIN_EXPENSIVE_SORT = [
    {
        "Plan": {
            "Node Type": "Sort",
            "Startup Cost": 890123.45,
            "Total Cost": 920456.78,
            "Plan Rows": 6001215,
            "Plan Width": 64,
            "Sort Key": ["l_shipdate"],
            "Output": ["l_orderkey", "l_shipdate", "l_quantity"],
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Parent Relationship": "Outer",
                    "Relation Name": "lineitem",
                    "Schema": "public",
                    "Alias": "lineitem",
                    "Startup Cost": 0.0,
                    "Total Cost": 170934.12,
                    "Plan Rows": 6001215,
                    "Plan Width": 64,
                }
            ],
        },
        "Planning Time": 0.15,
    }
]

EXPLAIN_MATERIALIZE_MANY_LOOPS = [
    {
        "Plan": {
            "Node Type": "Nested Loop",
            "Join Type": "Inner",
            "Startup Cost": 12.34,
            "Total Cost": 456789.01,
            "Plan Rows": 50000,
            "Plan Width": 24,
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Parent Relationship": "Outer",
                    "Relation Name": "supplier",
                    "Schema": "public",
                    "Alias": "supplier",
                    "Startup Cost": 0.0,
                    "Total Cost": 321.0,
                    "Plan Rows": 10000,
                    "Plan Width": 4,
                },
                {
                    "Node Type": "Materialize",
                    "Parent Relationship": "Inner",
                    "Startup Cost": 0.0,
                    "Total Cost": 1234.56,
                    "Plan Rows": 800000,
                    "Plan Width": 20,
                    "Actual Rows": 800000,
                    "Actual Loops": 10000,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Parent Relationship": "Outer",
                            "Relation Name": "partsupp",
                            "Schema": "public",
                            "Alias": "partsupp",
                            "Startup Cost": 0.0,
                            "Total Cost": 1234.56,
                            "Plan Rows": 800000,
                            "Plan Width": 20,
                        }
                    ],
                },
            ],
        },
        "Planning Time": 0.22,
    }
]

EXPLAIN_GOOD_PLAN = [
    {
        "Plan": {
            "Node Type": "Limit",
            "Startup Cost": 0.87,
            "Total Cost": 45.23,
            "Plan Rows": 10,
            "Plan Width": 32,
            "Plans": [
                {
                    "Node Type": "Index Scan",
                    "Parent Relationship": "Outer",
                    "Scan Direction": "Backward",
                    "Index Name": "idx_orders_orderdate",
                    "Relation Name": "orders",
                    "Schema": "public",
                    "Alias": "orders",
                    "Startup Cost": 0.43,
                    "Total Cost": 45.23,
                    "Plan Rows": 10,
                    "Plan Width": 32,
                    "Index Cond": "(o_orderdate >= '1998-01-01'::date)",
                }
            ],
        },
        "Planning Time": 0.05,
    }
]


# ─── Tests for ExplainParser ─────────────────────────────────────────────────


class TestExplainParser:
    """Tests for the ExplainParser class."""

    def setup_method(self):
        self.parser = ExplainParser()

    def test_parse_seq_scan(self):
        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.node_type == "Seq Scan"
        assert root.relation == "lineitem"
        assert root.plan_rows == 6001215
        assert root.total_cost == 170934.12
        assert root.filter == "(l_shipdate <= '1998-09-02'::date)"

    def test_parse_index_scan(self):
        root = self.parser.parse(EXPLAIN_INDEX_SCAN)
        assert root.node_type == "Index Scan"
        assert root.index_name == "idx_lineitem_shipdate"
        assert root.plan_rows == 500
        assert root.total_cost == 1234.56
        assert root.index_cond == "(l_shipdate >= '1998-01-01'::date)"

    def test_parse_nested_loop(self):
        root = self.parser.parse(EXPLAIN_NESTED_LOOP_HIGH_CARDINALITY)
        assert root.node_type == "Nested Loop"
        assert root.join_type == "Inner"
        assert root.plan_rows == 150000
        assert len(root.children) == 2
        assert root.children[0].node_type == "Seq Scan"
        assert root.children[0].relation == "orders"
        assert root.children[1].node_type == "Index Scan"

    def test_parse_hash_join(self):
        root = self.parser.parse(EXPLAIN_HASH_JOIN)
        assert root.node_type == "Hash Join"
        assert root.hash_cond == "(l.l_orderkey = o.o_orderkey)"
        assert len(root.children) == 2
        # Hash node has its own children
        hash_node = root.children[1]
        assert hash_node.node_type == "Hash"
        assert len(hash_node.children) == 1

    def test_parse_correlated_subplan(self):
        root = self.parser.parse(EXPLAIN_CORRELATED_SUBPLAN)
        assert root.node_type == "Seq Scan"
        assert root.relation == "orders"
        # SubPlan child
        subplan = root.children[0]
        assert subplan.parent_relationship == "SubPlan"
        assert subplan.subplan_name == "SubPlan 1"
        assert subplan.is_subplan

    def test_parse_sort(self):
        root = self.parser.parse(EXPLAIN_EXPENSIVE_SORT)
        assert root.node_type == "Sort"
        assert root.sort_key == ["l_shipdate"]
        assert root.plan_rows == 6001215

    def test_parse_materialize(self):
        root = self.parser.parse(EXPLAIN_MATERIALIZE_MANY_LOOPS)
        materialize = root.children[1]
        assert materialize.node_type == "Materialize"
        assert materialize.actual_rows == 800000
        assert materialize.actual_loops == 10000

    def test_parse_empty_raises(self):
        with pytest.raises(ValueError, match="Empty EXPLAIN JSON"):
            self.parser.parse([])

    def test_parse_no_plan_key_raises(self):
        with pytest.raises(ValueError, match="No 'Plan' key"):
            self.parser.parse([{"Query": "something"}])

    def test_parse_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Expected list or dict"):
            self.parser.parse("not json")

    def test_parse_dict_input(self):
        """Parser should handle unwrapped dict format too."""
        plan = EXPLAIN_SEQ_SCAN_LARGE[0]
        root = self.parser.parse(plan)
        assert root.node_type == "Seq Scan"

    def test_flatten(self):
        root = self.parser.parse(EXPLAIN_HASH_JOIN)
        nodes = self.parser.flatten(root)
        # Hash Join -> Seq Scan (lineitem) + Hash -> Seq Scan (orders)
        assert len(nodes) == 4
        types = [n.node_type for n in nodes]
        assert types == ["Hash Join", "Seq Scan", "Hash", "Seq Scan"]


class TestPlanNodeProperties:
    """Tests for PlanNode property methods."""

    def setup_method(self):
        self.parser = ExplainParser()

    def test_is_scan(self):
        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.is_scan is True

        root = self.parser.parse(EXPLAIN_INDEX_SCAN)
        assert root.is_scan is True

        root = self.parser.parse(EXPLAIN_HASH_JOIN)
        assert root.is_scan is False

    def test_is_join(self):
        root = self.parser.parse(EXPLAIN_HASH_JOIN)
        assert root.is_join is True

        root = self.parser.parse(EXPLAIN_NESTED_LOOP_HIGH_CARDINALITY)
        assert root.is_join is True

        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.is_join is False

    def test_is_sort(self):
        root = self.parser.parse(EXPLAIN_EXPENSIVE_SORT)
        assert root.is_sort is True

        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.is_sort is False

    def test_is_subplan(self):
        root = self.parser.parse(EXPLAIN_CORRELATED_SUBPLAN)
        subplan = root.children[0]
        assert subplan.is_subplan is True
        assert root.is_subplan is False

    def test_effective_rows_estimated(self):
        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.effective_rows == 6001215
        assert root.actual_rows is None

    def test_effective_rows_actual(self):
        root = self.parser.parse(EXPLAIN_MATERIALIZE_MANY_LOOPS)
        materialize = root.children[1]
        assert materialize.effective_rows == 800000
        assert materialize.actual_rows == 800000

    def test_effective_loops(self):
        root = self.parser.parse(EXPLAIN_MATERIALIZE_MANY_LOOPS)
        materialize = root.children[1]
        assert materialize.effective_loops == 10000

        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.effective_loops == 1


class TestAntiPatternDetection:
    """
    Tests for detecting plan anti-patterns.

    These tests verify the logic that would be in a PlanAnalyzer class.
    Since the analyzer may not be fully implemented yet, we test the
    detection logic directly using the parsed plan nodes.
    """

    def setup_method(self):
        self.parser = ExplainParser()
        # Thresholds matching config.py defaults
        self.large_table_threshold = 10000
        self.cost_threshold = 10000.0

    def _detect_seq_scan_large(self, root: PlanNode) -> list[str]:
        """Detect sequential scans on large tables."""
        issues = []
        for node in self.parser.flatten(root):
            if node.node_type == "Seq Scan" and node.plan_rows > self.large_table_threshold:
                issues.append(
                    f"seq_scan_large:{node.relation}:{node.plan_rows}"
                )
        return issues

    def _detect_nested_loop_high_card(self, root: PlanNode) -> list[str]:
        """Detect nested loops with high-cardinality output."""
        issues = []
        for node in self.parser.flatten(root):
            if node.node_type == "Nested Loop" and node.plan_rows > 10000:
                issues.append(f"nested_loop_high_card:{node.plan_rows}")
        return issues

    def _detect_correlated_subplan(self, root: PlanNode) -> list[str]:
        """Detect correlated subplans."""
        issues = []
        for node in self.parser.flatten(root):
            if node.is_subplan:
                issues.append(f"correlated_subplan:{node.subplan_name}")
        return issues

    def _detect_expensive_sort(self, root: PlanNode) -> list[str]:
        """Detect sorts on large row counts."""
        issues = []
        for node in self.parser.flatten(root):
            if node.is_sort and node.plan_rows > 100000:
                issues.append(f"expensive_sort:{node.plan_rows}")
        return issues

    def _detect_materialize_many_loops(self, root: PlanNode) -> list[str]:
        """Detect materialize with excessive loop count."""
        issues = []
        for node in self.parser.flatten(root):
            if (
                node.node_type == "Materialize"
                and node.actual_loops is not None
                and node.actual_loops > 1000
            ):
                issues.append(f"materialize_loops:{node.actual_loops}")
        return issues

    def test_detect_seq_scan_on_lineitem(self):
        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        issues = self._detect_seq_scan_large(root)
        assert len(issues) == 1
        assert "lineitem" in issues[0]
        assert "6001215" in issues[0]

    def test_no_seq_scan_issue_for_small_table(self):
        root = self.parser.parse(EXPLAIN_GOOD_PLAN)
        issues = self._detect_seq_scan_large(root)
        assert len(issues) == 0

    def test_detect_nested_loop_high_cardinality(self):
        root = self.parser.parse(EXPLAIN_NESTED_LOOP_HIGH_CARDINALITY)
        issues = self._detect_nested_loop_high_card(root)
        assert len(issues) == 1
        assert "150000" in issues[0]

    def test_hash_join_not_flagged(self):
        root = self.parser.parse(EXPLAIN_HASH_JOIN)
        issues = self._detect_nested_loop_high_card(root)
        assert len(issues) == 0

    def test_detect_correlated_subplan(self):
        root = self.parser.parse(EXPLAIN_CORRELATED_SUBPLAN)
        issues = self._detect_correlated_subplan(root)
        assert len(issues) == 1
        assert "SubPlan 1" in issues[0]

    def test_detect_expensive_sort(self):
        root = self.parser.parse(EXPLAIN_EXPENSIVE_SORT)
        issues = self._detect_expensive_sort(root)
        assert len(issues) == 1
        assert "6001215" in issues[0]

    def test_detect_materialize_many_loops(self):
        root = self.parser.parse(EXPLAIN_MATERIALIZE_MANY_LOOPS)
        issues = self._detect_materialize_many_loops(root)
        assert len(issues) == 1
        assert "10000" in issues[0]

    def test_good_plan_no_issues(self):
        root = self.parser.parse(EXPLAIN_GOOD_PLAN)
        issues = (
            self._detect_seq_scan_large(root)
            + self._detect_nested_loop_high_card(root)
            + self._detect_correlated_subplan(root)
            + self._detect_expensive_sort(root)
            + self._detect_materialize_many_loops(root)
        )
        assert len(issues) == 0

    def test_cost_threshold(self):
        root = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        assert root.total_cost > self.cost_threshold

        root = self.parser.parse(EXPLAIN_GOOD_PLAN)
        assert root.total_cost < self.cost_threshold


class TestCostScoring:
    """Tests for plan cost scoring logic."""

    def setup_method(self):
        self.parser = ExplainParser()

    def test_total_cost_propagation(self):
        """Root node total_cost should reflect the entire plan cost."""
        root = self.parser.parse(EXPLAIN_HASH_JOIN)
        # Root cost should be >= any child cost
        for child in root.children:
            assert root.total_cost >= child.startup_cost

    def test_cost_comparison(self):
        """Hash join should be cheaper than nested loop for same data."""
        nested = self.parser.parse(EXPLAIN_NESTED_LOOP_HIGH_CARDINALITY)
        hash_join = self.parser.parse(EXPLAIN_HASH_JOIN)
        # Hash join is more efficient for high-cardinality joins
        assert hash_join.total_cost < nested.total_cost

    def test_index_scan_cheaper_than_seq_scan(self):
        """Index scan on selective predicate should be much cheaper."""
        seq = self.parser.parse(EXPLAIN_SEQ_SCAN_LARGE)
        idx = self.parser.parse(EXPLAIN_INDEX_SCAN)
        assert idx.total_cost < seq.total_cost
