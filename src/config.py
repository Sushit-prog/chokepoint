import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    eia_api_key: str | None
    gdelt_base_url: str
    gdelt_max_records: int
    ofac_sdn_url: str
    eia_base_url: str
    http_timeout_seconds: float
    retry_delay_seconds: float
    corridor: str
    commodity: str
    anthropic_api_key: str | None
    anthropic_model: str
    eia_baseline_days: int
    eia_vol_scale: float
    llm_provider: str
    llm_api_key: str | None
    llm_model: str


PROVIDER_BASE_URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def load_settings() -> Settings:
    return Settings(
        eia_api_key=os.getenv("EIA_API_KEY") or None,
        gdelt_base_url=os.getenv(
            "GDELT_BASE_URL",
            "https://api.gdeltproject.org/api/v2/doc/doc",
        ),
        gdelt_max_records=_env_int("GDELT_MAX_RECORDS", 75),
        ofac_sdn_url=os.getenv(
            "OFAC_SDN_URL",
            "https://www.treasury.gov/ofac/downloads/sdn.csv",
        ),
        eia_base_url=os.getenv(
            "EIA_BASE_URL",
            "https://api.eia.gov/v2/petroleum/pri/spt/data/",
        ),
        http_timeout_seconds=_env_float("HTTP_TIMEOUT_SECONDS", 30.0),
        retry_delay_seconds=_env_float("RETRY_DELAY_SECONDS", 2.0),
        corridor=os.getenv("INGEST_CORRIDOR", "hormuz"),
        commodity=os.getenv("INGEST_COMMODITY", "crude_oil"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        eia_baseline_days=_env_int("EIA_BASELINE_DAYS", 30),
        eia_vol_scale=_env_float("EIA_VOL_SCALE", 1.0),
        llm_provider=os.getenv("LLM_PROVIDER", "groq").strip().lower(),
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
    )
