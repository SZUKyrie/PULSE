"""LangGraph-based pipeline for the plan-aware NL2SQL feedback loop.

Orchestrates the full cycle:
  question -> generate SQL -> EXPLAIN -> analyze plan ->
  (acceptable? -> return | not acceptable? -> format feedback -> regenerate)

Uses LangGraph's StateGraph for the control flow with conditional edges
based on plan acceptability and iteration limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from .analyzer import PlanAnalyzer, PlanReport
from .config import settings
from .db import Database
from .feedback import FeedbackFormatter, IterationController
from .generator import NL2SQLGenerator
from .llm_client import LLMClient
from .schema import SchemaContext, SchemaLinker, SchemaLoader


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------


class PipelineState(TypedDict):
    """State passed between LangGraph nodes in the feedback loop."""

    question: str
    db_name: str
    sql: str
    plan_report: Optional[PlanReport]
    feedback: Optional[str]
    iteration: int
    history: List[PlanReport]
    schema_context: Optional[SchemaContext]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Final output of the plan-aware NL2SQL pipeline."""

    final_sql: str
    iterations: int
    plan_reports: list[PlanReport] = field(default_factory=list)
    is_optimized: bool = False
    summary: str = ""

    @property
    def final_cost(self) -> float:
        if self.plan_reports:
            return self.plan_reports[-1].total_cost
        return 0.0

    @property
    def initial_cost(self) -> float:
        if self.plan_reports:
            return self.plan_reports[0].total_cost
        return 0.0

    @property
    def cost_reduction_pct(self) -> float:
        if self.initial_cost > 0:
            return ((self.initial_cost - self.final_cost) / self.initial_cost) * 100
        return 0.0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class Pipeline:
    """Plan-aware NL2SQL pipeline using LangGraph for orchestration.

    Implements the iterative feedback loop:
    1. Generate SQL from the natural language question
    2. Get the query plan via EXPLAIN
    3. Analyze the plan for anti-patterns
    4. If acceptable, return. Otherwise, format feedback and loop.
    """

    def __init__(
        self,
        db: Database | None = None,
        llm_client: LLMClient | None = None,
        max_iterations: int | None = None,
        cost_threshold: float | None = None,
    ):
        """Initialize the pipeline with its component dependencies.

        Args:
            db: Database connection. Uses default from settings if None.
            llm_client: LLM client. Creates default from settings if None.
            max_iterations: Override for max feedback iterations.
            cost_threshold: Override for acceptable plan cost threshold.
        """
        self._db = db or Database()
        self._llm = llm_client or LLMClient()

        self._max_iterations = max_iterations or settings.max_iterations
        self._cost_threshold = cost_threshold or settings.cost_threshold

        # Initialize sub-components
        self._schema_loader = SchemaLoader(self._db)
        self._schema_linker = SchemaLinker(self._llm)
        self._generator = NL2SQLGenerator(
            self._llm, self._schema_loader, self._schema_linker
        )
        self._formatter = FeedbackFormatter(cost_threshold=self._cost_threshold)
        self._controller = IterationController(
            max_iterations=self._max_iterations,
            min_improvement=0.1,
            cost_threshold=self._cost_threshold,
        )

        # Build the LangGraph
        self._graph = self._build_graph()

    def run(self, question: str, db_name: str) -> PipelineResult:
        """Execute the full pipeline for a natural language question.

        Args:
            question: The natural language question to translate.
            db_name: The target database name.

        Returns:
            PipelineResult with the final SQL, iteration count, and plan history.
        """
        initial_state: PipelineState = {
            "question": question,
            "db_name": db_name,
            "sql": "",
            "plan_report": None,
            "feedback": None,
            "iteration": 0,
            "history": [],
            "schema_context": None,
        }

        # Execute the graph
        final_state = self._graph.invoke(initial_state)

        # Build result
        history = final_state["history"]
        iterations = final_state["iteration"]
        is_optimized = iterations > 1 and (
            history[-1].total_cost < history[0].total_cost if history else False
        )

        result = PipelineResult(
            final_sql=final_state["sql"],
            iterations=iterations,
            plan_reports=history,
            is_optimized=is_optimized,
            summary=self._controller.get_iteration_summary(history),
        )
        return result

    def _build_graph(self) -> StateGraph:
        """Construct the LangGraph StateGraph with nodes and edges."""
        graph = StateGraph(PipelineState)

        # Add nodes
        graph.add_node("generate_sql", self._node_generate_sql)
        graph.add_node("analyze_plan", self._node_analyze_plan)
        graph.add_node("format_feedback", self._node_format_feedback)

        # Set entry point
        graph.set_entry_point("generate_sql")

        # Add edges
        graph.add_edge("generate_sql", "analyze_plan")

        # Conditional edge from analyze: either end or loop back
        graph.add_conditional_edges(
            "analyze_plan",
            self._check_termination,
            {
                "end": END,
                "continue": "format_feedback",
            },
        )

        # Feedback loops back to generate
        graph.add_edge("format_feedback", "generate_sql")

        return graph.compile()

    # ------------------------------------------------------------------
    # Graph nodes
    # ------------------------------------------------------------------

    def _node_generate_sql(self, state: PipelineState) -> dict:
        """Node: Generate or refine SQL using the LLM."""
        question = state["question"]
        db_name = state["db_name"]
        feedback = state["feedback"]
        iteration = state["iteration"]

        sql = self._generator.generate(
            question=question,
            db_name=db_name,
            feedback=feedback,
        )

        return {
            "sql": sql,
            "iteration": iteration + 1,
            "feedback": None,  # Reset feedback after consuming it
        }

    def _node_analyze_plan(self, state: PipelineState) -> dict:
        """Node: Run EXPLAIN on the SQL and analyze the plan.

        Uses the PlanAnalyzer which handles parsing internally. Constructs
        the analyzer with table sizes and index info from the schema context
        so the pattern detectors have full metadata available.

        If EXPLAIN fails (e.g., invalid table/column name), creates a synthetic
        error report so the pipeline can feed the error back to the LLM.
        """
        sql = state["sql"]
        db_name = state["db_name"]

        # Load schema context (cached after first load)
        schema_context = state["schema_context"]
        if schema_context is None:
            schema_context = self._schema_loader.load(db_name)

        # Build table_sizes and available_indexes from schema context
        table_sizes: dict[str, int] = {}
        available_indexes: dict[str, list[dict]] = {}
        for table in schema_context.tables:
            table_sizes[table.name] = table.estimated_rows
            available_indexes[table.name] = [
                {"name": idx.name, "columns": idx.columns}
                for idx in table.indexes
            ]

        try:
            # Get the query plan from PostgreSQL and analyze it
            explain_json = self._db.explain(sql)

            analyzer = PlanAnalyzer(
                table_sizes=table_sizes,
                available_indexes=available_indexes,
                cost_threshold=self._cost_threshold,
            )
            report = analyzer.analyze(explain_json)
        except Exception as e:
            # SQL failed to execute — create an error report that the
            # feedback formatter can turn into actionable LLM hints
            from .analyzer.patterns import AntiPattern, Severity
            from .analyzer.explain import PlanNode

            error_pattern = AntiPattern(
                pattern_name="sql_error",
                severity=Severity.HIGH,
                node=PlanNode(node_type="Error"),
                description=f"SQL failed with error: {e}",
                suggestion=(
                    f"Fix the SQL error. Available tables: "
                    f"{', '.join(schema_context.table_names)}. "
                    f"Use exact table names from the schema."
                ),
            )
            report = PlanReport(
                total_cost=float("inf"),
                anti_patterns=[error_pattern],
                is_acceptable=False,
                severity_summary={"high": 1},
            )

        # Append to history
        history = list(state["history"])
        history.append(report)

        return {
            "plan_report": report,
            "history": history,
            "schema_context": schema_context,
        }

    def _node_format_feedback(self, state: PipelineState) -> dict:
        """Node: Format plan issues into actionable feedback for the LLM."""
        report = state["plan_report"]
        schema_context = state["schema_context"]

        if report is None or schema_context is None:
            return {"feedback": None}

        feedback = self._formatter.format(report, schema_context)
        return {"feedback": feedback}

    # ------------------------------------------------------------------
    # Conditional edge
    # ------------------------------------------------------------------

    def _check_termination(self, state: PipelineState) -> str:
        """Conditional edge: decide whether to continue or end the loop."""
        history = state["history"]

        if self._controller.should_continue(history):
            return "continue"
        return "end"
