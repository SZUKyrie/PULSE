"""Schema linker: identifies relevant tables/columns from a natural language question.

Inspired by CHESS's schema linking approach but simplified:
1. Keyword matching as fast first pass
2. LLM-based linking as fallback for ambiguous cases
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from ..llm_client import LLMClient
from .loader import ColumnInfo, IndexInfo, SchemaContext, TableInfo


class LinkedSchema(BaseModel):
    """The subset of schema relevant to a user question."""

    tables: list[TableInfo] = Field(default_factory=list)
    columns: dict[str, list[ColumnInfo]] = Field(
        default_factory=dict,
        description="Relevant columns keyed by table name",
    )
    indexes: list[IndexInfo] = Field(default_factory=list)
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Linking confidence score"
    )

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]


# Minimum keyword match ratio to consider a table relevant without LLM fallback.
_KEYWORD_CONFIDENCE_THRESHOLD = 0.6

# Schema linking prompt template for LLM fallback.
_LINK_SYSTEM_PROMPT = """\
You are a database schema linker. Given a natural language question and a database schema, \
identify which tables and columns are needed to answer the question.

Respond ONLY with a JSON object in this exact format:
{
  "tables": ["table_name_1", "table_name_2"],
  "columns": {
    "table_name_1": ["col_a", "col_b"],
    "table_name_2": ["col_x"]
  },
  "confidence": 0.85
}

Rules:
- Include all tables needed for JOINs even if not directly mentioned.
- Include foreign key columns needed for joins.
- confidence is your self-assessed probability that the selection is correct (0.0 to 1.0).
- Do NOT include tables or columns unrelated to the question.
"""


class SchemaLinker:
    """Links NL questions to relevant schema elements.

    Uses a two-phase approach:
    1. Fast keyword matching against table/column names
    2. LLM-based linking as fallback when keyword matching is insufficient
    """

    def __init__(self, llm: LLMClient | None = None):
        self._llm = llm

    def link(self, question: str, schema: SchemaContext) -> LinkedSchema:
        """Identify relevant schema elements for the given question.

        First attempts keyword matching. If confidence is low and an LLM
        client is available, falls back to LLM-based linking.
        """
        linked = self._keyword_match(question, schema)

        if linked.confidence < _KEYWORD_CONFIDENCE_THRESHOLD and self._llm is not None:
            llm_linked = self._llm_link(question, schema)
            if llm_linked.confidence > linked.confidence:
                linked = llm_linked

        # Always include indexes for linked tables
        linked.indexes = self._collect_indexes(linked.tables)

        return linked

    def _keyword_match(self, question: str, schema: SchemaContext) -> LinkedSchema:
        """Match table and column names against question tokens.

        Uses normalized token overlap to identify candidate tables.
        Inspired by CHESS's value-based schema linking but simplified
        to pure string matching.
        """
        # Normalize the question: lowercase, split on non-alphanumeric
        q_tokens = set(re.findall(r"[a-z][a-z0-9]*", question.lower()))

        matched_tables: list[TableInfo] = []
        matched_columns: dict[str, list[ColumnInfo]] = {}
        total_score = 0.0

        for table in schema.tables:
            # Tokenize table name (split on underscore)
            table_tokens = set(table.name.lower().replace("_", " ").split())

            # Direct table name match
            table_score = len(table_tokens & q_tokens) / max(len(table_tokens), 1)

            # Check column-level matches
            col_matches: list[ColumnInfo] = []
            for col in table.columns:
                col_tokens = set(col.name.lower().replace("_", " ").split())
                if col_tokens & q_tokens:
                    col_matches.append(col)

            # Table is relevant if its name matches or multiple columns match
            if table_score > 0.3 or len(col_matches) >= 2:
                matched_tables.append(table)
                matched_columns[table.name] = col_matches if col_matches else table.columns
                total_score += max(table_score, len(col_matches) / max(len(table.columns), 1))

        # Confidence is average relevance across matched tables
        n_matched = len(matched_tables)
        confidence = (total_score / n_matched) if n_matched > 0 else 0.0
        confidence = min(confidence, 1.0)

        # Also add tables reachable via foreign keys from matched tables
        matched_tables = self._add_join_tables(matched_tables, matched_columns, schema)

        return LinkedSchema(
            tables=matched_tables,
            columns=matched_columns,
            confidence=confidence,
        )

    def _add_join_tables(
        self,
        tables: list[TableInfo],
        columns: dict[str, list[ColumnInfo]],
        schema: SchemaContext,
    ) -> list[TableInfo]:
        """Add tables reachable via foreign keys from already-matched tables."""
        matched_names = {t.name for t in tables}
        extra_tables: list[TableInfo] = []

        for fk in schema.foreign_keys:
            if fk.source_table in matched_names and fk.target_table not in matched_names:
                target = schema.get_table(fk.target_table)
                if target:
                    extra_tables.append(target)
                    columns[target.name] = target.columns
                    matched_names.add(target.name)
            elif fk.target_table in matched_names and fk.source_table not in matched_names:
                source = schema.get_table(fk.source_table)
                if source:
                    extra_tables.append(source)
                    columns[source.name] = source.columns
                    matched_names.add(source.name)

        return tables + extra_tables

    def _llm_link(self, question: str, schema: SchemaContext) -> LinkedSchema:
        """Use the LLM to identify relevant schema elements.

        Provides the full schema description and asks the model to select
        the relevant subset.
        """
        if self._llm is None:
            return LinkedSchema()

        # Build a concise schema description for the LLM
        schema_desc = self._format_schema_for_llm(schema)
        user_prompt = (
            f"Database schema:\n{schema_desc}\n\n"
            f"Question: {question}\n\n"
            "Identify the relevant tables and columns."
        )

        messages = [
            {"role": "system", "content": _LINK_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        raw_response = self._llm.generate(messages)

        return self._parse_llm_response(raw_response, schema)

    def _format_schema_for_llm(self, schema: SchemaContext) -> str:
        """Format schema as a compact description for the LLM."""
        lines: list[str] = []
        for table in schema.tables:
            cols = ", ".join(
                f"{c.name} ({c.data_type})" for c in table.columns
            )
            size_hint = f" [~{table.estimated_rows:,} rows]" if table.estimated_rows > 0 else ""
            lines.append(f"- {table.name}{size_hint}: {cols}")

        if schema.foreign_keys:
            lines.append("\nForeign keys:")
            for fk in schema.foreign_keys:
                src = f"{fk.source_table}({', '.join(fk.source_columns)})"
                tgt = f"{fk.target_table}({', '.join(fk.target_columns)})"
                lines.append(f"  {src} -> {tgt}")

        return "\n".join(lines)

    def _parse_llm_response(self, response: str, schema: SchemaContext) -> LinkedSchema:
        """Parse the LLM JSON response into a LinkedSchema."""
        # Extract JSON from the response (handle markdown code blocks)
        json_match = re.search(r"\{[\s\S]*\}", response)
        if not json_match:
            return LinkedSchema()

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return LinkedSchema()

        table_names: list[str] = data.get("tables", [])
        column_map: dict[str, list[str]] = data.get("columns", {})
        confidence: float = float(data.get("confidence", 0.5))

        # Resolve table names to TableInfo objects
        tables: list[TableInfo] = []
        columns: dict[str, list[ColumnInfo]] = {}

        for tname in table_names:
            table = schema.get_table(tname)
            if table is None:
                continue
            tables.append(table)

            # Resolve column names
            if tname in column_map:
                resolved_cols = [
                    c for c in table.columns if c.name in column_map[tname]
                ]
                columns[tname] = resolved_cols if resolved_cols else table.columns
            else:
                columns[tname] = table.columns

        return LinkedSchema(
            tables=tables,
            columns=columns,
            confidence=confidence,
        )

    def _collect_indexes(self, tables: list[TableInfo]) -> list[IndexInfo]:
        """Gather all indexes from the linked tables."""
        indexes: list[IndexInfo] = []
        for table in tables:
            indexes.extend(table.indexes)
        return indexes
