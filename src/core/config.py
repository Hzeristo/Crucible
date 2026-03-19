"""Central project settings loaded from config.yaml and .env.

The Crucible (熔炉) - Project Chimera 通用计算与认知地基.
Core settings are generic; domain-specific configs (e.g. PaperMiner) live in optional sub-blocks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, get_args, get_origin

from pydantic import BaseModel, Field, model_validator, SecretStr
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _repo_root() -> Path:
    """Resolve repository root from this module location."""
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _repo_root()


def _is_path_like_key(key: str) -> bool:
    """Heuristic for identifying path-like setting keys."""
    normalized = key.lower()
    return normalized.endswith(("_path", "_dir", "_root", "_file", "_folder")) or normalized in {
        "path",
        "dir",
        "root",
        "file",
        "vault",
    }


def _is_windows_drive_relative(path_value: Path) -> bool:
    """Detect Windows drive-relative paths such as 'C:folder/file'."""
    return bool(path_value.drive) and not path_value.is_absolute()


def _normalize_config_path(value: str | Path, project_root: Path) -> Path:
    """Normalize a config path without relying on process CWD."""
    raw_str = str(value)
    expanded = Path(raw_str).expanduser()

    if _is_windows_drive_relative(expanded):
        raise ValueError(
            f"Drive-relative path is not allowed (depends on per-drive CWD): {raw_str}. "
            f"Use an absolute path like 'C:/...' or a repo-relative path."
        )

    if expanded.is_absolute() or raw_str.startswith("~"):
        return expanded.resolve()
    return (project_root / expanded).resolve()


def _convert_path_like_values(
    value: Any,
    key_hint: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Any:
    """Recursively coerce path-like string settings to pathlib.Path."""
    if isinstance(value, Mapping):
        return {
            k: _convert_path_like_values(v, k, project_root) for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _convert_path_like_values(item, key_hint, project_root) for item in value
        ]
    if key_hint and _is_path_like_key(key_hint) and isinstance(value, (str, Path)):
        return _normalize_config_path(value, project_root)
    return value


def _is_path_annotation(annotation: Any) -> bool:
    """Return True when a field annotation is Path or optional Path."""
    if annotation is Path:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return Path in get_args(annotation)


# ---------------------------------------------------------------------------
# [PaperMiner Settings] - Optional sub-block for paper mining domain.
# When absent, new engines (e.g. Oligo) can initialize without crawler config.
# ---------------------------------------------------------------------------

_PAPER_MINER_KEYS = frozenset({
    "arxivpdf_dir", "md_papers_raw_dir", "md_papers_dir",
    "filtered_dir", "papers_root", "arxiv_query", "arxiv_max_results",
})


class PaperMinerSettings(BaseModel):
    """Optional paper-specific paths and arxiv query config."""

    arxivpdf_dir: Path | None = None
    md_papers_raw_dir: Path | None = None
    md_papers_dir: Path | None = None
    filtered_dir: Path | None = None
    papers_root: Path | None = None
    arxiv_query: str = "cat:cs.AI AND (all:memory OR all:agent OR all:RAG)"
    arxiv_max_results: int = 50


class Settings(BaseSettings):
    """Pydantic settings model with YAML + dotenv support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="allow",
        yaml_file=PROJECT_ROOT / "config.yaml",
        yaml_file_encoding="utf-8",
    )

    # --- Generic Crucible ---
    project_root: Path = Field(default_factory=_repo_root)
    config_file: Path = Field(default_factory=lambda: _repo_root() / "config.yaml")
    vault_root: Path | None = None
    inbox_folder: Path | None = None
    log_level: str = "INFO"

    # --- LLM / API keys ---
    deepseek_api_key: SecretStr | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    tg_bot_token: SecretStr | None = Field(default=None, alias="TG_BOT_TOKEN")
    tg_chat_id: SecretStr | None = Field(default=None, alias="TG_CHAT_ID")

    # --- [PaperMiner Settings] Optional sub-block ---
    paper_miner: PaperMinerSettings | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @model_validator(mode="before")
    @classmethod
    def _merge_paper_miner_flat_keys(cls, data: Any) -> Any:
        """Backward compat: merge flat paper_miner keys into paper_miner sub-block."""
        if not isinstance(data, Mapping):
            return data
        d = dict(data)
        pm = d.get("paper_miner")
        flat_present = any(k in d for k in _PAPER_MINER_KEYS)
        if flat_present and not isinstance(pm, Mapping):
            pm_data = {k: d.pop(k) for k in _PAPER_MINER_KEYS if k in d}
            if pm_data:
                d["paper_miner"] = pm_data
        return d

    @model_validator(mode="before")
    @classmethod
    def _coerce_path_like_values(cls, data: Any) -> Any:
        """Convert all path-like string keys to Path before validation."""
        if not isinstance(data, Mapping):
            return data
        return _convert_path_like_values(dict(data), project_root=PROJECT_ROOT)

    @model_validator(mode="after")
    def _normalize_typed_path_fields(self) -> "Settings":
        """Normalize all Path-typed fields against PROJECT_ROOT."""
        for field_name, field_info in type(self).model_fields.items():
            if not _is_path_annotation(field_info.annotation):
                continue
            value = getattr(self, field_name)
            if isinstance(value, (Path, str)):
                setattr(self, field_name, _normalize_config_path(value, PROJECT_ROOT))
        return self

    def require_path(self, field_name: str) -> Path:
        """Return a typed Path field or raise if unset/non-path."""
        value = getattr(self, field_name, None)
        if value is None:
            raise ValueError(f"Required path setting is missing: {field_name}")
        if not isinstance(value, Path):
            raise TypeError(
                f"Setting '{field_name}' must be pathlib.Path, got: {type(value).__name__}"
            )
        return value

    @property
    def paper_miner_or_default(self) -> PaperMinerSettings:
        """Return PaperMiner settings; defaults when not configured (e.g. for new engines)."""
        return self.paper_miner or PaperMinerSettings()


def load_config() -> Settings:
    """Create Settings from config.yaml, .env and process environment."""
    return Settings()
