"""
Unit tests for config validation.

Tests:
- YAML loading
- Env var substitution
- Pydantic validation rules
- INV-002: Compliance lists (762, 773) never in sync lists
"""

import pytest
import os
import tempfile
from pathlib import Path
from pydantic import ValidationError

from corev2.config.loader import load_config, resolve_env_vars
from corev2.config.schema import V2Config, RunMode


def test_resolve_env_vars_simple():
    """Test simple env var substitution."""
    os.environ["TEST_VAR"] = "test_value"
    
    data = {"key": "${TEST_VAR}"}
    result = resolve_env_vars(data)
    
    assert result["key"] == "test_value"


def test_resolve_env_vars_with_default():
    """Test env var with default value."""
    # Variable not set, should use default
    data = {"key": "${NONEXISTENT_VAR:-default_value}"}
    result = resolve_env_vars(data)
    
    assert result["key"] == "default_value"


def test_resolve_env_vars_missing_no_default():
    """Test missing env var without default raises error."""
    data = {"key": "${NONEXISTENT_VAR_123}"}
    
    with pytest.raises(ValueError, match="not set and no default"):
        resolve_env_vars(data)


def test_resolve_env_vars_nested():
    """Test env var resolution in nested structures."""
    os.environ["TEST_KEY"] = "secret"
    
    data = {
        "outer": {
            "inner": "${TEST_KEY}",
            "list": ["${TEST_KEY}", "literal"]
        }
    }
    
    result = resolve_env_vars(data)
    
    assert result["outer"]["inner"] == "secret"
    assert result["outer"]["list"][0] == "secret"
    assert result["outer"]["list"][1] == "literal"


def test_load_config_valid(tmp_path):
    """Test loading valid config."""
    # Set required env vars
    os.environ["HUBSPOT_API_KEY"] = "test-hs-key"
    os.environ["MAILCHIMP_API_KEY"] = "test-mc-key"
    os.environ["MAILCHIMP_AUDIENCE_ID"] = "test-audience"
    
    config_path = tmp_path / "test_config.yaml"
    config_path.write_text("""
hubspot:
  api_key: ${HUBSPOT_API_KEY}
  lists:
    general_marketing:
      - id: "718"
        name: "Test List"
      - id: "872"
        name: "Special Campaign List"
      - id: "784"
        name: "Manual Override List"
  exclusions:
    critical: ["762", "773"]
    active_deals: ["717"]
    exit: ["700"]

mailchimp:
  api_key: ${MAILCHIMP_API_KEY}
  server_prefix: us1
  audience_id: ${MAILCHIMP_AUDIENCE_ID}

sync:
  batch_size: 100
  tag_prefix: ""
  ori_lists_field: "ORI_LISTS"
  force_subscribe: true

exclusion_matrix:
  general_marketing:
    lists: ["718"]
    exclude: ["762", "773", "717", "700"]
  special_campaigns:
    lists: ["872"]
    exclude: ["762", "773", "717"]
  manual_override:
    lists: ["784"]
    exclude: ["762", "773"]

list_exclusion_rules:
  "718": ["719"]

secondary_sync:
  enabled: true
  mappings:
    - exit_tag: "Exit_Journey_1"
      destination_list: "700"
      destination_name: "Test Destination"
      source_list: "718"
      source_name: "Test Source"

archival:
  exempt_tags: ["VIP"]
  preservation_patterns: ["^Manual_.*"]

safety:
  test_contact_limit: 10
  run_mode: "test"
  allow_archive: false
""")
    
    config = load_config(str(config_path))
    
    assert config.hubspot.api_key.get_secret_value() == "test-hs-key"
    assert config.mailchimp.audience_id == "test-audience"
    assert config.sync.batch_size == 100
    assert config.safety.run_mode == RunMode.TEST


def test_config_compliance_lists_in_sync_lists_rejected(tmp_path):
    """INV-002: Compliance list (762) in sync lists should be rejected."""
    os.environ["HUBSPOT_API_KEY"] = "test-key"
    os.environ["MAILCHIMP_API_KEY"] = "test-key"
    os.environ["MAILCHIMP_AUDIENCE_ID"] = "test-audience"
    
    config_path = tmp_path / "invalid_config.yaml"
    config_path.write_text("""
hubspot:
  api_key: ${HUBSPOT_API_KEY}
  lists:
    general_marketing:
      - id: "762"  # COMPLIANCE LIST - FORBIDDEN
        name: "Unsubscribed"
  exclusions:
    critical: ["762", "773"]
    active_deals: []
    exit: []

mailchimp:
  api_key: ${MAILCHIMP_API_KEY}
  server_prefix: us1
  audience_id: ${MAILCHIMP_AUDIENCE_ID}

sync:
  batch_size: 100
  ori_lists_field: "ORI_LISTS"

exclusion_matrix:
  general_marketing:
    lists: ["762"]
    exclude: ["762", "773"]
  special_campaigns:
    lists: []
    exclude: ["762", "773"]
  manual_override:
    lists: []
    exclude: ["762", "773"]

list_exclusion_rules: {}
secondary_sync: {}
archival:
  exempt_tags: []
  preservation_patterns: []
safety:
  test_contact_limit: 0
  run_mode: "prod"
  allow_archive: true
""")
    
    with pytest.raises(ValidationError, match="INV-002 VIOLATION"):
        load_config(str(config_path))


def test_config_compliance_lists_not_excluded_rejected(tmp_path):
    """INV-002: Missing compliance lists in exclusions should be rejected."""
    os.environ["HUBSPOT_API_KEY"] = "test-key"
    os.environ["MAILCHIMP_API_KEY"] = "test-key"
    os.environ["MAILCHIMP_AUDIENCE_ID"] = "test-audience"
    
    config_path = tmp_path / "invalid_config.yaml"
    config_path.write_text("""
hubspot:
  api_key: ${HUBSPOT_API_KEY}
  lists:
    general_marketing:
      - id: "718"
        name: "Test"
  exclusions:
    critical: []
    active_deals: []
    exit: []

mailchimp:
  api_key: ${MAILCHIMP_API_KEY}
  server_prefix: us1
  audience_id: ${MAILCHIMP_AUDIENCE_ID}

sync:
  batch_size: 100
  ori_lists_field: "ORI_LISTS"

exclusion_matrix:
  general_marketing:
    lists: ["718"]
    exclude: []  # MISSING 762, 773
  special_campaigns:
    lists: []
    exclude: ["762", "773"]
  manual_override:
    lists: []
    exclude: ["762", "773"]

list_exclusion_rules: {}
secondary_sync: {}
archival:
  exempt_tags: []
  preservation_patterns: []
safety:
  test_contact_limit: 0
  run_mode: "prod"
  allow_archive: true
""")
    
    with pytest.raises(ValidationError, match="INV-002 VIOLATION"):
        load_config(str(config_path))


def test_config_duplicate_list_ids_rejected(tmp_path):
    """Duplicate list IDs across groups should be rejected."""
    os.environ["HUBSPOT_API_KEY"] = "test-key"
    os.environ["MAILCHIMP_API_KEY"] = "test-key"
    os.environ["MAILCHIMP_AUDIENCE_ID"] = "test-audience"
    
    config_path = tmp_path / "invalid_config.yaml"
    config_path.write_text("""
hubspot:
  api_key: ${HUBSPOT_API_KEY}
  lists:
    general_marketing:
      - id: "718"
        name: "Test 1"
    special_campaigns:
      - id: "718"  # DUPLICATE
        name: "Test 2"
  exclusions:
    critical: ["762", "773"]
    active_deals: []
    exit: []

mailchimp:
  api_key: ${MAILCHIMP_API_KEY}
  server_prefix: us1
  audience_id: ${MAILCHIMP_AUDIENCE_ID}

sync:
  batch_size: 100
  ori_lists_field: "ORI_LISTS"

exclusion_matrix:
  general_marketing:
    lists: ["718"]
    exclude: ["762", "773"]
  special_campaigns:
    lists: ["718"]
    exclude: ["762", "773"]
  manual_override:
    lists: []
    exclude: ["762", "773"]

list_exclusion_rules: {}
secondary_sync: {}
archival:
  exempt_tags: []
  preservation_patterns: []
safety:
  test_contact_limit: 0
  run_mode: "prod"
  allow_archive: true
""")
    
    with pytest.raises(ValidationError, match="Duplicate list ID: 718"):
        load_config(str(config_path))


def test_config_batch_size_limit():
    """Batch size > 100 should be rejected (Mailchimp limit)."""
    from corev2.config.schema import SyncConfig
    
    with pytest.raises(ValidationError):
        SyncConfig(batch_size=101, ori_lists_field="ORI_LISTS")


def test_config_is_production_ready():
    """Test triple-lock safety gate validation."""
    from corev2.config.schema import SafetyConfig, RunMode
    
    # Not production ready (test mode)
    config = SafetyConfig(
        test_contact_limit=10,
        run_mode=RunMode.TEST,
        allow_archive=False
    )
    # Verify NOT production ready
    assert config.test_contact_limit != 0
    assert config.run_mode != RunMode.PROD
    assert config.allow_archive is False
    
    # Production ready (triple-lock satisfied)
    config_prod = SafetyConfig(
        test_contact_limit=0,
        run_mode=RunMode.PROD,
        allow_archive=True
    )
    # Verify all gates satisfied
    assert config_prod.test_contact_limit == 0
    assert config_prod.run_mode == RunMode.PROD
    assert config_prod.allow_archive is True


def test_defaults_yaml_validates():
    """Test that defaults.yaml itself is valid config."""
    import os
    from pathlib import Path
    
    # Set required env vars
    os.environ["HUBSPOT_API_KEY"] = "test-key-defaults"
    os.environ["MAILCHIMP_API_KEY"] = "test-key-defaults"
    os.environ["MAILCHIMP_AUDIENCE_ID"] = "test-audience-defaults"
    
    # Load defaults.yaml
    defaults_path = Path(__file__).parent.parent.parent / "config" / "defaults.yaml"
    assert defaults_path.exists(), f"defaults.yaml not found at {defaults_path}"
    
    config = load_config(str(defaults_path))
    
    # Verify structure
    assert config.hubspot.api_key.get_secret_value() == "test-key-defaults"
    assert config.mailchimp.audience_id == "test-audience-defaults"
    assert config.sync.batch_size == 100
    assert config.sync.ori_lists_field == "ORI_LISTS"
    
    # Verify INV-002: Compliance lists (762, 773) in all exclusions
    assert "762" in config.exclusion_matrix.general_marketing.exclude
    assert "773" in config.exclusion_matrix.general_marketing.exclude
    assert "762" in config.exclusion_matrix.special_campaigns.exclude
    assert "773" in config.exclusion_matrix.special_campaigns.exclude
    assert "762" in config.exclusion_matrix.manual_override.exclude
    assert "773" in config.exclusion_matrix.manual_override.exclude
    
    # Verify safety gates (test mode by default)
    assert config.safety.run_mode == RunMode.TEST
    assert config.safety.allow_apply is False
    assert config.safety.allow_archive is False
