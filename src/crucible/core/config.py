"""Project Chimera unified configuration: ``~/.chimera/config.toml`` (+ env / legacy YAML).

Crucible-specific paths (paper_miner, playground, etc.) remain here; Oligo/LLM/Wash/Vault
policy live in nested blocks per ``docs/CONFIG_SCHEMA.md``.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any, Literal, Mapping, Self, get_args, get_origin

import tomlkit
import yaml
from dotenv import dotenv_values
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)
from pydantic_settings.sources import (
    InitSettingsSource,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)

from src.crucible.core.platform import get_chimera_root, get_config_path
from src.crucible.core.schemas import OligoAgentConfig

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    """Resolve repository root from this module location (src/crucible/core/config.py)."""
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _repo_root()

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

_PAPER_MINER_KEYS = frozenset({
    "arxivpdf_dir", "md_papers_raw_dir", "md_papers_dir",
    "filtered_dir", "failed_dir", "papers_root", "arxiv_query", "arxiv_max_results",
})

_SYSTEM_TOP_KEYS = frozenset({
    "vault_root", "skills_dir", "log_level", "inbox_folder",
    "vault_assets_dir", "lenses_dir", "playground_dir",
})


def _is_path_like_key(key: str) -> bool:
    normalized = key.lower()
    return normalized.endswith(("_path", "_dir", "_root", "_file", "_folder")) or normalized in {
        "path", "dir", "root", "file", "vault",
    }


def _is_windows_drive_relative(path_value: Path) -> bool:
    return bool(path_value.drive) and not path_value.is_absolute()


def _normalize_config_path(value: str | Path, project_root: Path) -> Path:
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
    if isinstance(value, Mapping):
        return {k: _convert_path_like_values(v, k, project_root) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert_path_like_values(item, key_hint, project_root) for item in value]
    if key_hint and _is_path_like_key(key_hint) and isinstance(value, (str, Path)):
        return _normalize_config_path(value, project_root)
    return value


def _is_path_annotation(annotation: Any) -> bool:
    if annotation is Path:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return Path in get_args(annotation)


def _pop_llm_secret_keys_case_insensitive(data: dict[str, Any]) -> None:
    for key in list(data.keys()):
        if key.lower() in _LLM_SECRET_KEY_NAMES_LOWER:
            data.pop(key, None)


def _restore_llm_secrets_from_os_and_dotenv(data: dict[str, Any]) -> None:
    env_path = PROJECT_ROOT / ".env"
    file_vals = dotenv_values(env_path) if env_path.is_file() else {}
    for name in _CANONICAL_LLM_SECRET_ENV_NAMES:
        if name in os.environ and str(os.environ[name]).strip() != "":
            data[name] = os.environ[name]
        elif file_vals.get(name) and str(file_vals[name]).strip() != "":
            data[name] = file_vals[name]

class SystemConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    vault_root: Path | None = None
    skills_dir: Path | None = None
    log_level: str = Field(default="INFO", description="DEBUG|INFO|WARNING|ERROR")
    inbox_folder: Path | None = None
    vault_assets_dir: Path | None = None
    lenses_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("lenses_dir", "CHIMERA_LENSES_DIR"),
    )
    playground_dir: Path | None = None


class OligoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    host: str = "127.0.0.1"
    port: int = Field(33333, ge=1, le=65535)
    max_turns: int = Field(10, ge=1)
    tool_execution_deadline_seconds: float = Field(45.0, ge=1.0, le=600.0)


class LLMModelConfig(BaseModel):
    """单个 LLM 模型的配置。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(description="Provider name (openai, deepseek, anthropic)")
    model: str = Field(description="Model ID")
    api_key: str = Field(description="API Key")
    base_url: str = Field(description="API base URL")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=90, gt=0)


class LLMProviderSlotConfig(BaseModel):
    """Astrocyte / HUD 可选的命名 Provider 槽（与 `[llm.providers.*]` 对齐）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=90, gt=0)


def _default_llm_provider_slots() -> dict[str, LLMProviderSlotConfig]:
    return {
        "openai": LLMProviderSlotConfig(
            name="OpenAI",
            api_key="",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
            temperature=0.7,
            timeout_seconds=90,
        ),
        "deepseek": LLMProviderSlotConfig(
            name="DeepSeek",
            api_key="",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            temperature=0.7,
            timeout_seconds=90,
        ),
        "anthropic": LLMProviderSlotConfig(
            name="Anthropic",
            api_key="",
            base_url="https://api.anthropic.com/v1",
            model="claude-3-5-sonnet-20241022",
            temperature=0.7,
            timeout_seconds=90,
        ),
    }


class LLMConfig(BaseModel):
    """LLM 配置集合：Working / Wash / Router（三槽位）。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    working: LLMModelConfig = Field(description="主力模型（用于 Final Stream）")
    wash: LLMModelConfig = Field(description="清洗模型（用于 Wash）")
    router: LLMModelConfig | None = Field(
        None,
        description="路由模型（可选，不配置则使用 working）",
    )
    providers: dict[str, LLMProviderSlotConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _merge_llm_provider_slots(self) -> Self:
        merged = {**_default_llm_provider_slots(), **self.providers}
        object.__setattr__(self, "providers", merged)
        return self


def _default_llm_config() -> LLMConfig:
    """与历史默认行为大致对齐的占位（密钥由环境或 TOML 覆盖）。"""
    return LLMConfig(
        working=LLMModelConfig(
            provider="openai",
            model="deepseek-chat",
            api_key="",
            base_url="https://api.openai.com/v1",
            temperature=0.7,
            timeout_seconds=90,
        ),
        wash=LLMModelConfig(
            provider="deepseek",
            model="deepseek-chat",
            api_key="",
            base_url="https://api.deepseek.com",
            temperature=0.1,
            timeout_seconds=30,
        ),
    )


class WashConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_chars: int = Field(1200, ge=0)
    bypass_tools: list[str] = Field(
        default_factory=lambda: [
            "search_vault_attribute",
            "metadata_lookup",
            "planner_json",
        ],
    )
    force_tools: list[str] = Field(
        default_factory=lambda: ["search_vault", "web_search", "read_markdown"],
    )


class VaultRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_size: int = Field(200, ge=0)
    cache_ttl_seconds: int = Field(300, ge=0)


class AstrocyteConfigBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Literal["dark", "light"] = "dark"
    enable_clipboard_capture: bool = False


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


class ChimeraConfig(BaseSettings):
    """Unified Chimera settings: TOML file + env (+ legacy YAML bootstrap when TOML missing)."""

    model_config = SettingsConfigDict(
        env_prefix="CHIMERA_",
        env_nested_delimiter="__",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    system: SystemConfig = Field(default_factory=SystemConfig)
    oligo: OligoConfig = Field(default_factory=OligoConfig)
    llm: LLMConfig = Field(default_factory=_default_llm_config)
    wash: WashConfig = Field(default_factory=WashConfig)
    vault: VaultRuntimeConfig = Field(default_factory=VaultRuntimeConfig)
    astrocyte: AstrocyteConfigBlock = Field(default_factory=AstrocyteConfigBlock)

    paper_miner: PaperMinerSettings | None = None
    project_root: Path = Field(default_factory=_repo_root)

    OPENAI_API_KEY: SecretStr | None = None
    DEEPSEEK_API_KEY: SecretStr | None = None
    ANTHROPIC_API_KEY: SecretStr | None = None
    GEMINI_API_KEY: SecretStr | None = None

    WASH_MODEL_BASE_URL: str | None = None
    WASH_MODEL_NAME: str | None = None
    WASH_MODEL_API_KEY: SecretStr | None = None

    tg_bot_token: SecretStr | None = Field(default=None, validation_alias="TG_BOT_TOKEN")
    tg_chat_id: SecretStr | None = Field(default=None, validation_alias="TG_CHAT_ID")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _chimera_toml_or_legacy_source(settings_cls),
            file_secret_settings,
        )

    @model_validator(mode="before")
    @classmethod
    def _shred_llm_secrets_from_merged_input(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        d = dict(data)
        _pop_llm_secret_keys_case_insensitive(d)
        _restore_llm_secrets_from_os_and_dotenv(d)
        return d

    @model_validator(mode="before")
    @classmethod
    def _merge_paper_miner_flat_keys(cls, data: Any) -> Any:
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
        if not isinstance(data, Mapping):
            return data
        return _convert_path_like_values(dict(data), project_root=PROJECT_ROOT)

    @model_validator(mode="after")
    def _normalize_system_paths(self) -> Self:
        sys_cfg = self.system.model_copy(deep=True)
        for field_name, field_info in SystemConfig.model_fields.items():
            if not _is_path_annotation(field_info.annotation):
                continue
            value = getattr(sys_cfg, field_name)
            if isinstance(value, (Path, str)):
                setattr(sys_cfg, field_name, _normalize_config_path(value, PROJECT_ROOT))
        if sys_cfg.skills_dir is None:
            sys_cfg = sys_cfg.model_copy(update={"skills_dir": get_chimera_root() / "skills"})
        if sys_cfg.lenses_dir is None:
            sys_cfg = sys_cfg.model_copy(update={"lenses_dir": get_chimera_root() / "lenses"})
        if sys_cfg.playground_dir is None:
            sys_cfg = sys_cfg.model_copy(update={"playground_dir": self.project_root / "playground"})
        object.__setattr__(self, "system", sys_cfg)
        return self

    @model_validator(mode="after")
    def _require_vault_root(self) -> Self:
        if self.system.vault_root is None:
            raise ValueError(
                "system.vault_root is required: set it in ~/.chimera/config.toml, legacy config.yaml, "
                "or CHIMERA_SYSTEM__VAULT_ROOT.",
            )
        return self

    @model_validator(mode="after")
    def _default_vault_assets_dir(self) -> Self:
        sys_cfg = self.system
        if sys_cfg.vault_assets_dir is None and sys_cfg.vault_root is not None:
            sys_cfg = sys_cfg.model_copy(
                update={"vault_assets_dir": sys_cfg.vault_root / "02_Assets" / "Papers"},
            )
            object.__setattr__(self, "system", sys_cfg)
        return self

    @model_validator(mode="after")
    def _ensure_paper_miner_dirs(self) -> Self:
        pm = self.paper_miner or PaperMinerSettings()
        if pm.papers_root is None:
            pm = pm.model_copy(update={"papers_root": self.project_root / "papers"})
        if pm.arxivpdf_dir is None:
            pm = pm.model_copy(update={"arxivpdf_dir": pm.papers_root / "arxivpdf"})
        if pm.md_papers_raw_dir is None:
            pm = pm.model_copy(update={"md_papers_raw_dir": pm.papers_root / "md_papers_raw"})
        if pm.md_papers_dir is None:
            pm = pm.model_copy(update={"md_papers_dir": pm.papers_root / "md_papers"})
        if pm.filtered_dir is None:
            pm = pm.model_copy(update={"filtered_dir": pm.papers_root / "filtered"})
        if pm.failed_dir is None:
            pm = pm.model_copy(update={"failed_dir": pm.papers_root / "failed"})
        object.__setattr__(self, "paper_miner", pm)
        return self

    # --- Backward-compatible flat accessors (pre-ChimeraConfig call sites) ---

    @property
    def vault_root(self) -> Path:
        assert self.system.vault_root is not None
        return self.system.vault_root

    @property
    def oligo_host(self) -> str:
        return self.oligo.host

    @property
    def oligo_port(self) -> int:
        return self.oligo.port

    @property
    def lenses_dir(self) -> Path:
        assert self.system.lenses_dir is not None
        return self.system.lenses_dir

    @property
    def inbox_folder(self) -> Path | None:
        return self.system.inbox_folder

    @property
    def vault_assets_dir(self) -> Path | None:
        return self.system.vault_assets_dir

    @property
    def playground_dir(self) -> Path:
        assert self.system.playground_dir is not None
        return self.system.playground_dir

    @property
    def log_level(self) -> str:
        return self.system.log_level

    @property
    def default_llm_timeout_seconds(self) -> float:
        return float(self.llm.working.timeout_seconds)

    @property
    def default_llm_model(self) -> str:
        return self.llm.working.model

    @property
    def default_llm_base_url(self) -> str:
        u = (self.llm.working.base_url or "").strip()
        return u or "https://api.openai.com/v1"

    @property
    def oligo_agent(self) -> OligoAgentConfig:
        return OligoAgentConfig(
            tool_execution_deadline_seconds=float(self.oligo.tool_execution_deadline_seconds),
            wash_min_chars=self.wash.min_chars,
            bypass_wash_tools=set(self.wash.bypass_tools),
            force_wash_tools=set(self.wash.force_tools),
        )

    def ensure_directories(self) -> None:
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
            self.system.skills_dir,
        )
        for path in dirs:
            if path is None:
                continue
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(f"Failed ensuring directory '{path}': {exc}") from exc

    def require_path(self, field_name: str) -> Path:
        if field_name == "vault_root":
            return self.vault_root
        if field_name == "vault_assets_dir":
            v = self.vault_assets_dir
            if v is None:
                raise ValueError("Required path setting is missing: vault_assets_dir")
            return v
        if field_name == "inbox_folder":
            v = self.inbox_folder
            if v is None:
                raise ValueError("Required path setting is missing: inbox_folder")
            return v
        value = getattr(self, field_name, None)
        if value is None:
            raise ValueError(f"Required path setting is missing: {field_name}")
        if not isinstance(value, Path):
            raise TypeError(
                f"Setting '{field_name}' must be pathlib.Path, got: {type(value).__name__}",
            )
        return value

    @property
    def paper_miner_or_default(self) -> PaperMinerSettings:
        return self.paper_miner or PaperMinerSettings()


def _scrub_secrets_for_disk(data: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(data)
    llm_block = out.get("llm")
    if isinstance(llm_block, dict):
        for slot in ("working", "wash", "router"):
            sub = llm_block.get(slot)
            if isinstance(sub, dict) and sub.get("api_key"):
                sub["api_key"] = ""
        prov = llm_block.get("providers")
        if isinstance(prov, dict):
            for sub in prov.values():
                if isinstance(sub, dict) and sub.get("api_key"):
                    sub["api_key"] = ""
    for key in list(out.keys()):
        if key.upper().endswith("_API_KEY") or key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
            out.pop(key, None)
    return out


def _paths_to_serializable(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _paths_to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_paths_to_serializable(v) for v in obj]
    return obj


def _dict_to_tomlkit_table(d: dict[str, Any]) -> tomlkit.items.Table:
    t = tomlkit.table()
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            subt = _dict_to_tomlkit_table(v)
            if subt:
                t[k] = subt
        else:
            t[k] = v
    return t


def _write_chimera_toml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scrubbed = _scrub_secrets_for_disk(data)
    serializable = _paths_to_serializable(scrubbed)
    doc = tomlkit.document()
    for k, v in serializable.items():
        if v is None:
            continue
        if isinstance(v, dict):
            tbl = _dict_to_tomlkit_table(v)
            if tbl:
                doc[k] = tbl
        else:
            doc[k] = v
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    logger.info("[Config] Wrote unified config template to %s", path)


def _read_legacy_yaml_dict() -> dict[str, Any]:
    yaml_path = PROJECT_ROOT / "config.yaml"
    if not yaml_path.is_file():
        return {}
    with open(yaml_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        return {}
    data = dict(raw)
    _pop_llm_secret_keys_case_insensitive(data)
    return _convert_path_like_values(data, project_root=PROJECT_ROOT)


def _legacy_yaml_to_chimera_nested(raw: dict[str, Any]) -> dict[str, Any]:
    """Map flat legacy YAML to nested ChimeraConfig dict."""
    raw = dict(raw)

    system: dict[str, Any] = {}
    for k in _SYSTEM_TOP_KEYS:
        if k in raw:
            system[k] = raw.pop(k)

    oligo_host = raw.pop("oligo_host", "127.0.0.1")
    oligo_port = raw.pop("oligo_port", 33333)
    oligo_block = raw.pop("oligo", None)
    if isinstance(oligo_block, Mapping):
        oligo_host = oligo_block.get("host", oligo_host)
        oligo_port = oligo_block.get("port", oligo_port)

    oagent = raw.pop("oligo_agent", None) or {}
    if not isinstance(oagent, Mapping):
        oagent = {}

    oligo: dict[str, Any] = {
        "host": oligo_host,
        "port": int(oligo_port),
        "max_turns": 10,
        "tool_execution_deadline_seconds": float(
            oagent.get("tool_execution_deadline_seconds", 45.0),
        ),
    }

    bypass = oagent.get("bypass_wash_tools")
    if isinstance(bypass, set):
        bypass = list(bypass)
    force = oagent.get("force_wash_tools")
    if isinstance(force, set):
        force = list(force)

    wash: dict[str, Any] = {
        "min_chars": int(oagent.get("wash_min_chars", 1200)),
        "bypass_tools": list(bypass)
        if isinstance(bypass, list)
        else ["search_vault_attribute", "metadata_lookup", "planner_json"],
        "force_tools": list(force)
        if isinstance(force, list)
        else ["search_vault", "web_search", "read_markdown"],
    }

    default_model = raw.pop("default_llm_model", "gpt-4o-mini")
    default_url = raw.pop("default_llm_base_url", None) or "https://api.openai.com/v1"
    default_timeout = float(raw.pop("default_llm_timeout_seconds", 90.0))
    to_int = max(1, int(round(default_timeout)))

    llm: dict[str, Any] = {
        "working": {
            "provider": "openai",
            "model": default_model,
            "api_key": "",
            "base_url": default_url,
            "temperature": 0.7,
            "timeout_seconds": to_int,
        },
        "wash": {
            "provider": "openai",
            "model": default_model,
            "api_key": "",
            "base_url": default_url,
            "temperature": 0.1,
            "timeout_seconds": 30,
        },
    }

    nested: dict[str, Any] = {
        "system": system,
        "oligo": oligo,
        "llm": llm,
        "wash": wash,
        "vault": {"cache_size": 200, "cache_ttl_seconds": 300},
        "astrocyte": {"theme": "dark", "enable_clipboard_capture": False},
    }

    pm_block = raw.pop("paper_miner", None)
    flat_pm = {k: raw.pop(k) for k in list(raw.keys()) if k in _PAPER_MINER_KEYS}
    if flat_pm:
        if isinstance(pm_block, dict):
            pm_block = {**pm_block, **flat_pm}
        else:
            pm_block = flat_pm
    if pm_block:
        nested["paper_miner"] = pm_block

    for key in ("tg_bot_token", "TG_BOT_TOKEN"):
        if key in raw:
            nested["tg_bot_token"] = raw.pop(key)
            break
    for key in ("tg_chat_id", "TG_CHAT_ID"):
        if key in raw:
            nested["tg_chat_id"] = raw.pop(key)
            break

    for wash_key in ("WASH_MODEL_BASE_URL", "WASH_MODEL_NAME", "WASH_MODEL_API_KEY"):
        if wash_key in raw:
            nested[wash_key] = raw.pop(wash_key)

    if raw:
        logger.debug(
            "[Config] Legacy YAML keys not mapped to Chimera TOML (ignored): %s",
            sorted(raw.keys()),
        )

    return nested


def _chimera_toml_or_legacy_source(settings_cls: type[BaseSettings]) -> PydanticBaseSettingsSource:
    path = get_config_path()
    if path.is_file():
        return TomlConfigSettingsSource(settings_cls, toml_file=path)
    legacy_flat = _read_legacy_yaml_dict()
    nested = _legacy_yaml_to_chimera_nested(legacy_flat)
    try:
        _write_chimera_toml(path, nested)
    except OSError as exc:
        logger.warning("[Config] Could not write %s: %s", path, exc)
    return InitSettingsSource(
        settings_cls,
        init_kwargs=nested,
        nested_model_default_partial_update=True,
    )


_global_config: ChimeraConfig | None = None


def get_config() -> ChimeraConfig:
    """Singleton Chimera configuration (TOML + env + optional legacy YAML bootstrap)."""
    global _global_config
    if _global_config is None:
        _global_config = ChimeraConfig()
    return _global_config


def load_config() -> ChimeraConfig:
    """Deprecated alias for :func:`get_config`."""
    return get_config()


Settings = ChimeraConfig
