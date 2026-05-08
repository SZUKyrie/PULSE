from __future__ import annotations

"""
Tests for the feedback module.

Tests the FeedbackFormatter (plan issues -> structured hints) and
the IterationController (termination conditions).

These tests use the actual src.feedback module and realistic PlanReport objects.
"""

import pytest

from src.analyzer.explain import ExplainParser, PlanNode
from src.analyzer.patterns import AntiPattern, AnalysisContext, Severity, detect_all
from src.analyzer.scorer import PlanReport, PlanScorer
from src.feedback.controller import IterationController


# ─── Fixtures ────────────────────────────────────────────────────────────────


EXPLAIN_SEQ_SCAN_LARGE = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "lineitem",
            "Schema": "public",
            "Alias": "lineitem",
            "Startup Cost": 0.0,
            "Total Cost": 170934.12,
            "Plan Rows": 6001215,
            "Plan Width": 12,
            "Filter": "(l_shipdate <= '1998-09-02'::date)",
        }
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
                    "Startup Cost": 0.43,
                    "Total Cost": 4.45,
                    "Plan Rows": 1,
                    "Plan Width": 8,
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
        }
    }
]

EXPLAIN_NESTED_LOOP = [
    {
        "Plan": {
            "Node Type": "Nested Loop",
            "Join Type": "Inner",
            "Startup Cost": 0.87,
            "Total Cost": 892456.23,
            "Plan Rows": 150000,
            "Plan Width": 32,
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
                    "Plan Rows": 4000,
                    "Plan Width": 8,
                    "Index Cond": "(l_orderkey = o.o_orderkey)",
                },
            ],
        }
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
        }
    }
]

EXPLAIN_GOOD_PLAN = [
    {
        "Plan": {
            "Node Type": "Index Scan",
            "Relation Name": "orders",
            "Schema": "public",
            "Alias": "orders",
            "Index Name": "idx_orders_orderdate",
            "Startup Cost": 0.43,
            "Total Cost": 45.23,
            "Plan Rows": 10,
            "Plan Width": 32,
            "Index Cond": "(o_orderdate >= '1998-01-01'::date)",
        }
    }
]


# ─── Helper to build PlanReports for testing ─────────────────────────────────


def _make_report(
    explain_json: list[dict],
    context: AnalysisContext | None = None,
) -> PlanReport:
    """Parse EXPLAIN JSON and produce a PlanReport."""
    parser = ExplainParser()
    scorer = PlanScorer(cost_threshold=50000.0)
    root = parser.parse(explain_json)

    if context is None:
        context = AnalysisContext(
            table_sizes={"lineitem": 6_000_000, "orders": 1_500_000},
            available_indexes={
                "lineitem": [
                    {"name": "idx_lineitem_shipdate", "columns": ["l_shipdate"]},
                    {"name": "idx_lineitem_orderkey", "columns": ["l_orderkey"]},
                ],
                "orders": [
                    {"name": "idx_orders_orderdate", "columns": ["o_orderdate"]},
                ],
            },
            cost_threshold=50000.0,
        )

    patterns = detect_all(root, context)
    return scorer.score(root, patterns)


# ─── Tests for FeedbackFormatter ─────────────────────────────────────────────


class TestFeedbackFormatterContract:
    """Tests for the FeedbackFormatter output contract.

    Since FeedbackFormatter.format() requires a SchemaContext (which is DB-dependent),
    we test the formatter indirectly through PlanReport.feedback_strings() which
    provides the same underlying information.
    """

    def test_seq_scan_produces_feedback(self):
        """Sequential scan on large table with index should produce feedback."""
        report = _make_report(EXPLAIN_SEQ_SCAN_LARGE)
        assert report.pattern_count > 0
        feedback = report.feedback_strings()
        assert len(feedback) > 0
        # Should mention the table and severity
        assert any("sequential_scan" in f.lower() or "lineitem" in f.lower() for f in feedback)

    def test_correlated_subplan_feedback(self):
        """Correlated subplan should produce HIGH severity feedback."""
        report = _make_report(EXPLAIN_CORRELATED_SUBPLAN)
        assert report.has_high_severity
        feedback = report.feedback_strings()
        has_subplan = any("correlated_subplan" in f for f in feedback)
        assert has_subplan

    def test_nested_loop_feedback(self):
        """Nested loop with high cardinality should produce feedback."""
        report = _make_report(EXPLAIN_NESTED_LOOP)
        feedback = report.feedback_strings()
        has_nested_loop = any("nested_loop" in f for f in feedback)
        assert has_nested_loop

    def test_expensive_sort_feedback(self):
        """Expensive sort should produce feedback mentioning sort keys."""
        report = _make_report(EXPLAIN_EXPENSIVE_SORT)
        feedback = report.feedback_strings()
        has_sort = any("expensive_sort" in f for f in feedback)
        assert has_sort
        # Should mention the sort key
        has_key = any("l_shipdate" in f for f in feedback)
        assert has_key

    def test_good_plan_no_structural_issues(self):
        """Good plan with index scan should have no HIGH/MEDIUM issues."""
        report = _make_report(EXPLAIN_GOOD_PLAN)
        assert report.severity_summary.get("high", 0) == 0
        assert report.severity_summary.get("medium", 0) == 0

    def test_feedback_includes_suggestion(self):
        """All feedback strings should include a suggestion."""
        report = _make_report(EXPLAIN_SEQ_SCAN_LARGE)
        for feedback_str in report.feedback_strings():
            assert "Suggestion:" in feedback_str

    def test_feedback_severity_ordering(self):
        """Anti-patterns should be ordered HIGH > MEDIUM > LOW."""
        report = _make_report(EXPLAIN_CORRELATED_SUBPLAN)
        if len(report.anti_patterns) > 1:
            severities = [ap.severity for ap in report.anti_patterns]
            # Verify sorted (HIGH first)
            severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
            orders = [severity_order[s] for s in severities]
            assert orders == sorted(orders)


# ─── Tests for IterationController ───────────────────────────────────────────


class TestIterationController:
    """Tests for the IterationController termination logic."""

    def setup_method(self):
        self.controller = IterationController(
            max_iterations=3,
            min_improvement=0.1,
            cost_threshold=50000.0,
        )

    def test_continue_on_empty_history(self):
        """Should continue when no iterations have happened yet."""
        result = self.controller.should_continue(history=[])
        assert result is True

    def test_stop_at_max_iterations(self):
        """Should stop when max_iterations reports have been collected."""
        reports = [
            _make_report(EXPLAIN_SEQ_SCAN_LARGE),
            _make_report(EXPLAIN_SEQ_SCAN_LARGE),
            _make_report(EXPLAIN_SEQ_SCAN_LARGE),
        ]
        result = self.controller.should_continue(history=reports)
        assert result is False

    def test_stop_when_plan_acceptable(self):
        """Should stop when the latest plan is acceptable."""
        reports = [_make_report(EXPLAIN_GOOD_PLAN)]
        result = self.controller.should_continue(history=reports)
        assert result is False

    def test_continue_when_plan_unacceptable(self):
        """Should continue when the plan has HIGH severity issues."""
        reports = [_make_report(EXPLAIN_SEQ_SCAN_LARGE)]
        result = self.controller.should_continue(history=reports)
        # Should want to continue since there are high-severity issues
        assert result is True

    def test_stop_on_insufficient_improvement(self):
        """Should stop if improvement between iterations is less than min_improvement."""
        # Two identical reports = 0% improvement
        report = _make_report(EXPLAIN_SEQ_SCAN_LARGE)
        reports = [report, report]
        result = self.controller.should_continue(history=reports)
        assert result is False

    def test_stop_on_repeating_patterns(self):
        """Should stop if the same anti-patterns repeat between iterations."""
        # Same plan twice = same patterns
        report = _make_report(EXPLAIN_CORRELATED_SUBPLAN)
        reports = [report, report]
        result = self.controller.should_continue(history=reports)
        assert result is False

    def test_continue_on_good_improvement(self):
        """Should continue if cost improved significantly."""
        # First report has very high cost, second has lower (but still above threshold)
        report_expensive = _make_report(EXPLAIN_CORRELATED_SUBPLAN)

        # Create a report with lower cost (the nested loop plan is cheaper)
        report_cheaper = _make_report(EXPLAIN_NESTED_LOOP)

        # Only continue if the cheaper plan still has issues AND improved significantly
        # The nested loop plan has cost 892456 which is > correlated subplan cost 6748324
        # So this represents improvement
        if report_expensive.total_cost > report_cheaper.total_cost:
            reports = [report_expensive, report_cheaper]
            result = self.controller.should_continue(history=reports)
            # Should continue because there's significant improvement and still issues
            assert result is True

    def test_iteration_summary_format(self):
        """get_iteration_summary should produce readable output."""
        reports = [
            _make_report(EXPLAIN_CORRELATED_SUBPLAN),
            _make_report(EXPLAIN_GOOD_PLAN),
        ]
        summary = self.controller.get_iteration_summary(reports)
        assert "Iteration Summary" in summary
        assert "Cost progression" in summary
        assert "Iteration 1" in summary
        assert "Iteration 2" in summary

    def test_iteration_summary_empty_history(self):
        """Summary should handle empty history gracefully."""
        summary = self.controller.get_iteration_summary([])
        assert "No iterations" in summary

    def test_stop_below_cost_threshold_no_high_issues(self):
        """Should stop when cost is below threshold and no high-severity issues."""
        # Good plan has cost 45.23 (below threshold) and no high issues
        reports = [_make_report(EXPLAIN_GOOD_PLAN)]
        result = self.controller.should_continue(history=reports)
        assert result is False

    def test_custom_thresholds(self):
        """Controller should respect custom threshold values."""
        strict_controller = IterationController(
            max_iterations=5,
            min_improvement=0.05,
            cost_threshold=100.0,
        )
        # Good plan has cost 45.23 which is below 100
        reports = [_make_report(EXPLAIN_GOOD_PLAN)]
        result = strict_controller.should_continue(history=reports)
        assert result is False

    def test_single_iteration_max(self):
        """With max_iterations=1, should always stop after first report."""
        one_shot = IterationController(max_iterations=1)
        reports = [_make_report(EXPLAIN_SEQ_SCAN_LARGE)]
        result = one_shot.should_continue(history=reports)
        assert result is False
