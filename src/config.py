from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_url: str = "postgresql://localhost:5432/postgres"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5-coder:7b"
    llm_temperature: float = 0.0
    max_iterations: int = 3
    cost_threshold: float = 10000.0
    large_table_threshold: int = 10000

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
