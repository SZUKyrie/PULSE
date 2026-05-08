from __future__ import annotations

"""
Integration tests for the full PULSE pipeline.

Tests the end-to-end flow with mocked LLM and database components
to verify iteration count, termination conditions, and correct
composition of pipeline stages.

The real pipeline uses LangGraph (src.pipeline.Pipeline), but these tests
use a simplified mock-compatible version to avoid DB/LLM dependencies.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.analyzer.explain import ExplainParser, PlanNode
from src.analyzer.patterns import AntiPattern, AnalysisContext, Severity, detect_all
from src.analyzer.scorer import PlanReport, PlanScorer
from src.feedback.controller import IterationController
from src.config import settings


# ─── Mock Plan Data ──────────────────────────────────────────────────────────

# Initial plan: expensive sequential scan (should trigger feedback)
PLAN_ITERATION_0 = {
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

# After first feedback: improved but still over threshold
PLAN_ITERATION_1 = {
    "Plan": {
        "Node Type": "Bitmap Heap Scan",
        "Relation Name": "lineitem",
        "Schema": "public",
        "Alias": "lineitem",
        "Startup Cost": 1234.56,
        "Total Cost": 45678.90,
        "Plan Rows": 500000,
        "Plan Width": 12,
        "Filter": "(l_quantity > 10)",
        "Plans": [
            {
                "Node Type": "Bitmap Index Scan",
                "Parent Relationship": "Outer",
                "Index Name": "idx_lineitem_shipdate",
                "Startup Cost": 0.0,
                "Total Cost": 1234.56,
                "Plan Rows": 500000,
                "Plan Width": 0,
                "Index Cond": "(l_shipdate >= '1998-01-01'::date AND l_shipdate <= '1998-09-02'::date)",
            }
        ],
    }
}

# After second feedback: good plan (below threshold)
PLAN_ITERATION_2 = {
    "Plan": {
        "Node Type": "Index Scan",
        "Index Name": "idx_lineitem_shipdate",
        "Relation Name": "lineitem",
        "Schema": "public",
        "Alias": "lineitem",
        "Startup Cost": 0.43,
        "Total Cost": 5678.90,
        "Plan Rows": 5000,
        "Plan Width": 12,
        "Index Cond": "(l_shipdate >= '1998-09-01'::date AND l_shipdate <= '1998-09-02'::date)",
    }
}


# ─── Mock SQL Responses ──────────────────────────────────────────────────────

SQL_INITIAL = "SELECT l_orderkey, l_quantity FROM lineitem WHERE l_shipdate <= '1998-09-02'"
SQL_IMPROVED_1 = "SELECT l_orderkey, l_quantity FROM lineitem WHERE l_shipdate >= '1998-01-01' AND l_shipdate <= '1998-09-02' AND l_quantity > 10"
SQL_IMPROVED_2 = "SELECT l_orderkey, l_quantity FROM lineitem WHERE l_shipdate >= '1998-09-01' AND l_shipdate <= '1998-09-02' AND l_quantity > 10"


# ─── Analysis context for mock plans ────────────────────────────────────────

MOCK_CONTEXT = AnalysisContext(
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


# ─── Pipeline Test Harness ───────────────────────────────────────────────────


class MockPipeline:
    """
    Mock-compatible pipeline that mimics src.pipeline.Pipeline's behavior.

    Uses the real analyzer, scorer, and controller but mocks out the LLM
    and database layers. This tests the orchestration logic in isolation.
    """

    def __init__(
        self,
        llm_responses: list[str],
        plan_responses: list[dict],
        context: AnalysisContext | None = None,
        max_iterations: int = 3,
        cost_threshold: float = 50000.0,
    ):
        self.llm_responses = list(llm_responses)
        self.plan_responses = list(plan_responses)
        self.context = context or MOCK_CONTEXT

        self.parser = ExplainParser()
        self.scorer = PlanScorer(cost_threshold=cost_threshold)
        self.controller = IterationController(
            max_iterations=max_iterations,
            min_improvement=0.1,
            cost_threshold=cost_threshold,
        )

        # Track calls for assertions
        self.llm_call_count = 0
        self.explain_call_count = 0
        self.history: list[PlanReport] = []

    def _generate_sql(self) -> str:
        """Simulate LLM SQL generation."""
        if self.llm_call_count < len(self.llm_responses):
            sql = self.llm_responses[self.llm_call_count]
        else:
            sql = self.llm_responses[-1]  # Repeat last
        self.llm_call_count += 1
        return sql

    def _explain(self) -> dict:
        """Simulate DB EXPLAIN."""
        if self.explain_call_count < len(self.plan_responses):
            plan = self.plan_responses[self.explain_call_count]
        else:
            raise RuntimeError("No more plan responses available")
        self.explain_call_count += 1
        return plan

    def run(self, question: str) -> dict:
        """
        Execute the pipeline.

        Returns dict with:
            - sql: final SQL
            - iterations: number of feedback iterations
            - costs: list of costs per iteration
            - terminated_reason: why the loop stopped
            - history: list of PlanReport objects
        """
        sql = self._generate_sql()
        terminated_reason = "max_iterations"

        while True:
            # Get and analyze plan
            try:
                plan_json = self._explain()
            except RuntimeError:
                terminated_reason = "explain_error"
                break

            root = self.parser.parse([plan_json])
            patterns = detect_all(root, self.context)
            report = self.scorer.score(root, patterns)
            self.history.append(report)

            # Check termination via controller
            if not self.controller.should_continue(self.history):
                if report.is_acceptable:
                    terminated_reason = "plan_acceptable"
                elif len(self.history) >= self.controller.max_iterations:
                    terminated_reason = "max_iterations"
                elif len(self.history) >= 2:
                    # Check if it's a convergence/regression issue
                    prev = self.history[-2]
                    improvement = self.scorer.compare(prev, report)
                    if improvement < 1.05:
                        terminated_reason = "convergence"
                    else:
                        terminated_reason = "no_improvement"
                else:
                    terminated_reason = "controller_stop"
                break

            # Generate improved SQL using feedback
            sql = self._generate_sql()

        costs = [r.total_cost for r in self.history]
        return {
            "sql": sql,
            "iterations": len(self.history),
            "costs": costs,
            "terminated_reason": terminated_reason,
            "history": self.history,
        }


# ─── Integration Tests ───────────────────────────────────────────────────────


class TestPipelineIntegration:
    """Integration tests for the full pipeline with mocked components."""

    def test_pipeline_converges_to_acceptable_plan(self):
        """Pipeline should stop when plan becomes acceptable (cost below threshold)."""
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL, SQL_IMPROVED_1, SQL_IMPROVED_2],
            plan_responses=[PLAN_ITERATION_0, PLAN_ITERATION_1, PLAN_ITERATION_2],
            max_iterations=5,
        )
        result = pipeline.run("Find lineitems shipped before a date")

        # Final plan (cost 5678.90) should be acceptable
        assert result["terminated_reason"] == "plan_acceptable"
        assert result["costs"][-1] < 50000.0
        # Costs should decrease
        assert result["costs"][0] > result["costs"][-1]

    def test_pipeline_stops_at_max_iterations(self):
        """Pipeline should stop after max_iterations even if cost is still high."""
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL, SQL_IMPROVED_1, SQL_IMPROVED_1],
            plan_responses=[PLAN_ITERATION_0, PLAN_ITERATION_0, PLAN_ITERATION_0],
            max_iterations=2,
        )
        result = pipeline.run("Find lineitems")

        # Should be capped by max iterations
        assert result["iterations"] <= 2

    def test_pipeline_stops_on_good_initial_plan(self):
        """If the first plan is already good, no feedback loop needed."""
        good_plan = {
            "Plan": {
                "Node Type": "Index Scan",
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
        }

        pipeline = MockPipeline(
            llm_responses=["SELECT * FROM orders WHERE o_orderdate >= '1998-01-01' LIMIT 10"],
            plan_responses=[good_plan],
            max_iterations=3,
        )
        result = pipeline.run("Find recent orders")

        assert result["iterations"] == 1
        assert result["terminated_reason"] == "plan_acceptable"
        assert result["costs"] == [45.23]
        # LLM should only be called once (initial generation)
        assert pipeline.llm_call_count == 1

    def test_pipeline_stops_on_convergence(self):
        """If cost stops improving, pipeline should stop."""
        # Same plan repeated: no improvement between iterations
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL, SQL_INITIAL],
            plan_responses=[PLAN_ITERATION_0, PLAN_ITERATION_0],
            max_iterations=5,
        )
        result = pipeline.run("Find lineitems")

        # Should stop after 2 iterations due to no improvement
        assert result["iterations"] == 2
        assert result["terminated_reason"] in ("convergence", "no_improvement", "controller_stop")

    def test_pipeline_handles_explain_error(self):
        """If EXPLAIN has no more responses, pipeline should stop gracefully."""
        pipeline = MockPipeline(
            llm_responses=["INVALID SQL"],
            plan_responses=[],  # No plans available
            max_iterations=3,
        )
        result = pipeline.run("Do something")

        assert result["terminated_reason"] == "explain_error"
        assert result["iterations"] == 0

    def test_pipeline_iteration_count(self):
        """Verify LLM and EXPLAIN call counts match iterations."""
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL, SQL_IMPROVED_1, SQL_IMPROVED_2],
            plan_responses=[PLAN_ITERATION_0, PLAN_ITERATION_1, PLAN_ITERATION_2],
            max_iterations=5,
        )
        pipeline.run("Find lineitems")

        # explain_call_count should equal number of iterations
        assert pipeline.explain_call_count == len(pipeline.history)

    def test_pipeline_cost_monotonically_decreasing(self):
        """In a successful optimization, costs should decrease each iteration."""
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL, SQL_IMPROVED_1, SQL_IMPROVED_2],
            plan_responses=[PLAN_ITERATION_0, PLAN_ITERATION_1, PLAN_ITERATION_2],
            max_iterations=5,
        )
        result = pipeline.run("Find lineitems")

        costs = result["costs"]
        for i in range(1, len(costs)):
            assert costs[i] < costs[i - 1], f"Cost did not decrease at iteration {i}"

    def test_pipeline_reports_track_anti_patterns(self):
        """Each report in history should have anti-pattern details."""
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL, SQL_IMPROVED_1, SQL_IMPROVED_2],
            plan_responses=[PLAN_ITERATION_0, PLAN_ITERATION_1, PLAN_ITERATION_2],
            max_iterations=5,
        )
        result = pipeline.run("Find lineitems")

        # First iteration should have HIGH severity issues (seq scan on 6M rows)
        first_report = result["history"][0]
        assert first_report.has_high_severity
        assert first_report.total_cost > 100000

        # Last iteration should be acceptable
        last_report = result["history"][-1]
        assert last_report.is_acceptable


class TestPipelineEdgeCases:
    """Edge case tests for the pipeline."""

    def test_single_iteration_pipeline(self):
        """Pipeline with max_iterations=1 should do exactly one plan check."""
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL],
            plan_responses=[PLAN_ITERATION_0],
            max_iterations=1,
        )
        result = pipeline.run("Find lineitems")

        assert result["iterations"] == 1
        assert pipeline.llm_call_count == 1
        assert pipeline.explain_call_count == 1

    def test_zero_cost_plan_is_acceptable(self):
        """Plan with zero cost should be treated as acceptable."""
        zero_plan = {
            "Plan": {
                "Node Type": "Result",
                "Startup Cost": 0.0,
                "Total Cost": 0.0,
                "Plan Rows": 0,
                "Plan Width": 0,
            }
        }
        pipeline = MockPipeline(
            llm_responses=["SELECT 1"],
            plan_responses=[zero_plan],
        )
        result = pipeline.run("Select constant")

        assert result["terminated_reason"] == "plan_acceptable"
        assert result["costs"] == [0.0]

    def test_parallel_plan_not_flagged(self):
        """A parallel seq scan on a small table should not trigger issues."""
        parallel_plan = {
            "Plan": {
                "Node Type": "Gather",
                "Startup Cost": 1000.0,
                "Total Cost": 5000.0,
                "Plan Rows": 100,
                "Plan Width": 8,
                "Workers Planned": 2,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Parent Relationship": "Outer",
                        "Relation Name": "small_table",
                        "Schema": "public",
                        "Alias": "small_table",
                        "Startup Cost": 0.0,
                        "Total Cost": 4000.0,
                        "Plan Rows": 5000,
                        "Plan Width": 8,
                    }
                ],
            }
        }
        # Use context without small_table in table_sizes (so detector won't fire)
        context = AnalysisContext(
            table_sizes={"small_table": 5000},
            available_indexes={},
            cost_threshold=50000.0,
        )
        pipeline = MockPipeline(
            llm_responses=["SELECT COUNT(*) FROM small_table"],
            plan_responses=[parallel_plan],
            context=context,
        )
        result = pipeline.run("Count rows")

        assert result["terminated_reason"] == "plan_acceptable"

    def test_multiple_anti_patterns_in_one_plan(self):
        """A plan with multiple anti-patterns should detect all of them."""
        complex_bad_plan = {
            "Plan": {
                "Node Type": "Sort",
                "Startup Cost": 500000.0,
                "Total Cost": 600000.0,
                "Plan Rows": 1000000,
                "Plan Width": 32,
                "Sort Key": ["l_orderkey"],
                "Plans": [
                    {
                        "Node Type": "Nested Loop",
                        "Parent Relationship": "Outer",
                        "Join Type": "Inner",
                        "Startup Cost": 0.0,
                        "Total Cost": 400000.0,
                        "Plan Rows": 1000000,
                        "Plan Width": 32,
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Parent Relationship": "Outer",
                                "Relation Name": "lineitem",
                                "Schema": "public",
                                "Alias": "lineitem",
                                "Startup Cost": 0.0,
                                "Total Cost": 170000.0,
                                "Plan Rows": 6000000,
                                "Plan Width": 12,
                                "Filter": "(l_shipdate > '1990-01-01'::date)",
                            },
                            {
                                "Node Type": "Index Scan",
                                "Parent Relationship": "Inner",
                                "Relation Name": "orders",
                                "Schema": "public",
                                "Alias": "orders",
                                "Index Name": "idx_orders_pk",
                                "Startup Cost": 0.0,
                                "Total Cost": 0.5,
                                "Plan Rows": 2000,
                                "Plan Width": 20,
                            },
                        ],
                    }
                ],
            }
        }
        pipeline = MockPipeline(
            llm_responses=[SQL_INITIAL],
            plan_responses=[complex_bad_plan],
            max_iterations=1,
        )
        result = pipeline.run("Complex query")

        # Should detect multiple issues
        report = result["history"][0]
        assert report.pattern_count >= 2  # At least seq scan + nested loop or sort
        assert report.has_high_severity
