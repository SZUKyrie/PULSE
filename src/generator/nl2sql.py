"""NL2SQLGenerator — generates SQL from natural language questions with schema context.

Uses a schema linker to identify relevant tables/columns, builds a structured prompt
with DDL and index information, and calls an LLM to generate SQL. Supports iterative
refinement by accepting plan feedback as additional context.
"""

from __future__ import annotations

import re

from ..llm_client import LLMClient
from ..schema.linker import SchemaLinker
from ..schema.loader import SchemaContext, SchemaLoader


# System prompt for the NL2SQL generation task.
_SYSTEM_PROMPT = """\
You are an expert SQL developer. Generate a PostgreSQL query that answers the \
user's natural language question.

Rules:
- Use ONLY the tables and columns provided in the schema context.
- Prefer JOINs over subqueries when possible.
- Use appropriate indexes — add WHERE predicates on indexed columns when relevant.
- Consider table sizes when choosing join strategies.
- Return ONLY the SQL query, wrapped in ```sql ... ``` markers.
- Do NOT include any explanation outside the SQL block.
- Ensure the query is correct and efficient.
"""

_REFINEMENT_PROMPT_SUFFIX = """\

## Performance Feedback from Previous Attempt

The previously generated SQL was analyzed and found to have performance issues. \
Please revise the SQL to address the feedback below while preserving correctness \
(the query must return the same results).

{feedback}
"""


class NL2SQLGenerator:
    """Generates SQL from natural language questions using schema-aware prompting.

    Integrates with:
    - SchemaLoader: to fetch the full database schema
    - SchemaLinker: to identify relevant tables/columns for a given question
    - LLMClient: to generate the actual SQL

    Supports iterative refinement by accepting optional plan feedback.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        schema_loader: SchemaLoader,
        schema_linker: SchemaLinker,
    ):
        """Initialize the generator.

        Args:
            llm_client: The LLM client for text generation.
            schema_loader: Loads full schema metadata from the database.
            schema_linker: Identifies relevant schema elements for a question.
        """
        self._llm = llm_client
        self._schema_loader = schema_loader
        self._schema_linker = schema_linker

        # Cache schema context per database to avoid repeated introspection
        self._schema_cache: dict[str, SchemaContext] = {}

    def generate(
        self, question: str, db_name: str, feedback: str | None = None
    ) -> str:
        """Generate SQL for a natural language question.

        Args:
            question: The natural language question to translate to SQL.
            db_name: The database name to query against.
            feedback: Optional plan feedback from a previous iteration.
                When provided, the prompt instructs the LLM to revise
                its SQL to address the performance issues.

        Returns:
            The generated SQL string (cleaned, without markdown markers).
        """
        # Load or retrieve cached schema
        schema = self._get_schema(db_name)

        # Link relevant tables/columns
        linked = self._schema_linker.link(question, schema)

        # Build the prompt
        user_prompt = self._build_user_prompt(question, linked, schema, feedback)

        # Generate SQL via LLM
        system_prompt = _SYSTEM_PROMPT
        if feedback:
            system_prompt += _REFINEMENT_PROMPT_SUFFIX.format(feedback=feedback)

        raw_response = self._llm.generate_sql(system_prompt, user_prompt)

        # Extract and clean the SQL
        return self._extract_sql(raw_response)

    def _get_schema(self, db_name: str) -> SchemaContext:
        """Load schema context, using cache if available."""
        if db_name not in self._schema_cache:
            self._schema_cache[db_name] = self._schema_loader.load(db_name)
        return self._schema_cache[db_name]

    def _build_user_prompt(
        self,
        question: str,
        linked,
        schema: SchemaContext,
        feedback: str | None,
    ) -> str:
        """Build the user prompt with schema context and question.

        Includes:
        - DDL for relevant tables with row count hints
        - Available indexes
        - Foreign key relationships
        - The natural language question
        - Optional feedback from plan analysis
        """
        parts: list[str] = []

        # Schema context section
        parts.append("## Schema Context\n")

        for table in linked.tables:
            # Table header with size hint
            parts.append(
                f"-- {table.name} (~{table.estimated_rows:,} rows)"
            )

            # Column definitions
            cols_str = ", ".join(
                f"{c.name} {c.data_type}" for c in table.columns
            )
            parts.append(f"CREATE TABLE {table.name} ({cols_str});")

            # Indexes for this table
            for idx in table.indexes:
                idx_cols = ", ".join(idx.columns)
                unique_marker = "UNIQUE " if idx.is_unique else ""
                parts.append(
                    f"  -- {unique_marker}INDEX {idx.name} ON {table.name}({idx_cols})"
                )
            parts.append("")

        # Foreign key relationships
        if schema.foreign_keys:
            relevant_tables = {t.name for t in linked.tables}
            relevant_fks = [
                fk
                for fk in schema.foreign_keys
                if fk.source_table in relevant_tables
                or fk.target_table in relevant_tables
            ]
            if relevant_fks:
                parts.append("## Relationships")
                for fk in relevant_fks:
                    src_cols = ", ".join(fk.source_columns)
                    tgt_cols = ", ".join(fk.target_columns)
                    parts.append(
                        f"  {fk.source_table}({src_cols}) -> "
                        f"{fk.target_table}({tgt_cols})"
                    )
                parts.append("")

        # The question
        parts.append(f"## Question\n{question}")

        # Previous feedback for refinement iterations
        if feedback:
            parts.append(f"\n## Previous Plan Feedback\n{feedback}")
            parts.append(
                "\nRevise the SQL to address the issues above. "
                "Maintain correctness."
            )

        return "\n".join(parts)

    def _extract_sql(self, response: str) -> str:
        """Extract SQL from an LLM response, handling various formats.

        Handles:
        - ```sql ... ``` code blocks
        - ``` ... ``` generic code blocks
        - Raw SQL (SELECT/WITH/INSERT/etc.)
        - Multiple SQL blocks (takes the last one, which is usually the final answer)
        """
        # Try to extract from ```sql ... ``` blocks
        sql_blocks = re.findall(
            r"```sql\s*\n?(.*?)```", response, re.DOTALL | re.IGNORECASE
        )
        if sql_blocks:
            return self._clean_sql(sql_blocks[-1])

        # Try generic code blocks
        code_blocks = re.findall(r"```\s*\n?(.*?)```", response, re.DOTALL)
        if code_blocks:
            return self._clean_sql(code_blocks[-1])

        # Try to find raw SQL starting with common keywords
        sql_match = re.search(
            r"((?:SELECT|WITH|INSERT|UPDATE|DELETE)\s+.*)",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if sql_match:
            return self._clean_sql(sql_match.group(1))

        # Last resort: return the whole response cleaned up
        return self._clean_sql(response)

    def _clean_sql(self, sql: str) -> str:
        """Clean extracted SQL — strip whitespace, remove trailing semicolons for EXPLAIN."""
        sql = sql.strip()
        # Remove trailing semicolons (PostgreSQL EXPLAIN doesn't need them)
        sql = sql.rstrip(";")
        return sql
