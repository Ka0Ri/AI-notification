"""Load a run config: locate it, load its env file, expand ${VAR}, check keys."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .errors import ConfigError

ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve(path: str | Path) -> Path:
    """A config is named by path, or by bare name under $NOTIFY_CONFIG_DIR.

    Callers live in other projects and cannot rely on this repo's layout.
    """
    path = Path(path)
    if path.exists() or path.is_absolute():
        return path
    config_dir = os.environ.get("NOTIFY_CONFIG_DIR")
    if config_dir:
        for candidate in (Path(config_dir) / path, Path(config_dir) / f"{path}.yaml"):
            if candidate.exists():
                return candidate
    return path


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse a KEY=VALUE file. No shell semantics - no export, no substitution."""
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _load_env(cfg_env_file: str | None, config_path: Path) -> None:
    """Put an env file's values into os.environ without overriding what is set."""
    env_file = os.environ.get("NOTIFY_ENV_FILE") or cfg_env_file
    if not env_file:
        return
    path = Path(env_file)
    if not path.is_absolute():
        path = config_path.parent / path
    if not path.exists():
        raise ConfigError(f"{config_path}: env_file not found: {path}")
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def _expand(value):
    """Substitute ${VAR} from the environment. Unset vars become empty strings so
    that --no-post / --no-ai runs work without secrets; the poster and the model
    client each report their own missing variable."""
    if isinstance(value, str):
        return ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def require(block: dict, keys: tuple[str, ...], where: str) -> None:
    """A config defines its whole run, so a key nobody set is an error, not a default."""
    missing = [k for k in keys if block.get(k) is None]
    if missing:
        raise ConfigError(f"{where} block is missing: {', '.join(missing)}")


def load(path: str | Path) -> dict:
    path = resolve(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no such config: {path}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")

    # The env file has to land before expansion, or ${VAR} resolves to nothing.
    _load_env(raw.get("env_file"), path.resolve())

    cfg = _expand(raw)
    if not cfg.get("name"):
        raise ConfigError(f"{path}: missing required key 'name'")

    cfg.setdefault("description", cfg["name"])
    cfg.setdefault("ai", {})
    cfg.setdefault("notify", {})
    cfg.setdefault("schedule", {})
    cfg["path"] = str(path.resolve())
    return cfg
