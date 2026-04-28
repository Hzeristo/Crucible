"""Jinja2 prompt rendering (filesystem templates under repo ``prompts/``)."""

import json
import logging
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    TemplateSyntaxError,
    UndefinedError,
)

logger = logging.getLogger(__name__)


class PromptManager:
    """加载并渲染 prompts 目录下的 Jinja2 模板。"""

    def __init__(self, template_dir: str | Path | None = None) -> None:
        if template_dir is None:
            # src/crucible/ports/prompts/jinja_prompt_manager.py -> parents[4] = repo root
            root = Path(__file__).resolve().parents[4]
            self.template_path = root / "prompts"
        else:
            self.template_path = Path(template_dir).expanduser().resolve()
        if not self.template_path.exists() or not self.template_path.is_dir():
            raise FileNotFoundError(
                f"Template directory not found: {self.template_path}"
            )

        self.env = Environment(
            loader=FileSystemLoader(self.template_path),
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )
        self.env.filters["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)
        logger.debug(
            "[Prompt] PromptManager initialized with template directory: %s",
            self.template_path,
        )

    def render(self, template_name: str, **kwargs: Any) -> str:
        if Path(template_name).is_absolute() or ".." in Path(template_name).parts:
            raise ValueError(f"Unsafe template path: {template_name}")
        try:
            logger.debug("[Prompt] Rendering template: %s", template_name)
            template = self.env.get_template(template_name)
            result = template.render(**kwargs)
            logger.debug("[Prompt] Template %s rendered successfully", template_name)
            return result
        except TemplateNotFound as exc:
            logger.error(
                "[Prompt] Template not found: %s in %s", template_name, self.template_path
            )
            raise FileNotFoundError(
                f"Template not found: {template_name} in {self.template_path}"
            ) from exc
        except (TemplateSyntaxError, UndefinedError) as exc:
            logger.error("[Prompt] Template rendering failed for %s: %s", template_name, exc)
            raise RuntimeError(f"Template rendering failed: {exc}") from exc
