import yaml
import os
import subprocess
import time
from time import sleep

from Core.service import Service
from Core.executors.slurm_executor import SlurmExecutor
from Core.executors.apptainer_executor import ApptainerExecutor
from Core.executors.process_executor import ProcessExecutor 
from Core.monitors.prometheus_monitor import PrometheusMonitor
from Core.loggers.file_logger import FileLogger


# ------------------------------------------------------------
# Utility
# ------------------------------------------------------------
def expand_path(path: str, recipe: dict) -> str:
    if not path:
        return path
    if "${global.workspace}" in path:
        path = path.replace("${global.workspace}", recipe["global"]["workspace"])
    return os.path.expandvars(os.path.expanduser(path))


# ------------------------------------------------------------
# Benchmark Manager
# ------------------------------------------------------------
class BenchmarkManager:
    def __init__(self):
        self.recipe = None
        self.recipe_path = None

    # ------------------------------------------------------------
    # Load & Validation
    # ------------------------------------------------------------
    def override_workspace(self, new_workspace: str):
        if not self.recipe:
            print("[ERROR] No recipe loaded — cannot override workspace.")
            return

        expanded = os.path.expandvars(os.path.expanduser(new_workspace))
        os.makedirs(expanded, exist_ok=True)
        self.recipe["global"]["workspace"] = expanded
        print(f"[INFO] Workspace overridden -> {expanded}")

    def load_recipe(self, path: str, override_workspace: str = None):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Recipe file {path} does not exist.")

        with open(path, 'r') as f:
            self.recipe = yaml.safe_load(f)

        # Expand env vars in global.*
        for key, value in self.recipe.get("global", {}).items():
            if isinstance(value, str):
                self.recipe["global"][key] = os.path.expandvars(os.path.expanduser(value))

        # Workspace setup
        workspace = override_workspace or self.recipe["global"].get("workspace")
        if not workspace:
            raise ValueError("global.workspace missing.")

        if not os.path.exists(workspace):
            try:
                os.makedirs(workspace, exist_ok=True)
                print(f"[INFO] Workspace created at {workspace}")
            except PermissionError:
                print("[WARN] Cannot create workspace, using /output")
                workspace = "/output"

        self.recipe["global"]["workspace"] = workspace
        print(f"[INFO] Workspace set to: {workspace}")
        print(f"[INFO] Recipe loaded from {path}")

        self.validate_recipe()

    # ------------------------------------------------------------
    # Validation (minimal but consistent)
    # ------------------------------------------------------------
    def validate_recipe(self):
        req_sections = [
            "meta", "global", "services", "clients",
            "monitors", "loggers", "execution",
            "reporting", "notifications", "cleanup"
        ]

        for s in req_sections:
            if s not in self.recipe:
                raise ValueError(f"Missing required section: {s}")

        # Global
        if "workspace" not in self.recipe["global"]:
            raise ValueError("global.workspace missing")

        # Services
        for svc in self.recipe["services"]:
            if svc["executor"]["type"] not in ("process", "slurm", "apptainer"):
                raise ValueError("Unknown executor type in service")

        # Clients
        for cl in self.recipe["clients"]:
            if cl["executor"]["type"] not in ("process", "slurm", "apptainer"):
                raise ValueError("Unknown executor type in client")

        print("[INFO] Recipe validation passed (HPC-safe).")

    # ------------------------------------------------------------
    # Executors
    # ------------------------------------------------------------
    def _create_executor(self, spec: dict):
        if not spec or "type" not in spec:
            return None

        ex_type = spec["type"]

        if ex_type == "slurm":
            sl = spec.get("slurm", {})
            return SlurmExecutor(
                job_name=sl.get("job_name", "job"),
                nodes=sl.get("nodes", 1),
                ntasks=sl.get("ntasks", 1)
            )

        if ex_type == "apptainer":
            return ApptainerExecutor(image=spec.get("image", ""))

        if ex_type == "process":
            return ProcessExecutor()

        print(f"[WARN] Unknown executor type {ex_type}")
        return None

    # ------------------------------------------------------------
    # Monitors
    # ------------------------------------------------------------
    def _create_monitors_map(self):
        monitors_map = {}
        for m in self.recipe.get("monitors", []):
            if m["type"] == "prometheus":
                monitors_map[m["id"]] = PrometheusMonitor(
                    gateway_url=m.get("gateway_url", "http://localhost:9091/metrics"),
                    collect_interval=m.get("collect_interval", 10),
                    save_path=m.get("save_as", "metrics.json")
                )
            else:
                print(f"[WARN] Unknown monitor type: {m['type']}")
        return monitors_map

    # ------------------------------------------------------------
    # Loggers
    # ------------------------------------------------------------
    def _create_loggers_map(self):
        loggers_map = {}
        for l in self.recipe.get("loggers", []):
            if l["type"] == "file":
                raw = l.get("paths", [self.recipe["global"]["workspace"]])[0]
                path = expand_path(raw, self.recipe)
                os.makedirs(path, exist_ok=True)
                loggers_map[l["id"]] = FileLogger(
                    log_dir=path,
                    file_name=l.get("file_name", "log.json"),
                    fmt=l.get("format", "json")
                )
                print(f"[INFO] Logger '{l['id']}' ready -> {path}")
        return loggers_map

    # ------------------------------------------------------------
    # Launchers
    # ------------------------------------------------------------
    def launch_service(self, svc_obj: Service, cmd_spec):
        cmd = cmd_spec if isinstance(cmd_spec, str) else " ".join(cmd_spec)
        print(f"[INFO] Launching service {svc_obj.id} -> {cmd}")
        svc_obj.start(cmd)

    def launch_client(self, client_obj: Service, cmd_spec):
        cmd = cmd_spec if isinstance(cmd_spec, str) else " ".join(cmd_spec)
        print(f"[INFO] Launching client {client_obj.id} -> {cmd}")
        client_obj.start(cmd)

    # ------------------------------------------------------------
    # Monitoring / Logging
    # ------------------------------------------------------------
    def start_monitors(self, monitors_map):
        for mid, m in monitors_map.items():
            try:
                m.start()
            except Exception as e:
                print(f"[WARN] Cannot start monitor {mid}: {e}")

    def start_loggers(self, loggers_map):
        for lid, lg in loggers_map.items():
            lg.log("Logger ready", "DEBUG")

    # ------------------------------------------------------------
    # Post Actions
    # ------------------------------------------------------------
    def execute_post_actions(self, actions):
        print("[INFO] Executing post-actions...")
        for act in actions:
            if act == "collect_metrics":
                for sid, s in self._services_objs.items():
                    s.collect_metrics()
                for cid, c in self._clients_objs.items():
                    c.collect_metrics()

            elif act == "stop_services":
                for s in self._services_objs.values():
                    s.stop()
                for c in self._clients_objs.values():
                    c.stop()

            else:
                print(f"[WARN] Unknown post action: {act}")

    # ------------------------------------------------------------
    # Main benchmark loop
    # ------------------------------------------------------------
    def run_benchmark(self):
        print("[INFO] === Starting Benchmark ===")

        monitors_map = self._create_monitors_map()
        loggers_map = self._create_loggers_map()
        self.start_monitors(monitors_map)
        self.start_loggers(loggers_map)

        # Build service objects
        self._services_objs = {}
        for svc in self.recipe["services"]:
            executor = self._create_executor(svc["executor"])
            mon = monitors_map.get(svc.get("monitor"))
            log = loggers_map.get(svc.get("logger"))
            self._services_objs[svc["id"]] = Service(
                id=svc["id"], role=svc["role"],
                executor=executor, monitor=mon, logger=log
            )

        # Build client objects
        self._clients_objs = {}
        for cl in self.recipe["clients"]:
            executor = self._create_executor(cl["executor"])
            mon = monitors_map.get(cl.get("monitor"))
            log = loggers_map.get(cl.get("logger"))
            self._clients_objs[cl["id"]] = Service(
                id=cl["id"], role=cl["type"],
                executor=executor, monitor=mon, logger=log
            )

        # Start services
        for svc in self.recipe["services"]:
            self.launch_service(
                self._services_objs[svc["id"]],
                svc.get("command", "")
            )

        # Start clients
        for cl in self.recipe["clients"]:
            self.launch_client(
                self._clients_objs[cl["id"]],
                cl.get("workload", {}).get("cmd", "")
            )

        # Runtime loop
        duration = self.recipe["execution"]["duration"]
        poll = self.recipe["execution"].get("poll_interval", 5)

        start = time.time()
        while time.time() - start < duration:
            all_done = True
            for cid, c in self._clients_objs.items():
                st = c.status()
                if st in ("running", None, "UNKNOWN", "PENDING"):
                    all_done = False
                    break
            if all_done:
                print("[INFO] All clients finished early.")
                break
            time.sleep(poll)

        # Post actions
        self.execute_post_actions(self.recipe["execution"].get("post_actions", []))

        print("[INFO] === Benchmark Completed ===")
