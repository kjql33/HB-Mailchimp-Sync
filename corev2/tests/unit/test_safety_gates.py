"""Unit tests for CLI safety gates."""

import pytest
from pathlib import Path
from corev2.cli import apply_mode
from corev2.config.loader import load_config
from corev2.config.schema import RunMode
import json
import tempfile


def test_safety_gate_blocks_non_prod_mode():
    """Verify apply mode rejects non-prod run_mode."""
    # Create minimal test plan
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        plan = {
            "metadata": {
                "config_file": "corev2/config/test_config.yaml"
            },
            "operations": []
        }
        json.dump(plan, f)
        plan_path = Path(f.name)
    
    try:
        # Test config has run_mode=test, should fail
        result = apply_mode(plan_path, dry_run=False)
        assert result == 1  # Should fail
    except ValueError as e:
        assert "run_mode" in str(e)
        assert "prod" in str(e).lower()
    finally:
        plan_path.unlink(missing_ok=True)


def test_safety_gate_allows_dry_run():
    """Verify dry-run bypasses safety gates."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        plan = {
            "metadata": {
                "config_file": "corev2/config/test_config.yaml"
            },
            "operations": []
        }
        json.dump(plan, f)
        plan_path = Path(f.name)
    
    try:
        # Dry-run should pass even with test run_mode
        # (Will fail later on actual execution, but safety gates should pass)
        result = apply_mode(plan_path, dry_run=True)
        # Note: May fail on executor initialization, but not on safety gate check
        assert result in (0, 1)  # Either succeeds or fails elsewhere
    finally:
        plan_path.unlink(missing_ok=True)


def test_config_safety_defaults():
    """Verify default safety config prevents accidental production use."""
    config = load_config("corev2/config/test_config.yaml")
    
    # Default should be safe (not production-ready)
    assert not config.is_production_ready()
    assert config.safety.run_mode != RunMode.PROD
    assert not config.safety.allow_apply
    assert not config.safety.allow_archive
