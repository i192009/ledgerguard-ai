from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./ledgerguard.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret_key: str = "development-only-change-this-secret-before-deployment-123456"
    jwt_access_token_minutes: int = 30
    jwt_refresh_token_days: int = 7
    demo_mode: bool = True
    task_mode: str = "inline"
    seed_transaction_count: int = 10000
    llm_provider: str = "offline"
    llm_model: str = ""
    openai_api_key: str = ""
    llm_base_url: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    mcp_transactions_url: str = "http://127.0.0.1:8101/mcp"
    mcp_risk_url: str = "http://127.0.0.1:8102/mcp"
    mcp_reconciliation_url: str = "http://127.0.0.1:8103/mcp"
    mcp_approvals_url: str = "http://127.0.0.1:8104/mcp"
    mcp_transactions_token: str = "dev-transactions-secret-change-me"
    mcp_risk_token: str = "dev-risk-secret-change-me"
    mcp_reconciliation_token: str = "dev-reconciliation-secret-change-me"
    mcp_approvals_token: str = "dev-approvals-secret-change-me"
    mcp_context_secret: str = "dev-delegation-key-distinct-from-access-secret-change-me"
    tool_timeout_seconds: int = 180
    tool_budget: int = 40
    approval_hours: int = 24
    two_person_threshold: str = "50000.00"
    settlement_tolerance_days: int = 3
    high_value_threshold: str = "10000.00"
    artifacts_dir: str = "artifacts"
    checkpoint_path: str = "checkpoints.db"
    metrics_token: str = "dev-monitoring-secret-change-me-before-deployment"
    llm_input_cost_per_million: str = "0"
    llm_output_cost_per_million: str = "0"

    def validate_runtime(self) -> None:
        if self.app_env == "production":
            secrets = [
                self.jwt_secret_key,
                self.mcp_context_secret,
                self.metrics_token,
                *[
                    getattr(self, f"mcp_{name}_token")
                    for name in ("transactions", "risk", "reconciliation", "approvals")
                ],
            ]
            if (
                self.demo_mode
                or self.task_mode != "celery"
                or len(set(secrets)) != len(secrets)
                or any(len(s) < 32 or s.startswith(("dev-", "development")) for s in secrets)
            ):
                raise ValueError("Production requires unique secrets, DEMO_MODE=false and TASK_MODE=celery")


@lru_cache
def settings() -> Settings:
    value = Settings()
    value.validate_runtime()
    return value
