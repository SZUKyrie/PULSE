# PULSE

**Plan-Understanding Loop for SQL Efficiency**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Tests](https://img.shields.io/badge/Tests-62%20passed-brightgreen)

## Overview

PULSE is a performance-aware NL2SQL system that generates not just *correct* SQL, but *efficient* SQL. It closes the gap between LLM-generated queries and human-optimized equivalents by feeding PostgreSQL physical plan analysis back to the LLM in an iterative refinement loop.

**Core insight:** Database optimizers can only perform local transformations (predicate pushdown, index selection). They cannot fix structural problems introduced at generation time — correlated subqueries, wrong join strategies, missing index utilization. PULSE detects these anti-patterns in the query plan and guides the LLM to fix them.

## Architecture

```
Natural Language Question
        │
        ▼
┌─────────────────────────────────┐
│  Schema-Aware NL2SQL Generator  │
│  (LLM + schema linking + RAG)  │
└──────────────┬──────────────────┘
               ▼
         Generated SQL
               │
               ▼
┌─────────────────────────────────┐
│  PostgreSQL EXPLAIN             │
│  (FORMAT JSON, COSTS, VERBOSE)  │
└──────────────┬──────────────────┘
               ▼
┌─────────────────────────────────┐
│  Plan Analyzer                  │
│  - 6 anti-pattern detectors     │
│  - Cost scoring & severity      │
│  - Accept/reject decision       │
└──────────────┬──────────────────┘
               ▼
         Plan acceptable?
          /          \
        Yes           No
         │             │
         ▼             ▼
   Return SQL    ┌─────────────────────┐
                 │  Feedback Formatter  │
                 │  (Structured hints   │
                 │   for LLM revision)  │
                 └──────────┬──────────┘
                            │
                            ▼
                   Loop back to LLM
                   (max 3 iterations)
```

## Key Features

- **6 Anti-Pattern Detectors** — Sequential scans on large tables, nested loops on high-cardinality joins, correlated subqueries, expensive sorts, redundant materializations, cost threshold violations
- **Iterative Refinement** — LangGraph-based feedback loop with convergence detection and early termination
- **Schema-Aware Generation** — Prompts include DDL, indexes, table sizes, and foreign keys via schema linking
- **Pluggable LLM Backend** — OpenAI-compatible interface; works with vLLM, TGI, Ollama, or any API endpoint
- **Dual Benchmarks** — Spider (correctness) + TPC-H (performance) evaluation

## Quick Start

### Prerequisites

- Python 3.9+
- PostgreSQL 16 (for EXPLAIN analysis)
- An LLM endpoint (OpenAI-compatible API)

### Installation

```bash
git clone https://github.com/SZUKyrie/PULSE.git
cd PULSE
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e ".[dev]"
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your settings:
#   DB_URL=postgresql://user:pass@localhost:5432/tpch
#   LLM_BASE_URL=http://your-lab-server:8000/v1
#   LLM_MODEL=qwen2.5-coder:7b
```

### Load TPC-H Data

```bash
chmod +x scripts/setup_tpch.sh
./scripts/setup_tpch.sh
```

### Run Tests

```bash
pytest tests/ -v
```

### Run Benchmark

```bash
python scripts/run_benchmark.py --benchmark tpch --model qwen2.5-coder:7b --output results.json
```

## Project Structure

```
src/
├── config.py              # Pydantic settings (env-based configuration)
├── llm_client.py          # OpenAI-compatible LLM client
├── db.py                  # PostgreSQL connection + EXPLAIN wrapper
├── schema/
│   ├── loader.py          # Load DDLs, indexes, stats from information_schema
│   ├── linker.py          # Schema linking (keyword + LLM-based)
│   └── prompt.py          # Prompt templates with performance hints
├── generator/
│   └── nl2sql.py          # NL → SQL generation with refinement support
├── analyzer/              # Core contribution
│   ├── explain.py         # EXPLAIN JSON → PlanNode tree parser
│   ├── patterns.py        # Anti-pattern detection (6 detectors)
│   └── scorer.py          # Cost scoring + accept/reject logic
├── feedback/
│   ├── formatter.py       # Plan issues → actionable LLM hints
│   └── controller.py      # Iteration control + convergence detection
└── pipeline.py            # LangGraph StateGraph orchestration
```

## Evaluation Metrics

| Metric | Measures |
|--------|----------|
| Execution Accuracy (EX) | Correctness — does the SQL return the right answer? |
| Valid Efficiency Score (VES) | Cost ratio of generated SQL vs. gold standard |
| Execution Time Ratio | Wallclock speedup after plan-guided refinement |
| Feedback Convergence | Average iterations needed to reach acceptable plan |
| Correctness Preservation | EX does not degrade after optimization |

## Anti-Patterns Detected

| Pattern | Signal | Severity |
|---------|--------|----------|
| Seq Scan on large table (>10K rows) | Missing index usage | High |
| Nested Loop on high-cardinality join | Should be Hash/Merge Join | High |
| Correlated SubPlan | Could rewrite as JOIN | High |
| Sort with high estimated rows | Potential disk spill | Medium |
| Materialize with many loops | Redundant recomputation | Medium |
| Total cost exceeds threshold | General inefficiency | Low |

## Academic Context

This project is a diploma thesis at **Shenzhen University** (深圳大学), Department of Computer Science, High-Performance Computing Class.

**Author:** 何衍泓 (Kyrie He)

**Research Group:** SZU DSEG (Data Science and Engineering Group)

**Related Work:**
- RetroSlow (PVLDB, under review) — Physical plan profiling and slow query detection
- LLM-R2 (VLDB 2024) — LLM-based query rewrite rule selection
- DIN-SQL (NeurIPS 2023) — Iterative NL2SQL with error correction
- CHESS (2024) — Multi-stage NL2SQL pipeline

## License

MIT
