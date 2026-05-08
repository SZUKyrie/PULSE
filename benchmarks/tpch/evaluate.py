"""
TpchEvaluator — Correctness and efficiency evaluation for TPC-H queries.

Evaluates:
1. Correctness: whether predicted SQL returns the same results as gold SQL
2. Efficiency: cost comparison via EXPLAIN, VES score, time ratio
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


@dataclass
class EfficiencyMetrics:
    """Efficiency comparison between predicted and gold SQL."""

    predicted_cost: float
    gold_cost: float
    ves_score: float  # Valid Efficiency Score = gold_cost / predicted_cost
    time_ratio: float  # gold_time / predicted_time (>1 means predicted is slower)
    predicted_time_ms: float = 0.0
    gold_time_ms: float = 0.0


class TpchEvaluator:
    """Evaluates TPC-H query correctness and efficiency."""

    def __init__(self, dsn: str):
        """
        Args:
            dsn: PostgreSQL connection string for the TPC-H database.
                 e.g., "postgresql://localhost:5432/tpch"
        """
        self.dsn = dsn

    def _get_connection(self):
        """Create a new database connection."""
        return psycopg2.connect(self.dsn)

    def _execute_sql(self, sql: str, conn) -> list[tuple[Any, ...]]:
        """Execute SQL and return results."""
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                return cur.fetchall()
            return []

    def _get_plan_cost(self, sql: str, conn) -> float:
        """Get the estimated total cost from EXPLAIN."""
        explain_sql = f"EXPLAIN (FORMAT JSON, COSTS) {sql}"
        with conn.cursor() as cur:
            cur.execute(explain_sql)
            result = cur.fetchone()
            if result:
                plan = result[0]
                if isinstance(plan, list) and len(plan) > 0:
                    return float(plan[0].get("Plan", {}).get("Total Cost", 0.0))
            return 0.0

    def _time_execution(self, sql: str, conn) -> float:
        """Time the actual execution of a query in milliseconds."""
        # Use EXPLAIN ANALYZE to get accurate timing
        explain_sql = f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}"
        with conn.cursor() as cur:
            cur.execute(explain_sql)
            result = cur.fetchone()
            if result:
                plan = result[0]
                if isinstance(plan, list) and len(plan) > 0:
                    return float(plan[0].get("Execution Time", 0.0))
            return 0.0

    def _normalize_results(
        self, results: list[tuple[Any, ...]]
    ) -> set[tuple[Any, ...]]:
        """Normalize result set for order-independent comparison."""
        normalized = set()
        for row in results:
            normalized_row = tuple(
                round(cell, 2) if isinstance(cell, float) else cell
                for cell in row
            )
            normalized.add(normalized_row)
        return normalized

    def evaluate_correctness(self, predicted: str, gold: str) -> bool:
        """
        Evaluate whether predicted SQL returns the same results as gold SQL.

        Args:
            predicted: The predicted SQL query.
            gold: The gold-standard SQL query.

        Returns:
            True if result sets match (order-independent), False otherwise.
        """
        conn = self._get_connection()
        try:
            try:
                predicted_results = self._execute_sql(predicted, conn)
            except Exception:
                return False

            try:
                gold_results = self._execute_sql(gold, conn)
            except Exception:
                return False

            predicted_set = self._normalize_results(predicted_results)
            gold_set = self._normalize_results(gold_results)
            return predicted_set == gold_set
        finally:
            conn.close()

    def evaluate_efficiency(
        self, predicted_sql: str, gold_sql: str, conn=None
    ) -> EfficiencyMetrics:
        """
        Compare efficiency of predicted vs gold SQL using EXPLAIN costs and timing.

        Args:
            predicted_sql: The predicted SQL query.
            gold_sql: The gold-standard SQL query.
            conn: Optional existing connection. If None, creates a new one.

        Returns:
            EfficiencyMetrics with cost and time comparisons.
        """
        own_conn = conn is None
        if own_conn:
            conn = self._get_connection()

        try:
            # Get plan costs
            predicted_cost = self._get_plan_cost(predicted_sql, conn)
            gold_cost = self._get_plan_cost(gold_sql, conn)

            # Time actual execution
            predicted_time = self._time_execution(predicted_sql, conn)
            gold_time = self._time_execution(gold_sql, conn)

            # VES = gold_cost / predicted_cost
            # Score of 1.0 means same cost; >1 means predicted is more efficient
            # <1 means predicted is less efficient
            ves_score = gold_cost / predicted_cost if predicted_cost > 0 else 0.0

            # Time ratio: predicted_time / gold_time
            # >1 means predicted is slower; <1 means predicted is faster
            time_ratio = (
                predicted_time / gold_time if gold_time > 0 else float("inf")
            )

            return EfficiencyMetrics(
                predicted_cost=predicted_cost,
                gold_cost=gold_cost,
                ves_score=ves_score,
                time_ratio=time_ratio,
                predicted_time_ms=predicted_time,
                gold_time_ms=gold_time,
            )
        finally:
            if own_conn:
                conn.close()

    def evaluate_batch(
        self, results: list[tuple[int, str, str]]
    ) -> dict:
        """
        Evaluate a batch of TPC-H results.

        Args:
            results: List of (query_id, predicted_sql, gold_sql) tuples.

        Returns:
            Dict with aggregate metrics:
                - total: number of queries
                - correct: number of correct predictions
                - accuracy: correctness ratio
                - avg_ves: average VES score (only for correct predictions)
                - avg_time_ratio: average time ratio
                - per_query: list of per-query results
        """
        total = len(results)
        correct = 0
        ves_scores = []
        time_ratios = []
        per_query = []

        conn = self._get_connection()
        try:
            for query_id, predicted_sql, gold_sql in results:
                entry: dict[str, Any] = {"query_id": query_id}

                # Check correctness
                try:
                    predicted_results = self._execute_sql(predicted_sql, conn)
                    gold_results = self._execute_sql(gold_sql, conn)
                    is_correct = self._normalize_results(
                        predicted_results
                    ) == self._normalize_results(gold_results)
                except Exception as e:
                    is_correct = False
                    entry["error"] = str(e)

                entry["correct"] = is_correct
                if is_correct:
                    correct += 1

                # Evaluate efficiency (even for incorrect queries, for analysis)
                try:
                    metrics = self.evaluate_efficiency(
                        predicted_sql, gold_sql, conn=conn
                    )
                    entry["predicted_cost"] = metrics.predicted_cost
                    entry["gold_cost"] = metrics.gold_cost
                    entry["ves_score"] = metrics.ves_score
                    entry["time_ratio"] = metrics.time_ratio
                    entry["predicted_time_ms"] = metrics.predicted_time_ms
                    entry["gold_time_ms"] = metrics.gold_time_ms

                    if is_correct:
                        ves_scores.append(metrics.ves_score)
                        time_ratios.append(metrics.time_ratio)
                except Exception as e:
                    entry["efficiency_error"] = str(e)

                per_query.append(entry)
        finally:
            conn.close()

        avg_ves = sum(ves_scores) / len(ves_scores) if ves_scores else 0.0
        avg_time_ratio = (
            sum(time_ratios) / len(time_ratios) if time_ratios else 0.0
        )

        return {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0.0,
            "avg_ves": avg_ves,
            "avg_time_ratio": avg_time_ratio,
            "per_query": per_query,
        }
