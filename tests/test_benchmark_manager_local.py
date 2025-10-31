import os
import yaml
import time
from Interface.benchmark_manager import BenchmarkManager

class DummyExecutor:
    def __init__(self, *a, **k):
        self._running = False
    def run(self, command, **kwargs):
        self._running = True
        return "dummy-job"
    def stop(self):
        self._running = False
    def status(self):
        return "running" if self._running else "COMPLETED"

class SimpleMonitor:
    def __init__(self, *a, **k):
        self.started = False
    def start(self):
        self.started = True
    def collect(self):
        return {"ok": True}
    def stop(self):
        self.started = False

class SimpleLogger:
    def __init__(self, *a, **k):
        self.path = "/tmp/test.log"
    def log(self, msg, level="INFO"):
        pass
    def export(self):
        return self.path

def test_run_benchmark_local(tmp_path, monkeypatch):
    # recipe expected to be next to this test file: tests/recipe_local.yaml
    recipe_path = os.path.join(os.path.dirname(__file__), "TestRecipe.yaml")

    # monkeypatch manager factory methods to return simple objects
    monkeypatch.setattr(BenchmarkManager, "_create_executor", lambda self, spec: DummyExecutor())
    monkeypatch.setattr(BenchmarkManager, "_create_monitors_map", lambda self: {"mon1": SimpleMonitor()})
    monkeypatch.setattr(BenchmarkManager, "_create_loggers_map", lambda self: {"log1": SimpleLogger()})

    mgr = BenchmarkManager()
    mgr.load_recipe(recipe_path)
    mgr.run_benchmark()
    assert hasattr(mgr, "_services_objs")
    assert "svc1" in mgr._services_objs
    assert "cli1" in mgr._clients_objs