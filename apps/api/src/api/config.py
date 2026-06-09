from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    research_api_key: str = Field(default="", repr=False)
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ]
    research_db_path: Path = Path(".data/research.sqlite3")
    research_artifact_dir: Path = Path(".data/research-runs")
    forecast_enabled: bool = True
    forecast_artifact_dir: Path = Path(".data/forecast-runs")
    forecast_max_concurrent_packs: int = Field(default=1, ge=1)
    research_poller_enabled: bool = True
    research_poller_interval_seconds: float = Field(default=5.0, gt=0)
    research_deep_research_timeout_seconds: int = Field(default=7200, gt=0)
    research_deep_research_submit_timeout_seconds: int = Field(default=120, gt=0)
    research_deep_research_submit_stale_seconds: int = Field(default=300, gt=0)
    research_deep_research_collecting_stale_seconds: int = Field(default=60, gt=0)
    research_review_timeout_seconds: int = Field(default=180, gt=0)
    research_review_max_report_chars: int = Field(default=50000, ge=1)
    research_review_max_citations: int = Field(default=40, ge=0)
    research_review_web_search_enabled: bool = False
    research_private_vector_store_allowlist: list[str] = Field(default_factory=list)
    research_manual_import_max_report_chars: int = Field(default=50000, ge=1)
    research_manual_import_max_file_bytes: int = Field(default=1048576, ge=1)

    default_max_targeted_rerun_runs: int = Field(default=2, ge=0)
    default_max_full_rerun_runs: int = Field(default=1, ge=0)
    default_max_llm_patch_runs: int = Field(default=3, ge=0)
    default_max_verification_runs: int = Field(default=3, ge=0)
    default_max_total_iterations: int = Field(default=5, ge=1)
    default_max_total_tool_calls: int = Field(default=120, ge=1)

    research_deep_research_input_cost_per_1m: float = Field(default=0.0, ge=0)
    research_deep_research_output_cost_per_1m: float = Field(default=0.0, ge=0)
    research_reviewer_input_cost_per_1m: float = Field(default=0.0, ge=0)
    research_reviewer_output_cost_per_1m: float = Field(default=0.0, ge=0)
    research_web_search_cost_per_call: float = Field(default=0.01, ge=0)

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

    @model_validator(mode="after")
    def validate_submit_stale_after_timeout(self) -> Self:
        if (
            self.research_deep_research_submit_stale_seconds
            <= self.research_deep_research_submit_timeout_seconds
        ):
            raise ValueError(
                "research_deep_research_submit_stale_seconds must be greater than "
                "research_deep_research_submit_timeout_seconds"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
