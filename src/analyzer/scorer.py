"""
PlanScorer — Scores query plans and determines acceptability.

The scorer aggregates anti-pattern findings into a PlanReport that summarizes:
- Whether the plan is acceptable (no HIGH severity issues, cost within threshold)
- A severity breakdown for the feedback formatter
- Overall cost metrics for comparison between iterations

The scoring logic follows RetroSlow's principle of operator-level cost attribution:
each anti-pattern contributes a weighted penalty, and the aggregate determines
whether the plan requires another feedback iteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .explain import PlanNode
from .patterns import AntiPattern, Severity


@dataclass
class PlanReport:
    """
    Complete analysis report for a query plan.

    This is the primary output of the plan analyzer, consumed by the
    feedback formatter to generate LLM-readable optimization hints.
    """

    total_cost: float
    anti_patterns: list[AntiPattern]
    is_acceptable: bool
    severity_summary: dict[str, int]
    cost_threshold: float = 50000.0
    plan_rows: int = 0

    @property
    def has_high_severity(self) -> bool:
        return self.severity_summary.get("high", 0) > 0

    @property
    def has_medium_severity(self) -> bool:
        return self.severity_summary.get("medium", 0) > 0

    @property
    def pattern_count(self) -> int:
        return len(self.anti_patterns)

    def feedback_strings(self) -> list[str]:
        """Generate feedback strings for all anti-patterns, suitable for LLM prompt."""
        return [ap.to_feedback() for ap in self.anti_patterns]

    def summary(self) -> str:
        """Human-readable summary of the plan report."""
        status = "ACCEPTABLE" if self.is_acceptable else "NEEDS OPTIMIZATION"
        lines = [
            f"Plan Report: {status}",
            f"  Total Cost: {self.total_cost:,.1f} (threshold: {self.cost_threshold:,.1f})",
            f"  Anti-patterns: {self.pattern_count} "
            f"(HIGH: {self.severity_summary.get('high', 0)}, "
            f"MEDIUM: {self.severity_summary.get('medium', 0)}, "
            f"LOW: {self.severity_summary.get('low', 0)})",
        ]
        if self.anti_patterns:
            lines.append("  Issues:")
            for ap in self.anti_patterns:
                lines.append(f"    - [{ap.severity.value.upper()}] {ap.pattern_name}: {ap.description[:100]}...")
        return "\n".join(lines)


class PlanScorer:
    """
    Scores query plans based on anti-pattern analysis.

    Acceptability criteria:
    1. No HIGH severity anti-patterns (these indicate structural problems
       that the optimizer cannot fix)
    2. Total plan cost below the configured threshold
    3. At most N medium-severity issues (configurable)

    The scorer is deliberately conservative: it's better to request one
    extra iteration than to accept a plan that will be 10x slower.
    """

    def __init__(
        self,
        cost_threshold: float = 50000.0,
        max_medium_issues: int = 3,
    ):
        """
        Args:
            cost_threshold: Maximum acceptable total plan cost.
            max_medium_issues: Maximum number of MEDIUM issues before
                the plan is considered unacceptable.
        """
        self.cost_threshold = cost_threshold
        self.max_medium_issues = max_medium_issues

    def score(
        self, plan_root: PlanNode, anti_patterns: list[AntiPattern]
    ) -> PlanReport:
        """
        Score a query plan and produce a PlanReport.

        Args:
            plan_root: Root node of the parsed plan tree.
            anti_patterns: List of detected anti-patterns from detect_all().

        Returns:
            PlanReport with acceptability determination and severity breakdown.
        """
        total_cost = plan_root.total_cost

        # Count patterns by severity
        severity_summary: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for ap in anti_patterns:
            severity_summary[ap.severity.value] += 1

        # Determine acceptability
        is_acceptable = self._determine_acceptability(
            total_cost=total_cost,
            severity_summary=severity_summary,
        )

        return PlanReport(
            total_cost=total_cost,
            anti_patterns=anti_patterns,
            is_acceptable=is_acceptable,
            severity_summary=severity_summary,
            cost_threshold=self.cost_threshold,
            plan_rows=plan_root.plan_rows,
        )

    def _determine_acceptability(
        self,
        total_cost: float,
        severity_summary: dict[str, int],
    ) -> bool:
        """
        Determine whether a plan is acceptable.

        A plan is unacceptable if ANY of:
        1. It has HIGH severity anti-patterns (structural issues)
        2. Its total cost exceeds the threshold
        3. It has more than max_medium_issues MEDIUM-severity patterns

        Returns:
            True if the plan is acceptable, False if it needs optimization.
        """
        # Rule 1: Any HIGH severity issue makes the plan unacceptable
        if severity_summary.get("high", 0) > 0:
            return False

        # Rule 2: Cost exceeds threshold
        if total_cost > self.cost_threshold:
            return False

        # Rule 3: Too many medium issues
        if severity_summary.get("medium", 0) > self.max_medium_issues:
            return False

        return True

    def compare(self, before: PlanReport, after: PlanReport) -> float:
        """
        Calculate improvement ratio between two plan reports.

        Returns a float representing the improvement factor:
        - > 1.0 means the plan improved (lower cost)
        - = 1.0 means no change
        - < 1.0 means the plan got worse (higher cost)

        Also considers anti-pattern count reduction as a secondary signal.

        Args:
            before: PlanReport from the previous iteration.
            after: PlanReport from the current iteration.

        Returns:
            Improvement ratio (before_cost / after_cost), clamped to avoid
            division by zero.
        """
        # Avoid division by zero
        if after.total_cost <= 0:
            after_cost = 1.0
        else:
            after_cost = after.total_cost

        if before.total_cost <= 0:
            before_cost = 1.0
        else:
            before_cost = before.total_cost

        cost_improvement = before_cost / after_cost

        # Weight anti-pattern reduction as a bonus factor
        before_issues = before.pattern_count
        after_issues = after.pattern_count
        if before_issues > 0 and after_issues < before_issues:
            # Bonus for reducing anti-patterns (up to 20%)
            reduction_ratio = (before_issues - after_issues) / before_issues
            pattern_bonus = 1.0 + (reduction_ratio * 0.2)
        else:
            pattern_bonus = 1.0

        return cost_improvement * pattern_bonus

    def should_continue_iteration(
        self,
        current_report: PlanReport,
        previous_report: Optional[PlanReport] = None,
        iteration: int = 0,
        max_iterations: int = 3,
    ) -> bool:
        """
        Determine whether another feedback iteration is warranted.

        Stops iteration if:
        1. Plan is acceptable
        2. Max iterations reached
        3. No improvement from last iteration (convergence)
        4. Only LOW severity issues remain

        Args:
            current_report: Report from current iteration.
            previous_report: Report from previous iteration (None for first).
            iteration: Current iteration number (0-indexed).
            max_iterations: Maximum allowed iterations.

        Returns:
            True if another iteration should be attempted.
        """
        # Already acceptable
        if current_report.is_acceptable:
            return False

        # Max iterations reached
        if iteration >= max_iterations:
            return False

        # Only LOW severity issues remain — not worth iterating
        if (
            current_report.severity_summary.get("high", 0) == 0
            and current_report.severity_summary.get("medium", 0) == 0
        ):
            return False

        # Check for convergence (no improvement from last iteration)
        if previous_report is not None:
            improvement = self.compare(previous_report, current_report)
            # Less than 5% improvement — converged
            if improvement < 1.05:
                return False

        return True
