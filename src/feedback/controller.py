"""IterationController — manages the feedback loop termination logic.

Decides whether to continue refining a query based on:
- Maximum iteration count
- Cost improvement ratio between iterations
- Whether anti-patterns are repeating (no progress)
- Whether the plan is already acceptable
"""

from __future__ import annotations

from ..analyzer.scorer import PlanReport


class IterationController:
    """Controls termination of the plan feedback loop.

    The controller tracks iteration history and decides when further
    refinement is unlikely to yield meaningful improvement.
    """

    def __init__(
        self,
        max_iterations: int = 3,
        min_improvement: float = 0.1,
        cost_threshold: float = 50_000.0,
    ):
        """Initialize the controller.

        Args:
            max_iterations: Maximum number of feedback iterations allowed.
            min_improvement: Minimum cost improvement ratio (0.1 = 10%) to
                continue iterating. If improvement is below this, stop.
            cost_threshold: Plans below this cost are considered acceptable
                regardless of anti-patterns.
        """
        self.max_iterations = max_iterations
        self.min_improvement = min_improvement
        self.cost_threshold = cost_threshold

    def should_continue(self, history: list[PlanReport]) -> bool:
        """Determine whether to continue the feedback loop.

        Args:
            history: List of PlanReport objects from each iteration so far.
                history[0] is the first (unrefined) plan, history[-1] is the
                most recent.

        Returns:
            True if the loop should continue with another iteration,
            False if it should terminate.
        """
        if not history:
            return True

        # Condition 1: Max iterations reached
        if len(history) >= self.max_iterations:
            return False

        latest = history[-1]

        # Condition 2: Plan is already acceptable
        if latest.is_acceptable:
            return False

        # Condition 3: Cost is below threshold and no high-severity issues
        if (
            latest.total_cost <= self.cost_threshold
            and latest.severity_summary.get("high", 0) == 0
        ):
            return False

        # Need at least 2 reports to check improvement
        if len(history) < 2:
            return True

        previous = history[-2]

        # Condition 4: Insufficient cost improvement
        if previous.total_cost > 0:
            improvement = (
                (previous.total_cost - latest.total_cost) / previous.total_cost
            )
            if improvement < self.min_improvement:
                return False

        # Condition 5: Same anti-patterns repeating (no progress)
        if self._patterns_repeating(history):
            return False

        return True

    def _patterns_repeating(self, history: list[PlanReport]) -> bool:
        """Check if the same anti-patterns appear in the last two iterations.

        If the exact same set of pattern names is present in both the
        current and previous iteration, the LLM is not making progress.
        """
        if len(history) < 2:
            return False

        current_names = {ap.pattern_name for ap in history[-1].anti_patterns}
        previous_names = {ap.pattern_name for ap in history[-2].anti_patterns}

        # If the sets are identical, the feedback loop is stuck
        return current_names == previous_names and len(current_names) > 0

    def get_iteration_summary(self, history: list[PlanReport]) -> str:
        """Generate a human-readable summary of iteration progress.

        Args:
            history: List of PlanReport objects from each iteration.

        Returns:
            A multi-line summary string describing cost progression
            and anti-pattern resolution across iterations.
        """
        if not history:
            return "No iterations completed."

        lines: list[str] = []
        lines.append(f"## Iteration Summary ({len(history)} iteration(s))")
        lines.append("")

        # Cost progression
        costs = [r.total_cost for r in history]
        lines.append("Cost progression:")
        for i, cost in enumerate(costs):
            marker = " (initial)" if i == 0 else ""
            if i > 0:
                prev = costs[i - 1]
                if prev > 0:
                    delta_pct = ((prev - cost) / prev) * 100
                    marker = f" ({delta_pct:+.1f}%)"
                else:
                    marker = ""
            lines.append(f"  Iteration {i + 1}: cost = {cost:,.0f}{marker}")

        # Overall improvement
        if len(costs) >= 2 and costs[0] > 0:
            total_improvement = ((costs[0] - costs[-1]) / costs[0]) * 100
            lines.append(f"\nTotal cost reduction: {total_improvement:.1f}%")

        # Anti-pattern resolution
        initial_patterns = {ap.pattern_name for ap in history[0].anti_patterns}
        final_patterns = {ap.pattern_name for ap in history[-1].anti_patterns}
        resolved = initial_patterns - final_patterns
        remaining = final_patterns

        if resolved:
            resolved_names = ", ".join(sorted(resolved))
            lines.append(f"Resolved issues: {resolved_names}")
        if remaining:
            remaining_names = ", ".join(sorted(remaining))
            lines.append(f"Remaining issues: {remaining_names}")

        # Termination reason
        lines.append(f"\nFinal plan acceptable: {history[-1].is_acceptable}")

        return "\n".join(lines)
