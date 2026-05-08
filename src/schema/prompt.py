"""Prompt templates for schema-aware SQL generation."""

from __future__ import annotations

from .linker import LinkedSchema
from .loader import ForeignKey, SchemaContext


def build_schema_prompt(
    linked_schema: LinkedSchema,
    foreign_keys: list[ForeignKey] | None = None,
) -> str:
    """Format the linked schema as CREATE TABLE DDL with index hints.

    Produces a prompt fragment that gives the LLM:
    - CREATE TABLE statements with column types
    - Index information as comments
    - Approximate table sizes as comments
    - Foreign key relationships
    """
    parts: list[str] = []

    for table in linked_schema.tables:
        # Table header with size annotation
        size_comment = ""
        if table.estimated_rows > 0:
            size_comment = f"  -- ~{table.estimated_rows:,} rows"

        col_defs: list[str] = []
        # Use linked columns if available, otherwise all columns
        columns = linked_schema.columns.get(table.name, table.columns)
        for col in columns:
            null_str = "" if col.nullable else " NOT NULL"
            col_defs.append(f"    {col.name} {col.data_type.upper()}{null_str}")

        create_stmt = (
            f"CREATE TABLE {table.name} ({size_comment}\n"
            + ",\n".join(col_defs)
            + "\n);"
        )
        parts.append(create_stmt)

        # Index hints as comments below the table definition
        table_indexes = [idx for idx in linked_schema.indexes if idx.table_name == table.name]
        if table_indexes:
            idx_lines: list[str] = []
            for idx in table_indexes:
                unique_str = "UNIQUE " if idx.is_unique else ""
                pk_str = " (PRIMARY KEY)" if idx.is_primary else ""
                cols = ", ".join(idx.columns)
                idx_lines.append(
                    f"-- {unique_str}INDEX {idx.name} ON {table.name}({cols}){pk_str}"
                )
            parts.append("\n".join(idx_lines))

    # Foreign key relationships
    if foreign_keys:
        fk_section = _format_foreign_keys(foreign_keys, linked_schema.table_names)
        if fk_section:
            parts.append(fk_section)

    return "\n\n".join(parts)


def _format_foreign_keys(fks: list[ForeignKey], table_names: list[str]) -> str:
    """Format relevant foreign keys as ALTER TABLE statements."""
    relevant_fks: list[str] = []
    table_set = set(table_names)

    for fk in fks:
        # Include FK only if both tables are in the linked schema
        if fk.source_table in table_set and fk.target_table in table_set:
            src_cols = ", ".join(fk.source_columns)
            tgt_cols = ", ".join(fk.target_columns)
            relevant_fks.append(
                f"-- FK: {fk.source_table}({src_cols}) REFERENCES "
                f"{fk.target_table}({tgt_cols})"
            )

    if not relevant_fks:
        return ""

    return "-- Foreign Key Relationships:\n" + "\n".join(relevant_fks)


def build_generation_prompt(
    question: str,
    schema_prompt: str,
    dialect: str = "PostgreSQL",
) -> str:
    """Build the full SQL generation prompt combining question and schema.

    This is the user-facing prompt sent to the LLM for initial SQL generation.
    The system prompt should be set separately.
    """
    return (
        f"Given the following {dialect} database schema:\n\n"
        f"{schema_prompt}\n\n"
        f"Write a SQL query to answer this question:\n"
        f"{question}\n\n"
        f"Requirements:\n"
        f"- Use only the tables and columns defined above.\n"
        f"- Use available indexes where possible (prefer indexed columns in WHERE/JOIN).\n"
        f"- Prefer JOINs over correlated subqueries for better performance.\n"
        f"- For large tables, use indexed predicates to avoid sequential scans.\n"
        f"- Return ONLY the SQL query, no explanation."
    )


# System prompt for the SQL generation LLM.
GENERATION_SYSTEM_PROMPT = """\
You are an expert SQL developer. Generate correct and efficient {dialect} queries.

Guidelines for efficient SQL:
- Use JOINs instead of correlated subqueries when possible.
- Apply predicates on indexed columns to enable index scans.
- Avoid SELECT * — select only needed columns.
- Use appropriate JOIN types (INNER vs LEFT) based on the question semantics.
- For aggregations on large tables, filter early to reduce rows before grouping.
- Avoid unnecessary DISTINCT unless the question implies deduplication.

Output ONLY the SQL query. No markdown, no explanation.
"""


def build_system_prompt(dialect: str = "PostgreSQL") -> str:
    """Return the system prompt for SQL generation."""
    return GENERATION_SYSTEM_PROMPT.format(dialect=dialect)
