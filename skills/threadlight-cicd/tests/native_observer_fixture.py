"""Reuse the canonical observer's real subprocess fixture across CI entry points."""
import importlib.util
from pathlib import Path


class NativeExecutionFixture:
    def __init__(self, tmp_path, observer):
        path = Path(__file__).resolve().parents[2] / "threadlight-agentops/tests/test_runtime_integration.py"
        spec = importlib.util.spec_from_file_location("_canonical_observer_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.case = module.RuntimeIntegrationTests()
        self.case.setUp()
        assert self.case.observer is observer
        self.fixture = self.case.fixture
        self.repo = self.case.repo
        self.executable = self.case.executable

    def approve(self, operation):
        return self.case.approve(operation)

    def close(self):
        self.case.doCleanups()
