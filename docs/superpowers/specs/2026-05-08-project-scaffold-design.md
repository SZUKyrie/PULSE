# PULSE — Project Scaffold Design

## Goal

Scaffold the complete project structure for a performance-aware NL2SQL system with physical plan feedback loop.

## Architecture Decision

**Modular Python package + LangGraph orchestration.** Plain Python modules for each component, LangGraph StateGraph only for the feedback loop (which is naturally a state machine).

Informed by: CHESS (modular phases), DIN-SQL/MAC-SQL (iterative refinement), LLM-R2 (structured hint vocabulary).

## Project Structure

```
src/
├── config.py              # Pydantic settings (DB URL, LLM endpoint, thresholds)
├── llm_client.py          # OpenAI-compatible client, pluggable base_url
├── db.py                  # PostgreSQL connection pool (psycopg2)
├── schema/
│   ├── loader.py          # Load DDLs, indexes, column stats from information_schema
│   ├── linker.py          # Schema linking: NL question → relevant tables/columns
│   └── prompt.py          # Prompt templates for SQL generation
├── generator/
│   └── nl2sql.py          # Core: NL + schema context → SQL
├── analyzer/
│   ├── explain.py         # EXPLAIN FORMAT JSON wrapper + plan tree parser
│   ├── patterns.py        # Anti-pattern dataclasses + detection rules
│   └── scorer.py          # Cost scoring, severity ranking, accept/reject decision
├── feedback/
│   ├── formatter.py       # Plan issues → structured natural language hints
│   └── controller.py      # Iteration control (max 3, early termination conditions)
├── pipeline.py            # LangGraph StateGraph: generate → analyze → feedback → loop
benchmarks/
├── spider/                # Spider dataset loader + execution accuracy evaluator
├── tpch/                  # TPC-H loader + VES/time ratio evaluator
scripts/
├── setup_tpch.sh          # Load TPC-H SF1 into PostgreSQL
├── run_benchmark.py       # CLI entrypoint for experiments
tests/                     # Unit tests per module
```

## Key Interfaces

- `LLMClient.generate(messages) → str` — thin OpenAI-compatible wrapper
- `SchemaLoader.load(db_name) → SchemaContext` — DDLs + indexes + stats
- `SchemaLinker.link(question, schema) → LinkedSchema` — relevant subset
- `NL2SQLGenerator.generate(question, linked_schema) → str` — SQL output
- `PlanAnalyzer.analyze(sql) → PlanReport` — anti-patterns + cost + verdict
- `FeedbackFormatter.format(report) → str` — human-readable hints for LLM
- `IterationController.should_continue(history) → bool` — loop control
- `Pipeline.run(question, db_name) → PipelineResult` — end-to-end

## Anti-Pattern Detection (6 patterns)

1. SeqScan on large table (>10K rows) when index exists
2. NestedLoop on high-cardinality join (should be Hash/Merge)
3. Correlated SubPlan (rewrite as JOIN)
4. Sort with high row estimate (disk spill risk)
5. Materialize with many loops (redundant recomputation)
6. Total cost exceeds threshold

## LLM Integration

Pluggable `base_url` + model name. User's lab server (unknown model) will be configured later via `.env`. Interface is OpenAI chat/completions compatible.

## Evaluation Metrics

- Execution Accuracy (EX) — correctness
- Valid Efficiency Score (VES) — cost ratio vs gold SQL
- Execution Time Ratio — wallclock speedup
- Feedback Convergence — iterations needed
- Correctness Preservation — EX doesn't drop after optimization
