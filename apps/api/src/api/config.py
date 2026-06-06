from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
    research_db_path: Path = Path(".data/research.sqlite3")
    research_artifact_dir: Path = Path(".data/research-runs")
    research_poller_enabled: bool = True
    research_poller_interval_seconds: float = 5.0
    research_deep_research_timeout_seconds: int = 1800

    default_max_deep_research_runs: int = 2
    default_max_llm_fix_runs: int = 3
    default_max_total_iterations: int = 5
    default_max_no_progress_rounds: int = 2
    default_max_cost_usd: float = 20.0
    default_max_total_tool_calls: int = 120

    research_deep_research_input_cost_per_1m: float = 0.0
    research_deep_research_output_cost_per_1m: float = 0.0
    research_reviewer_input_cost_per_1m: float = 0.0
    research_reviewer_output_cost_per_1m: float = 0.0
    research_web_search_cost_per_call: float = 0.0

    o3_deep_research_azure_openai_endpoint: str = ""
    o3_deep_research_azure_openai_key: str = ""
    o3_deep_research_azure_openai_api_version: str = ""
    o3_deep_research_azure_openai_deployment_name: str = "o3-deep-research"

    gpt5_5_azure_openai_endpoint: str = ""
    gpt5_5_azure_openai_key: str = ""
    gpt5_5_azure_openai_api_version: str = ""
    gpt5_5_azure_openai_deployment_name: str = "gpt-5.5"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
