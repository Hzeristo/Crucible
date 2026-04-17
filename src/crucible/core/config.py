"""Central project settings loaded from config.yaml and .env.

The Crucible (熔炉) - Project Chimera 通用计算与认知地基.
Core settings are generic; domain-specific configs (e.g. PaperMiner) live in optional sub-blocks.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping, get_args, get_origin

from dotenv import dotenv_values
from pydantic import AliasChoices, BaseModel, Field, model_validator, SecretStr

from src.crucible.core.schemas import OligoAgentConfig
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _repo_root() -> Path:
    """Resolve repository root from this module location (src/crucible/core/config.py)."""
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _repo_root()

logger = logging.getLogger(__name__)


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
    "filtered_dir", "failed_dir", "papers_root", "arxiv_query", "arxiv_max_results",
})

# LLM API keys must never be honored from config.yaml (only env / .env).
_LLM_SECRET_KEY_NAMES_LOWER: frozenset[str] = frozenset({
    "openai_api_key",
    "deepseek_api_key",
    "anthropic_api_key",
    "gemini_api_key",
    "wash_model_api_key",
})

_CANONICAL_LLM_SECRET_ENV_NAMES: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "WASH_MODEL_API_KEY",
)


class CrucibleYamlSettingsSource(YamlConfigSettingsSource):
    """YAML loader that drops LLM secret keys so config.yaml cannot inject them."""

    def __call__(self) -> dict[str, Any]:
        raw = super().__call__()
        return {
            k: v
            for k, v in raw.items()
            if k.lower() not in _LLM_SECRET_KEY_NAMES_LOWER
        }


def _pop_llm_secret_keys_case_insensitive(data: dict[str, Any]) -> None:
    """Remove LLM secret keys from a flat mapping (any casing)."""
    for key in list(data.keys()):
        if key.lower() in _LLM_SECRET_KEY_NAMES_LOWER:
            data.pop(key, None)


def _restore_llm_secrets_from_os_and_dotenv(data: dict[str, Any]) -> None:
    """
    Re-apply LLM secrets from OS env (highest) then project .env file.
    Used after stripping merged input so YAML/file noise cannot win over 12-factor sources.
    """
    env_path = PROJECT_ROOT / ".env"
    file_vals = dotenv_values(env_path) if env_path.is_file() else {}
    for name in _CANONICAL_LLM_SECRET_ENV_NAMES:
        if name in os.environ and str(os.environ[name]).strip() != "":
            data[name] = os.environ[name]
        elif file_vals.get(name) and str(file_vals[name]).strip() != "":
            data[name] = file_vals[name]


class PaperMinerSettings(BaseModel):
    """Optional paper-specific paths and arxiv query config."""

    arxivpdf_dir: Path | None = None
    md_papers_raw_dir: Path | None = None
    md_papers_dir: Path | None = None
    filtered_dir: Path | None = None
    failed_dir: Path | None = None
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
    project_root: Path = Field(
        default_factory=_repo_root
    )
    config_file: Path = Field(
        default_factory=lambda: _repo_root() / "config.yaml"
    )
    # 绝对路径：本机 Obsidian Vault 根目录（见 config.example.yaml）
    vault_root: Path
    #: Vault 内论文 PDF 附件目录；未配置时默认为 ``{vault_root}/02_Assets/Papers``，加载时自动创建。
    vault_assets_dir: Path | None = None
    playground_dir: Path = Field(
        default_factory=lambda: _repo_root() / "playground"
    )
    inbox_folder: Path | None = None
    log_level: str = "INFO"
    #: Optics Lens 配置目录；可用 ``CHIMERA_LENSES_DIR`` 覆盖，默认 ``~/.chimera/lenses``。
    lenses_dir: Path = Field(
        default_factory=lambda: Path.home() / ".chimera" / "lenses",
        validation_alias=AliasChoices("CHIMERA_LENSES_DIR", "lenses_dir"),
    )

    # --- LLM secrets (12-Factor: OS env / .env only; never put these in config.yaml) ---
    OPENAI_API_KEY: SecretStr | None = Field(default=None)
    DEEPSEEK_API_KEY: SecretStr | None = Field(default=None)
    ANTHROPIC_API_KEY: SecretStr | None = Field(default=None)
    GEMINI_API_KEY: SecretStr | None = Field(default=None)

    # --- LLM defaults (non-secret; safe in config.yaml) ---
    default_llm_base_url: str = Field(default="https://api.openai.com/v1")
    default_llm_model: str = Field(default="gpt-4o-mini")
    #: 单次 HTTP 请求总超时（秒）；Anatomist / 长上下文等场景建议 300–900。
    default_llm_timeout_seconds: float = Field(default=300.0, ge=5.0, le=3600.0)

    # --- Oligo Wash: cheap model for tool compression (optional; all from env / .env) ---
    WASH_MODEL_BASE_URL: str | None = Field(default=None)
    WASH_MODEL_NAME: str | None = Field(default=None)
    WASH_MODEL_API_KEY: SecretStr | None = Field(default=None)

    # --- Telegram (secrets via env / .env) ---
    tg_bot_token: SecretStr | None = Field(default=None, alias="TG_BOT_TOKEN")
    tg_chat_id: SecretStr | None = Field(default=None, alias="TG_CHAT_ID")

    # --- [PaperMiner Settings] Optional sub-block ---
    paper_miner: PaperMinerSettings | None = None

    # --- Oligo Agent (tool execution / wash policy; override via config.yaml oligo_agent) ---
    oligo_agent: OligoAgentConfig = Field(default_factory=OligoAgentConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # pydantic-settings merges with deep_update(current_source, accumulated_state):
        # accumulated state from earlier sources wins on key conflicts. Therefore the
        # first entry below has the highest priority (init > env > dotenv > yaml).
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            CrucibleYamlSettingsSource(settings_cls),
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
    def _shred_llm_secrets_from_merged_input(cls, data: Any) -> Any:
        """
        Defense in depth: strip LLM API key fields from the merged input dict (any
        casing), then re-apply only from OS environment and `.env`. This neutralizes
        any plaintext keys that might still reach the merged payload while keeping
        env/dotenv as the sole sources of truth for these secrets.
        """
        if not isinstance(data, Mapping):
            return data
        d = dict(data)
        _pop_llm_secret_keys_case_insensitive(d)
        _restore_llm_secrets_from_os_and_dotenv(d)
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

    @model_validator(mode="after")
    def _ensure_paper_miner_dirs(self) -> "Settings":
        """Populate PaperMiner routed directories as absolute paths."""
        pm = self.paper_miner or PaperMinerSettings()
        if pm.papers_root is None:
            pm.papers_root = self.project_root / "papers"
        if pm.arxivpdf_dir is None:
            pm.arxivpdf_dir = pm.papers_root / "arxivpdf"
        if pm.md_papers_raw_dir is None:
            pm.md_papers_raw_dir = pm.papers_root / "md_papers_raw"
        if pm.md_papers_dir is None:
            pm.md_papers_dir = pm.papers_root / "md_papers"
        if pm.filtered_dir is None:
            pm.filtered_dir = pm.papers_root / "filtered"
        if pm.failed_dir is None:
            pm.failed_dir = pm.papers_root / "failed"
        self.paper_miner = pm
        return self

    @model_validator(mode="after")
    def _default_vault_assets_dir(self) -> "Settings":
        """Default vault PDF assets folder under vault_root."""
        if self.vault_assets_dir is None:
            object.__setattr__(
                self, "vault_assets_dir", self.vault_root / "02_Assets" / "Papers"
            )
        return self

    @model_validator(mode="after")
    def _default_playground_layout(self) -> "Settings":
        """Keep playground path defaults pure (no filesystem side effects)."""
        return self

    def ensure_directories(self) -> None:
        """Explicitly create all runtime directories used by the workflows."""
        pm = self.paper_miner_or_default
        dirs: tuple[Path, ...] = (
            pm.papers_root,
            pm.arxivpdf_dir,
            pm.md_papers_raw_dir,
            pm.md_papers_dir,
            pm.filtered_dir,
            pm.failed_dir,
            self.vault_root,
            self.vault_assets_dir,
            self.playground_dir,
            self.playground_dir / "pdfs",
            self.playground_dir / "md_raw",
            self.playground_dir / "md_clean",
            self.lenses_dir,
        )
        for path in dirs:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(
                    f"Failed ensuring directory '{path}': {exc}"
                ) from exc

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
