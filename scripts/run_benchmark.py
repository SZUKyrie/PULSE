from __future__ import annotations

#!/usr/bin/env python3
"""
run_benchmark.py — CLI to run PULSE benchmarks.

Usage:
    python scripts/run_benchmark.py --benchmark spider --model qwen2.5-coder:7b --output results.json
    python scripts/run_benchmark.py --benchmark tpch --model deepseek-v3 --output results.json
"""

import json
import sys
import time
from pathlib import Path

import click

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.llm_client import LLMClient
from src.db import Database


@click.command()
@click.option(
    "--benchmark",
    type=click.Choice(["spider", "tpch"]),
    required=True,
    help="Benchmark to run: spider (correctness) or tpch (correctness + efficiency).",
)
@click.option(
    "--model",
    type=str,
    default=None,
    help="Model name to use. Defaults to config setting.",
)
@click.option(
    "--output",
    type=click.Path(),
    default="benchmark_results.json",
    help="Path to save results JSON.",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True),
    default=None,
    help="Path to benchmark data directory.",
)
@click.option(
    "--db-url",
    type=str,
    default=None,
    help="Database URL (for TPC-H). Defaults to config setting.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Limit number of queries to run (for debugging).",
)
@click.option(
    "--use-feedback/--no-feedback",
    default=True,
    help="Enable/disable plan feedback loop.",
)
@click.option(
    "--max-iterations",
    type=int,
    default=3,
    help="Maximum feedback iterations.",
)
def main(
    benchmark: str,
    model: str | None,
    output: str,
    data_dir: str | None,
    db_url: str | None,
    limit: int | None,
    use_feedback: bool,
    max_iterations: int,
):
    """Run PULSE benchmark evaluation."""
    click.echo(f"=== PULSE Benchmark Runner ===")
    click.echo(f"Benchmark: {benchmark}")
    click.echo(f"Model: {model or settings.llm_model}")
    click.echo(f"Feedback: {'enabled' if use_feedback else 'disabled'}")
    click.echo(f"Max iterations: {max_iterations}")
    click.echo("")

    # Initialize LLM client
    llm = LLMClient(model=model)

    if benchmark == "spider":
        results = run_spider_benchmark(llm, data_dir, limit, use_feedback, max_iterations)
    elif benchmark == "tpch":
        results = run_tpch_benchmark(
            llm, db_url, limit, use_feedback, max_iterations
        )
    else:
        click.echo(f"Unknown benchmark: {benchmark}", err=True)
        sys.exit(1)

    # Save results
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    click.echo(f"\nResults saved to: {output_path}")
    _print_summary(results)


def run_spider_benchmark(
    llm: LLMClient,
    data_dir: str | None,
    limit: int | None,
    use_feedback: bool,
    max_iterations: int,
) -> dict:
    """Run the Spider benchmark."""
    from benchmarks.spider import SpiderLoader, SpiderEvaluator

    if not data_dir:
        click.echo("ERROR: --data-dir required for Spider benchmark.", err=True)
        sys.exit(1)

    loader = SpiderLoader(data_dir)
    questions = loader.load_questions(split="dev")

    if limit:
        questions = questions[:limit]

    click.echo(f"Loaded {len(questions)} Spider questions.")
    click.echo("")

    predictions = []
    start_time = time.time()

    with click.progressbar(questions, label="Running queries") as bar:
        for q in bar:
            predicted_sql = generate_sql_for_question(
                llm=llm,
                question=q.question,
                db_id=q.db_id,
                schema_info=loader.get_schema_for_db(q.db_id),
                use_feedback=use_feedback,
                max_iterations=max_iterations,
            )
            predictions.append((predicted_sql, q.gold_sql, q.db_id))

    elapsed = time.time() - start_time

    # Evaluate
    db_dir = str(Path(data_dir) / "database")
    evaluator = SpiderEvaluator(db_dir)
    metrics = evaluator.evaluate_batch(predictions)
    metrics["elapsed_seconds"] = elapsed
    metrics["benchmark"] = "spider"
    metrics["model"] = llm.model
    metrics["use_feedback"] = use_feedback
    metrics["max_iterations"] = max_iterations

    return metrics


def run_tpch_benchmark(
    llm: LLMClient,
    db_url: str | None,
    limit: int | None,
    use_feedback: bool,
    max_iterations: int,
) -> dict:
    """Run the TPC-H benchmark."""
    from benchmarks.tpch import TpchLoader, TpchEvaluator

    dsn = db_url or settings.db_url
    loader = TpchLoader()
    queries = loader.load_queries()

    if limit:
        queries = queries[:limit]

    click.echo(f"Loaded {len(queries)} TPC-H queries.")
    click.echo(f"Database: {dsn}")
    click.echo("")

    predictions = []
    start_time = time.time()

    with click.progressbar(queries, label="Running queries") as bar:
        for q in bar:
            predicted_sql = generate_sql_for_question(
                llm=llm,
                question=q.question,
                db_id="tpch",
                schema_info=_get_tpch_schema_info(),
                use_feedback=use_feedback,
                max_iterations=max_iterations,
                db_url=dsn,
            )
            predictions.append((q.query_id, predicted_sql, q.gold_sql))

    elapsed = time.time() - start_time

    # Evaluate
    evaluator = TpchEvaluator(dsn)
    metrics = evaluator.evaluate_batch(predictions)
    metrics["elapsed_seconds"] = elapsed
    metrics["benchmark"] = "tpch"
    metrics["model"] = llm.model
    metrics["use_feedback"] = use_feedback
    metrics["max_iterations"] = max_iterations

    return metrics


def generate_sql_for_question(
    llm: LLMClient,
    question: str,
    db_id: str,
    schema_info: dict | None,
    use_feedback: bool,
    max_iterations: int,
    db_url: str | None = None,
) -> str:
    """
    Generate SQL for a natural language question using the NL2SQL pipeline.

    This is the main entry point that optionally uses plan feedback.
    """
    # Build system prompt with schema information
    schema_text = _format_schema(schema_info) if schema_info else ""
    system_prompt = (
        "You are an expert SQL developer. Generate a SQL query that answers "
        "the user's question. Return ONLY the SQL query, no explanation.\n\n"
        f"Database: {db_id}\n"
        f"{schema_text}"
    )

    # Initial generation
    predicted_sql = llm.generate_sql(system_prompt, question)
    predicted_sql = _clean_sql(predicted_sql)

    if not use_feedback or not db_url:
        return predicted_sql

    # Plan feedback loop
    try:
        from src.analyzer.explain import ExplainParser
        db = Database(dsn=db_url)
        parser = ExplainParser()

        for iteration in range(max_iterations):
            # Get query plan
            try:
                plan_json = db.explain(predicted_sql)
            except Exception:
                break  # If EXPLAIN fails, the SQL is likely invalid

            root = parser.parse([plan_json])
            total_cost = root.total_cost

            # Check if cost is acceptable
            if total_cost < settings.cost_threshold:
                break

            # Generate feedback and ask LLM to improve
            feedback = _generate_plan_feedback(root, parser)
            if not feedback:
                break

            refinement_prompt = (
                f"The following SQL query has performance issues:\n"
                f"```sql\n{predicted_sql}\n```\n\n"
                f"Plan analysis feedback:\n{feedback}\n\n"
                f"Please rewrite the query to address these issues. "
                f"Return ONLY the improved SQL query."
            )
            predicted_sql = llm.generate_sql(system_prompt, refinement_prompt)
            predicted_sql = _clean_sql(predicted_sql)

        db.close()
    except ImportError:
        pass  # If analyzer not available, skip feedback
    except Exception:
        pass  # Don't fail on feedback errors

    return predicted_sql


def _generate_plan_feedback(root, parser) -> str:
    """Generate feedback text from plan analysis."""
    nodes = parser.flatten(root)
    issues = []

    for node in nodes:
        # Detect sequential scan on large table
        if node.node_type == "Seq Scan" and node.plan_rows > settings.large_table_threshold:
            issues.append(
                f"Sequential scan on '{node.relation}' "
                f"(estimated {node.plan_rows:,} rows). "
                f"Consider using an index or adding a more selective predicate."
            )

        # Detect nested loop with high row count
        if node.node_type == "Nested Loop" and node.plan_rows > 10000:
            issues.append(
                f"Nested Loop join with {node.plan_rows:,} estimated rows. "
                f"Consider rewriting as a Hash Join or Merge Join."
            )

        # Detect correlated subplan
        if node.is_subplan:
            issues.append(
                f"Correlated subplan detected ('{node.subplan_name or 'SubPlan'}'). "
                f"Consider rewriting as a JOIN or using a CTE."
            )

        # Detect expensive sort
        if node.is_sort and node.plan_rows > 100000:
            issues.append(
                f"Sort operation on {node.plan_rows:,} rows. "
                f"May cause disk spill. Consider pre-sorting via index."
            )

    return "\n".join(f"- {issue}" for issue in issues)


def _format_schema(schema_info: dict) -> str:
    """Format schema info for the LLM prompt."""
    if not schema_info:
        return ""

    lines = [f"Schema for database '{schema_info.get('db_id', 'unknown')}':"]
    tables = schema_info.get("table_names", [])
    columns = schema_info.get("column_names", [])

    for i, table in enumerate(tables):
        table_cols = [
            col[1] for col in columns if col[0] == i
        ]
        lines.append(f"  {table}({', '.join(table_cols)})")

    return "\n".join(lines)


def _get_tpch_schema_info() -> dict:
    """Return TPC-H schema information for the prompt."""
    return {
        "db_id": "tpch",
        "table_names": [
            "region", "nation", "supplier", "part", "partsupp",
            "customer", "orders", "lineitem",
        ],
        "column_names": [
            [-1, "*"],
            [0, "r_regionkey"], [0, "r_name"], [0, "r_comment"],
            [1, "n_nationkey"], [1, "n_name"], [1, "n_regionkey"], [1, "n_comment"],
            [2, "s_suppkey"], [2, "s_name"], [2, "s_address"],
            [2, "s_nationkey"], [2, "s_phone"], [2, "s_acctbal"], [2, "s_comment"],
            [3, "p_partkey"], [3, "p_name"], [3, "p_mfgr"], [3, "p_brand"],
            [3, "p_type"], [3, "p_size"], [3, "p_container"],
            [3, "p_retailprice"], [3, "p_comment"],
            [4, "ps_partkey"], [4, "ps_suppkey"], [4, "ps_availqty"],
            [4, "ps_supplycost"], [4, "ps_comment"],
            [5, "c_custkey"], [5, "c_name"], [5, "c_address"],
            [5, "c_nationkey"], [5, "c_phone"], [5, "c_acctbal"],
            [5, "c_mktsegment"], [5, "c_comment"],
            [6, "o_orderkey"], [6, "o_custkey"], [6, "o_orderstatus"],
            [6, "o_totalprice"], [6, "o_orderdate"], [6, "o_orderpriority"],
            [6, "o_clerk"], [6, "o_shippriority"], [6, "o_comment"],
            [7, "l_orderkey"], [7, "l_partkey"], [7, "l_suppkey"],
            [7, "l_linenumber"], [7, "l_quantity"], [7, "l_extendedprice"],
            [7, "l_discount"], [7, "l_tax"], [7, "l_returnflag"],
            [7, "l_linestatus"], [7, "l_shipdate"], [7, "l_commitdate"],
            [7, "l_receiptdate"], [7, "l_shipinstruct"], [7, "l_shipmode"],
            [7, "l_comment"],
        ],
    }


def _clean_sql(raw: str) -> str:
    """Clean LLM output to extract just the SQL query."""
    sql = raw.strip()

    # Remove markdown code blocks
    if sql.startswith("```sql"):
        sql = sql[6:]
    elif sql.startswith("```"):
        sql = sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]

    sql = sql.strip()

    # Remove trailing semicolons for consistent comparison
    if sql.endswith(";"):
        sql = sql[:-1].strip()

    return sql


def _print_summary(results: dict) -> None:
    """Print a human-readable summary of results."""
    click.echo("\n=== Results Summary ===")
    click.echo(f"Benchmark: {results.get('benchmark', 'unknown')}")
    click.echo(f"Model: {results.get('model', 'unknown')}")
    click.echo(f"Feedback: {'enabled' if results.get('use_feedback') else 'disabled'}")
    click.echo(f"Elapsed: {results.get('elapsed_seconds', 0):.1f}s")
    click.echo("")
    click.echo(f"Total queries: {results.get('total', 0)}")
    click.echo(f"Correct: {results.get('correct', 0)}")
    click.echo(f"Accuracy: {results.get('accuracy', 0):.2%}")

    if "avg_ves" in results:
        click.echo(f"Avg VES: {results['avg_ves']:.4f}")
    if "avg_time_ratio" in results:
        click.echo(f"Avg Time Ratio: {results['avg_time_ratio']:.2f}x")


if __name__ == "__main__":
    main()
