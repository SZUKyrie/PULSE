"""
SpiderLoader — Loads and parses the Spider NL2SQL benchmark dataset.

Expected data directory layout:
  data_dir/
    tables.json       — schema definitions for all databases
    dev.json          — dev split (1034 questions)
    train_spider.json — train split
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SpiderQuestion:
    """A single Spider benchmark question."""

    question: str
    db_id: str
    gold_sql: str
    difficulty: str  # "easy", "medium", "hard", "extra"


class SpiderLoader:
    """Loads Spider dataset from disk."""

    DIFFICULTY_MAP = {
        "easy": "easy",
        "medium": "medium",
        "hard": "hard",
        "extra": "extra",
    }

    def __init__(self, data_dir: str):
        """
        Args:
            data_dir: Path to the Spider dataset root directory.
                      Must contain tables.json and dev.json / train_spider.json.
        """
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Spider data directory not found: {data_dir}")

        self._schemas: dict[str, dict] | None = None

    @property
    def schemas(self) -> dict[str, dict]:
        """Lazily load and cache schema definitions from tables.json."""
        if self._schemas is None:
            self._schemas = self._load_schemas()
        return self._schemas

    def _load_schemas(self) -> dict[str, dict]:
        """Parse tables.json into a dict keyed by db_id."""
        tables_path = self.data_dir / "tables.json"
        if not tables_path.exists():
            raise FileNotFoundError(f"tables.json not found in {self.data_dir}")

        with open(tables_path, "r", encoding="utf-8") as f:
            raw_tables = json.load(f)

        schemas = {}
        for db in raw_tables:
            db_id = db["db_id"]
            schemas[db_id] = {
                "db_id": db_id,
                "table_names": db.get("table_names_original", db.get("table_names", [])),
                "column_names": db.get("column_names_original", db.get("column_names", [])),
                "column_types": db.get("column_types", []),
                "primary_keys": db.get("primary_keys", []),
                "foreign_keys": db.get("foreign_keys", []),
            }
        return schemas

    def load_questions(self, split: str = "dev") -> list[SpiderQuestion]:
        """
        Load questions from the specified split.

        Args:
            split: One of "dev" or "train".

        Returns:
            List of SpiderQuestion instances.

        Raises:
            FileNotFoundError: If the split JSON file does not exist.
            ValueError: If split is not recognized.
        """
        if split == "dev":
            filename = "dev.json"
        elif split == "train":
            filename = "train_spider.json"
        else:
            raise ValueError(f"Unknown split '{split}'. Use 'dev' or 'train'.")

        filepath = self.data_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"{filename} not found in {self.data_dir}")

        with open(filepath, "r", encoding="utf-8") as f:
            raw_questions = json.load(f)

        questions = []
        for item in raw_questions:
            question = SpiderQuestion(
                question=item["question"],
                db_id=item["db_id"],
                gold_sql=item.get("query", item.get("SQL", "")),
                difficulty=self._parse_difficulty(item),
            )
            questions.append(question)

        return questions

    def _parse_difficulty(self, item: dict) -> str:
        """Extract difficulty label from a Spider question entry."""
        # Spider dev.json has a "difficulty" field from the hardness annotation
        raw = item.get("difficulty", item.get("hardness", "unknown"))
        return self.DIFFICULTY_MAP.get(raw.lower(), raw.lower())

    def get_schema_for_db(self, db_id: str) -> dict | None:
        """Return schema information for a specific database."""
        return self.schemas.get(db_id)

    def get_db_ids(self) -> list[str]:
        """Return all available database IDs."""
        return list(self.schemas.keys())
