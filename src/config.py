from pathlib import Path

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    db_url: str = "postgresql://localhost:5432/tpch"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5-coder:7b"
    llm_temperature: float = 0.0
    max_iterations: int = 3
    cost_threshold: float = 10000.0
    large_table_threshold: int = 10000

    model_config = {
        "env_file": str(_ENV_FILE),
        "env_file_encoding": "utf-8",
    }


settings = Settings()
