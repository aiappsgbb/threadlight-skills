"""Shared pytest configuration for threadlight-governed-actions.

Prepends the sibling ``scripts/`` directory to ``sys.path`` so tests can
``import contracts`` / ``import canonical`` directly, the same way the
scripts themselves are invoked (as standalone modules, not an installed
package).
"""
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
