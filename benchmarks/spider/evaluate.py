"""
SpiderEvaluator — Execution accuracy evaluation for Spider benchmark.

Uses the standard NL2SQL evaluation methodology:
- Run predicted SQL and gold SQL on the same database
- Compare result sets (order-independent)
- A prediction is correct if result sets match
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class SpiderEvaluator:
    """Evaluates NL2SQL predictions against Spider gold SQL using execution accuracy."""

    def __init__(self, db_dir: str):
        """
        Args:
            db_dir: Path to Spider database directory.
                    Contains subdirectories like concert_singer/concert_singer.sqlite
        """
        self.db_dir = Path(db_dir)

    def _get_db_path(self, db_name: str) -> Path:
        """Resolve the SQLite database path for a given db_name."""
        db_path = self.db_dir / db_name / f"{db_name}.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {db_path}. "
                f"Ensure the Spider databases are extracted in {self.db_dir}"
            )
        return db_path

    def _execute_sql(self, sql: str, db_path: Path) -> list[tuple[Any, ...]]:
        """Execute SQL on a SQLite database and return results."""
        conn = sqlite3.connect(str(db_path))
        conn.text_factory = str
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            results = cursor.fetchall()
            return results
        finally:
            conn.close()

    def _normalize_result_set(
        self, results: list[tuple[Any, ...]]
    ) -> set[tuple[Any, ...]]:
        """
        Normalize results for order-independent comparison.
        Converts each row to a tuple and collects into a set.
        """
        normalized = set()
        for row in results:
            # Convert each cell to a comparable form
            normalized_row = tuple(
                str(cell).strip().lower() if isinstance(cell, str) else cell
                for cell in row
            )
            normalized.append(normalized_row) if False else normalized.add(
                normalized_row
            )
        return normalized

    def evaluate_execution_accuracy(
        self, predicted: str, gold: str, db_name: str
    ) -> bool:
        """
        Evaluate whether predicted SQL produces the same results as gold SQL.

        Args:
            predicted: The predicted SQL query.
            gold: The gold-standard SQL query.
            db_name: The Spider database name (e.g., "concert_singer").

        Returns:
            True if result sets match (order-independent), False otherwise.
        """
        db_path = self._get_db_path(db_name)

        try:
            predicted_results = self._execute_sql(predicted, db_path)
        except Exception:
            # If predicted SQL fails to execute, it's wrong
            return False

        try:
            gold_results = self._execute_sql(gold, db_path)
        except Exception:
            # If gold SQL fails, we can't evaluate — treat as failure
            return False

        # Compare result sets (order-independent)
        predicted_set = self._normalize_result_set(predicted_results)
        gold_set = self._normalize_result_set(gold_results)

        return predicted_set == gold_set

    def evaluate_batch(
        self, results: list[tuple[str, str, str]]
    ) -> dict:
        """
        Evaluate a batch of (predicted_sql, gold_sql, db_name) tuples.

        Args:
            results: List of (predicted_sql, gold_sql, db_name) tuples.

        Returns:
            Dict with metrics:
                - total: number of questions
                - correct: number of correct predictions
                - accuracy: execution accuracy (correct / total)
                - errors: number of queries that raised exceptions
                - by_db: per-database accuracy breakdown
        """
        total = len(results)
        correct = 0
        errors = 0
        db_results: dict[str, dict[str, int]] = {}

        for predicted, gold, db_name in results:
            if db_name not in db_results:
                db_results[db_name] = {"total": 0, "correct": 0}
            db_results[db_name]["total"] += 1

            try:
                if self.evaluate_execution_accuracy(predicted, gold, db_name):
                    correct += 1
                    db_results[db_name]["correct"] += 1
            except FileNotFoundError:
                errors += 1
            except Exception:
                errors += 1

        by_db = {
            db: {
                "total": stats["total"],
                "correct": stats["correct"],
                "accuracy": stats["correct"] / stats["total"] if stats["total"] > 0 else 0.0,
            }
            for db, stats in db_results.items()
        }

        return {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0.0,
            "errors": errors,
            "by_db": by_db,
        }
