from dataclasses import dataclass, field, replace
from functools import lru_cache
import os
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_path: Path = field(default_factory=lambda: Path(os.getenv("DATABASE_PATH", str(ROOT / "data" / "paperpilot.db"))).resolve())
    mode: str = field(default_factory=lambda: os.getenv("APP_MODE", "live"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""), repr=False)
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "90")))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "4096")))
    cache_ttl: int = field(default_factory=lambda: int(os.getenv("ARXIV_CACHE_TTL", "3600")))
    arxiv_min_interval: float = field(default_factory=lambda: max(3.0, float(os.getenv("ARXIV_MIN_INTERVAL", "3"))))
    arxiv_timeout: float = field(default_factory=lambda: float(os.getenv("ARXIV_TIMEOUT", "30")))
    fulltext_timeout: float = field(default_factory=lambda: min(300.0, max(10.0, float(os.getenv("FULLTEXT_TIMEOUT", "120")))))


@lru_cache
def _environment_settings() -> Settings:
    return Settings()


def model_config_path() -> Path:
    return _environment_settings().database_path.parent / "model-config.json"


def stored_model_config() -> dict | None:
    path = model_config_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ("base_url", "model", "api_key", "revision")):
            raise ValueError("invalid configuration")
        return value
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        raise RuntimeError("后端模型配置文件无法读取，请检查 data/model-config.json。") from None


def get_settings() -> Settings:
    # Read the atomic override on every call so the separate worker sees saved changes.
    settings = _environment_settings()
    saved = stored_model_config()
    return replace(settings, llm_base_url=saved["base_url"], llm_model=saved["model"],
                   llm_api_key=saved["api_key"]) if saved else settings


# Preserve the existing test/environment cache reset contract.
get_settings.cache_clear = _environment_settings.cache_clear
