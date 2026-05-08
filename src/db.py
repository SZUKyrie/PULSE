from __future__ import annotations

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from .config import settings


class Database:
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or settings.db_url
        self._pool = pool.SimpleConnectionPool(minconn=1, maxconn=5, dsn=self._dsn)

    def _get_conn(self):
        return self._pool.getconn()

    def _put_conn(self, conn):
        self._pool.putconn(conn)

    def execute(self, sql: str, params: tuple | None = None) -> list[dict]:
        conn = self._get_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                if cur.description:
                    return [dict(row) for row in cur.fetchall()]
                conn.commit()
                return []
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    def explain(self, sql: str) -> dict:
        explain_sql = f"EXPLAIN (FORMAT JSON, COSTS, VERBOSE, ANALYZE false) {sql}"
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(explain_sql)
                result = cur.fetchone()
                return result[0][0] if result else {}
        finally:
            self._put_conn(conn)

    def get_table_sizes(self) -> dict[str, int]:
        sql = """
            SELECT relname AS table_name, reltuples::bigint AS estimated_rows
            FROM pg_class
            WHERE relkind = 'r'
              AND relnamespace = (
                  SELECT oid FROM pg_namespace WHERE nspname = 'public'
              )
            ORDER BY reltuples DESC;
        """
        rows = self.execute(sql)
        return {row["table_name"]: row["estimated_rows"] for row in rows}

    def close(self):
        self._pool.closeall()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
