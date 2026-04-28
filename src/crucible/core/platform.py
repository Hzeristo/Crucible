"""
平台相关路径与环境的统一抽象层。
所有业务代码必须通过此模块获取路径，禁止直接使用 Path.home() 或硬编码。
"""
from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _migrate_legacy_dotless_chimera() -> None:
    """若旧数据在 ``~/chimera``（无点）且 ``~/.chimera`` 尚不存在，则整体迁移到 ``~/.chimera``。"""
    base = Path.home()
    old_dir = base / "chimera"
    new_dir = base / ".chimera"
    if old_dir.exists() and not new_dir.exists():
        shutil.move(str(old_dir), str(new_dir))
        logger.info("[Config] Migrated data from ~/chimera to ~/.chimera")


def get_chimera_root() -> Path:
    """
    获取 Chimera 的根目录（用户数据目录）。

    Windows: 用户主目录（``%USERPROFILE%``）下的 ``.chimera``。
    Linux/macOS: 用户主目录下的 ``.chimera``。

    Returns:
        绝对路径，已解析 ~ 和环境变量
    """
    if sys.platform == "win32":
        # Windows: 使用 USERPROFILE 而不是 HOME
        # 避免某些情况下 ~ 被解析为网络路径
        base = Path.home()
    else:
        # Linux/macOS: 标准 HOME
        base = Path.home()

    _migrate_legacy_dotless_chimera()

    chimera_dir = base / ".chimera"
    chimera_dir.mkdir(parents=True, exist_ok=True)
    return chimera_dir.resolve()  # 返回绝对路径


def get_config_path() -> Path:
    """获取配置文件的绝对路径"""
    return get_chimera_root() / "config.toml"


def get_skills_dir() -> Path:
    """获取 Skills 目录的绝对路径"""
    skills_dir = get_chimera_root() / "skills"
    skills_dir.mkdir(exist_ok=True)
    return skills_dir


def get_skill_stats_path() -> Path:
    """获取 Skill 统计数据文件路径（`~/.chimera/skill_stats.json`）。"""
    return get_chimera_root() / "skill_stats.json"


def get_logs_dir() -> Path:
    """获取日志目录的绝对路径"""
    logs_dir = get_chimera_root() / "logs"
    logs_dir.mkdir(exist_ok=True)
    return logs_dir


def get_cache_dir() -> Path:
    """获取缓存目录的绝对路径"""
    cache_dir = get_chimera_root() / "cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir


# Linux 特定逻辑预留
def get_linux_systemd_service_path() -> Path | None:
    """
    获取 systemd service 文件路径（仅 Linux）。

    Returns:
        Linux: ~/.config/systemd/user/chimera-oligo.service
        其他平台: None
    """
    if sys.platform != "linux":
        return None

    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    return systemd_dir / "chimera-oligo.service"
