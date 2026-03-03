"""
Config loader with YAML parsing and env var substitution.
"""

import os
import re
import yaml
import hashlib
from pathlib import Path
from typing import Any, Dict
from .schema import V2Config


def compute_config_hash(config: V2Config) -> str:
    """
    Compute deterministic hash of config for plan validation.
    
    Excludes safety gates from hash (can change between plan and apply).
    """
    import json
    
    # Convert config to dict, exclude safety (mutable between plan/apply)
    config_dict = config.model_dump(mode="json", exclude={"safety"})
    
    # Sort keys for deterministic hash
    config_json = json.dumps(config_dict, sort_keys=True)
    
    return hashlib.sha256(config_json.encode()).hexdigest()[:16]


def resolve_env_vars(data: Any) -> Any:
    """
    Recursively resolve ${ENV_VAR} placeholders in config.
    
    Examples:
      api_key: ${HUBSPOT_API_KEY}
      server: ${MAILCHIMP_SERVER:-us1}  # with default value
    """
    if isinstance(data, dict):
        return {k: resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_env_vars(item) for item in data]
    elif isinstance(data, str):
        # Match ${VAR} or ${VAR:-default}
        pattern = r'\$\{([^}:]+)(?::(-)?([^}]*))?\}'
        
        def replace_match(match):
            var_name = match.group(1)
            has_default = match.group(2) is not None
            default_value = match.group(3) if has_default else None
            
            # Get env var or use default
            value = os.getenv(var_name)
            if value is None:
                if has_default:
                    return default_value
                else:
                    raise ValueError(f"Environment variable {var_name} not set and no default provided")
            return value
        
        return re.sub(pattern, replace_match, data)
    else:
        return data


def load_config(path: str) -> V2Config:
    """
    Load config from YAML file with env var resolution and Pydantic validation.
    
    Args:
        path: Path to YAML config file
        
    Returns:
        Validated V2Config instance
        
    Raises:
        FileNotFoundError: Config file not found
        ValueError: Invalid config or missing env vars
        ValidationError: Pydantic validation failed
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    
    # Load YAML (explicitly use UTF-8 encoding)
    with open(config_path, encoding='utf-8') as f:
        raw_data = yaml.safe_load(f)
    
    if not raw_data:
        raise ValueError(f"Config file is empty: {path}")
    
    # Resolve env vars
    resolved_data = resolve_env_vars(raw_data)
    
    # Validate with Pydantic (runs all @field_validator checks)
    config = V2Config(**resolved_data)
    
    return config
