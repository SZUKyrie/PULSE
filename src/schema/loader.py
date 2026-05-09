"""Schema loader: introspects PostgreSQL metadata via information_schema and pg_catalog."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..db import Database


class ColumnInfo(BaseModel):
    """Column metadata."""

    name: str
    data_type: str
    nullable: bool = True
    ordinal_position: int = 0


class IndexInfo(BaseModel):
    """Index metadata."""

    name: str
    table_name: str
    columns: list[str]
    is_unique: bool = False
    is_primary: bool = False


class ForeignKey(BaseModel):
    """Foreign key relationship."""

    constraint_name: str
    source_table: str
    source_columns: list[str]
    target_table: str
    target_columns: list[str]


class TableInfo(BaseModel):
    """Full table metadata."""

    name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    estimated_rows: int = 0


class SchemaContext(BaseModel):
    """Complete database schema context for NL2SQL generation."""

    db_name: str
    tables: list[TableInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)

    def get_table(self, name: str) -> TableInfo | None:
        """Look up a table by name (case-insensitive)."""
        lower = name.lower()
        for t in self.tables:
            if t.name.lower() == lower:
                return t
        return None

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]


class SchemaLoader:
    """Loads schema metadata from a PostgreSQL database."""

    def __init__(self, db: Database):
        self._db = db

    def load(self, db_name: str, schema_name: str = "public") -> SchemaContext:
        """Load full schema context for the given database.

        Queries information_schema for tables, columns, indexes, foreign keys,
        and pg_class for approximate row counts.
        """
        tables = self._load_tables(schema_name)
        columns_map = self._load_columns(schema_name)
        indexes_map = self._load_indexes(schema_name)
        row_counts = self._load_row_counts(schema_name)
        foreign_keys = self._load_foreign_keys(schema_name)

        table_infos: list[TableInfo] = []
        for table_name in tables:
            table_infos.append(
                TableInfo(
                    name=table_name,
                    columns=columns_map.get(table_name, []),
                    indexes=indexes_map.get(table_name, []),
                    estimated_rows=row_counts.get(table_name, 0),
                )
            )

        return SchemaContext(
            db_name=db_name,
            tables=table_infos,
            foreign_keys=foreign_keys,
        )

    def _load_tables(self, schema_name: str) -> list[str]:
        """Get all table names in the schema."""
        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """
        rows = self._db.execute(sql, (schema_name,))
        return [row["table_name"] for row in rows]

    def _load_columns(self, schema_name: str) -> dict[str, list[ColumnInfo]]:
        """Load all columns grouped by table."""
        sql = """
            SELECT table_name, column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position;
        """
        rows = self._db.execute(sql, (schema_name,))
        columns_map: dict[str, list[ColumnInfo]] = {}
        for row in rows:
            col = ColumnInfo(
                name=row["column_name"],
                data_type=row["data_type"],
                nullable=row["is_nullable"] == "YES",
                ordinal_position=row["ordinal_position"],
            )
            columns_map.setdefault(row["table_name"], []).append(col)
        return columns_map

    def _load_indexes(self, schema_name: str) -> dict[str, list[IndexInfo]]:
        """Load all indexes grouped by table using pg_catalog."""
        sql = """
            SELECT
                i.relname AS index_name,
                t.relname AS table_name,
                ix.indisunique AS is_unique,
                ix.indisprimary AS is_primary,
                array_agg(a.attname ORDER BY k.n) AS columns
            FROM pg_catalog.pg_index ix
            JOIN pg_catalog.pg_class t ON t.oid = ix.indrelid
            JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid
            JOIN pg_catalog.pg_namespace ns ON ns.oid = t.relnamespace
            CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, n)
            JOIN pg_catalog.pg_attribute a
                ON a.attrelid = t.oid AND a.attnum = k.attnum
            WHERE ns.nspname = %s
            GROUP BY i.relname, t.relname, ix.indisunique, ix.indisprimary
            ORDER BY t.relname, i.relname;
        """
        rows = self._db.execute(sql, (schema_name,))
        indexes_map: dict[str, list[IndexInfo]] = {}
        for row in rows:
            idx = IndexInfo(
                name=row["index_name"],
                table_name=row["table_name"],
                columns=row["columns"],
                is_unique=row["is_unique"],
                is_primary=row["is_primary"],
            )
            indexes_map.setdefault(row["table_name"], []).append(idx)
        return indexes_map

    def _load_row_counts(self, schema_name: str) -> dict[str, int]:
        """Approximate row counts from pg_class.reltuples."""
        sql = """
            SELECT c.relname AS table_name,
                   c.reltuples::bigint AS estimated_rows
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace ns ON ns.oid = c.relnamespace
            WHERE ns.nspname = %s
              AND c.relkind = 'r'
            ORDER BY c.reltuples DESC;
        """
        rows = self._db.execute(sql, (schema_name,))
        return {row["table_name"]: max(0, row["estimated_rows"]) for row in rows}

    def _load_foreign_keys(self, schema_name: str) -> list[ForeignKey]:
        """Load all foreign key constraints."""
        sql = """
            SELECT
                tc.constraint_name,
                tc.table_name AS source_table,
                array_agg(DISTINCT kcu.column_name) AS source_columns,
                ccu.table_name AS target_table,
                array_agg(DISTINCT ccu.column_name) AS target_columns
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = %s
            GROUP BY tc.constraint_name, tc.table_name, ccu.table_name
            ORDER BY tc.table_name;
        """
        rows = self._db.execute(sql, (schema_name,))
        return [
            ForeignKey(
                constraint_name=row["constraint_name"],
                source_table=row["source_table"],
                source_columns=_ensure_list(row["source_columns"]),
                target_table=row["target_table"],
                target_columns=_ensure_list(row["target_columns"]),
            )
            for row in rows
        ]


def _ensure_list(val) -> list[str]:
    """Convert PostgreSQL array_agg output to a Python list.

    psycopg2 may return a list or a string like '{a,b}' depending on cursor type.
    """
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [v.strip() for v in val.strip("{}").split(",") if v.strip()]
    return []
