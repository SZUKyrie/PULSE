# PULSE — Diploma Project

## Project Overview

**Title**: Performance-Aware NL2SQL with Physical Plan Feedback Loop

**Author**: 何衍泓 (Kyrie He), Shenzhen University, High-Performance Computing Class

**Advisor**: TBD (SZU DSEG — Data Science and Engineering Group)

**One-line summary**: A natural language to SQL system that uses physical plan analysis to iteratively refine generated queries, producing both correct AND efficient SQL — especially for OLAP engines with limited query optimization capabilities.

---

## Background & Motivation

### The Problem

Existing NL2SQL systems (DAIL-SQL, DIN-SQL, CHESS, C3) focus solely on **correctness** — whether the SQL returns the right answer. They ignore **performance**. In practice, LLM-generated SQL is often orders of magnitude slower than human-written equivalents because:

1. LLMs don't know table sizes, available indexes, or partition schemes
2. LLMs make bad structural choices (correlated subqueries instead of JOINs, unnecessary DISTINCTs, etc.)
3. OLAP engines (ClickHouse, Doris) have weak optimizers that can't fix structural issues

### Why Integration Matters

The DB optimizer can only perform **local transformations** (predicate pushdown, index selection). It CANNOT globally restructure SQL. The structural choices made at generation time set a performance ceiling. This gap is most severe on:
- OLAP engines with limited rewrite capabilities (ClickHouse, Doris)
- Complex analytical queries (multi-join, aggregation, window functions)
- Large-scale data where bad structure = 100x+ slowdown

### Related Work (and our gap)

| Paper | Focus | Gap vs. ours |
|-------|-------|--------------|
| LLM-R2 (VLDB 2024) | LLM selects rewrite rules for existing SQL | No NL input, no plan feedback loop |
| DIN-SQL (NeurIPS 2023) | Iterative NL2SQL with error correction | Feedback = execution errors, not plan performance |
| CHESS (2024) | Multi-stage NL2SQL pipeline | No plan-awareness in revision |
| Bao (SIGMOD 2021) | Learned query optimizer | ML-based, not LLM; no NL input |
| RetroSlow (PVLDB, under review) | Slow query detection via physical plan profiles | Detection only, no generation or rewriting |

**Our novelty**: No existing work combines NL2SQL generation + physical plan anti-pattern detection + iterative LLM self-refinement based on plan feedback.

---

## System Architecture

```
User (Natural Language Question)
        │
        ▼
┌─────────────────────────────────┐
│  Schema-Aware NL2SQL Module     │
│  (LLM + RAG over DDL/indexes)  │
│  Models: Qwen2.5-7B (local)    │
│          or DeepSeek-V3 (API)  │
└──────────────┬──────────────────┘
               ▼
         Generated SQL
               │
               ▼
┌─────────────────────────────────┐
│  PostgreSQL EXPLAIN (plan only) │
│  FORMAT JSON, COSTS, VERBOSE    │
└──────────────┬──────────────────┘
               ▼
┌─────────────────────────────────┐
│  Plan Analyzer                  │
│  - Anti-pattern detection       │
│  - Cost scoring                 │
│  - Operator-level diagnosis     │
│  (RetroSlow-inspired)           │
└──────────────┬──────────────────┘
               ▼
         Plan acceptable?
          /          \
        Yes           No
         │             │
         ▼             ▼
   Return SQL    ┌─────────────────────┐
                 │  Feedback Formatter  │
                 │  (Plan issues →      │
                 │   structured hints)  │
                 └──────────┬──────────┘
                            │
                            ▼
                   Loop back to LLM
                   (max 3 iterations)
```

---

## Key Components

### 1. Schema-Aware NL2SQL Module
- RAG over table DDLs, indexes, column statistics, partition info
- Schema linking: identify relevant tables from NL question
- Prompt includes available indexes and approximate table sizes

### 2. Plan Analyzer (Core Contribution)
Anti-patterns to detect:

| Pattern | Signal | Severity |
|---------|--------|----------|
| Seq Scan on large table (>10K rows) | Missing index usage | High |
| Nested Loop on high-cardinality join | Should be Hash/Merge Join | High |
| Correlated SubPlan | Could rewrite as JOIN | High |
| Sort with high estimated rows | Potential disk spill | Medium |
| Materialize with many loops | Redundant re-computation | Medium |
| Estimated cost > threshold | General inefficiency | Low |

### 3. Feedback Formatter
Translates plan issues into actionable LLM hints:
```
"Sequential scan on 'lineitem' (6M rows). Index 'idx_lineitem_shipdate' 
exists but unused. Consider adding a predicate on l_shipdate to enable 
index access. Current estimated cost: 45,231."
```

### 4. Iteration Controller
- Max 3 feedback iterations
- Terminates early if: plan cost acceptable, no new issues found, or correctness would be compromised
- Tracks cost improvement per iteration

---

## Tech Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| Database | PostgreSQL 16 | Best EXPLAIN output, TPC-H support |
| Local LLM | Ollama + Qwen2.5-Coder-7B | Free, runs on M3 MacBook |
| API LLM | DeepSeek-V3 via API | Cheap (¥1/M tokens), high quality |
| Framework | Python + LangChain/LangGraph | Agent orchestration |
| Schema RAG | ChromaDB or FAISS | Lightweight vector store |
| Benchmark | Spider + TPC-H (SF1-10) | Correctness + performance evaluation |
| Plan parsing | psycopg2 + JSON format | Native PostgreSQL |

### Hardware
- Development: MacBook Pro M3 (local Ollama for dev, API for experiments)
- Optional: SZU lab GPU server for fine-tuning experiments

---

## Evaluation Plan

### Metrics

| Metric | Measures | Target |
|--------|----------|--------|
| Execution Accuracy (EX) | Correctness (standard NL2SQL metric) | ≥ baseline |
| Valid Efficiency Score (VES) | Cost ratio: generated vs. gold SQL | Show improvement |
| Execution Time Ratio | Wallclock speedup after feedback | 2-10x on complex queries |
| Feedback Convergence | Iterations needed | < 3 average |
| Correctness Preservation | EX doesn't drop after optimization | Critical constraint |

### Baselines
1. Vanilla NL2SQL (same LLM, no plan feedback)
2. One-shot "generate efficient SQL" prompt (plan-aware prompt, no iteration)
3. NL2SQL + DB optimizer only (trust the optimizer)
4. LLM-R2 style rewriting (post-hoc optimization, no NL awareness)

### Key Experiment
"Feedback loop makes a local 7B model match or exceed API-based models (GPT-4/DeepSeek-V3) without feedback on query efficiency, while maintaining correctness."

---

## Timeline (Estimated)

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1. Baseline NL2SQL | 2 weeks | Working NL2SQL on Spider + TPC-H |
| 2. Plan Analyzer | 2 weeks | Anti-pattern detection engine |
| 3. Feedback Loop | 2 weeks | Iterative refinement pipeline |
| 4. Evaluation | 2 weeks | Full benchmark results |
| 5. Advanced features | 2 weeks | Ablations, fine-tuning, OLAP engines |
| 6. Thesis writing | 3 weeks | Final document |

---

## Kyrie's Relevant Experience

- **RetroSlow** (PVLDB under review): Physical plan profiling, six matching tests, operator-level cost analysis — directly applicable to Plan Analyzer
- **Kuaishou internship**: SQL dialect translation (ClickHouse/Hive/Spark → Doris), Calcite parser evaluation, multi-engine SQL compatibility — understands real OLAP pain points
- **Skills**: PostgreSQL internals, C/C++, Python, LangChain, query optimization

---

## Open Questions

1. Which OLAP engine to target for "weak optimizer" experiments? (ClickHouse vs Doris vs both?)
2. Should we also do fine-tuning on a small model, or is prompting + feedback sufficient?
3. Paper target: VLDB demo track? SIGMOD industry? Or Chinese conference (NDBC)?
4. Should the plan analyzer also suggest index creation (not just query rewriting)?

---

## Getting Started

```bash
# Set up local dev environment
brew install postgresql@16 ollama
ollama pull qwen2.5-coder:7b

# Load TPC-H
git clone https://github.com/gregrahn/tpch-kit.git
cd tpch-kit/dbgen && make
./dbgen -s 1  # Scale factor 1 (1GB)

# Python environment
python -m venv .venv && source .venv/bin/activate
pip install psycopg2-binary langchain chromadb
```
