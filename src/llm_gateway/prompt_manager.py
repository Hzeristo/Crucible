"""Prompt rendering gateway based on Jinja2 templates."""

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
            root = Path(__file__).resolve().parents[2]
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
        logger.debug(
            f"PromptManager initialized with template directory: {self.template_path}"
        )

    def render(self, template_name: str, **kwargs: Any) -> str:
        """
        渲染指定模板
        :param template_name: e.g., 'tasks/phase3_filter.j2'
        :param kwargs: 传递给模板的变量 (任意键值对，如 payload, json_schema 等)
        :return: 渲染后的 String
        """
        if Path(template_name).is_absolute() or ".." in Path(template_name).parts:
            raise ValueError(f"Unsafe template path: {template_name}")
        try:
            logger.debug(f"Rendering template: {template_name}")
            template = self.env.get_template(template_name)
            result = template.render(**kwargs)
            logger.debug(f"Template {template_name} rendered successfully")
            return result
        except TemplateNotFound as exc:
            logger.error(f"Template not found: {template_name} in {self.template_path}")
            raise FileNotFoundError(
                f"Template not found: {template_name} in {self.template_path}"
            ) from exc
        except (TemplateSyntaxError, UndefinedError) as exc:
            logger.error(f"Template rendering failed for {template_name}: {exc}")
            raise RuntimeError(f"Template rendering failed: {exc}") from exc
