"""The host reads YAML once; containers receive JSON with internal proxy URLs."""

import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

MODEL_TIERS = ("expert", "standard", "starter")
MODEL_FIELDS = ("base_url", "model_name", "api_key")
MODEL_CONFIG_PATH = Path(__file__).resolve().parent.parent / "model-config.yaml"
MODEL_CONFIG_ENV = "A3_MODEL_CONFIG_JSON"


def validate_base_url(value: str) -> str:
    """Accept a complete HTTP(S) API base URL without credentials or query data."""
    try:
        url = urlsplit(value)
        valid = (url.scheme in ("http", "https") and url.hostname
                 and url.username is None and url.password is None
                 and not url.query and not url.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in value)
                 and "\\" not in value
                 and not any(s in (".", "..") for s in unquote(url.path).replace("\\", "/").split("/")))
        port = url.port  # Also validates malformed and out-of-range ports.
        valid = valid and port != 0
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("base_url must be an HTTP(S) API URL without credentials, query or fragment")
    return value.rstrip("/")


def _model_configs(data) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("model-config.yaml must contain a mapping")
    models = {}
    for tier in MODEL_TIERS:
        entry = data.get(tier)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"{tier} must contain a mapping")
        # Empty template sections are unconfigured, just like absent tiers.
        if not entry or all(value is None or value == "" for value in entry.values()):
            continue
        model = {}
        for field in MODEL_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{tier}.{field} must be a non-empty string")
            model[field] = value.strip()
        try:
            model["base_url"] = validate_base_url(model["base_url"])
        except ValueError:
            raise ValueError(f"{tier}.base_url must be a valid HTTP(S) API base URL") from None
        models[tier] = model
    return models


def load_model_config(path=None) -> dict:
    """Load configured tiers only; extra/missing tiers are silently ignored.

    An explicit path always reads YAML. Without a path, prefer the host-provided
    run snapshot inside a container, otherwise read the default YAML file.
    Error messages deliberately omit configuration values (including secrets).
    """
    if path is None and MODEL_CONFIG_ENV in os.environ:
        try:
            data = json.loads(os.environ[MODEL_CONFIG_ENV])
        except (ValueError, TypeError):
            raise ValueError("Invalid model configuration snapshot") from None
    else:
        try:
            import yaml
        except ImportError:
            raise ValueError("YAML loading requires: python3 -m pip install -r requirements.txt") from None
        try:
            with Path(path or MODEL_CONFIG_PATH).open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError:
            raise ValueError("Invalid YAML in model configuration") from None
    return _model_configs(data)
