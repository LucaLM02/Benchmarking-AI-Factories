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
# Benchmark Manager (HPC-safe, multi-node, Slurm-ready)
# ------------------------------------------------------------
class BenchmarkManager:
    def __init__(self):
        self.recipe = None
        self.recipe_path = None
        self._services_objs = {}
        self._clients_objs = {}
        self._monitors = {}
        self._loggers = {}

    # ------------------------------------------------------------
    # Load Recipe
    # ------------------------------------------------------------
    def override_workspace(self, new_workspace: str):
        expanded = os.path.expandvars(os.path.expanduser(new_workspace))
        os.makedirs(expanded, exist_ok=True)
        self.recipe["global"]["workspace"] = expanded
        print(f"[INFO] Workspace overridden -> {expanded}")

    def load_recipe(self, path: str, override_workspace: str = None):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Recipe file {path} does not exist.")

        with open(path, 'r') as f:
            self.recipe = yaml.safe_load(f)

        for key, value in self.recipe.get("global", {}).items():
            if isinstance(value, str):
                self.recipe["global"][key] = os.path.expandvars(os.path.expanduser(value))

        workspace = override_workspace or self.recipe["global"].get("workspace")
        if not workspace:
            raise ValueError("global.workspace missing")

        if not os.path.exists(workspace):
            try:
                os.makedirs(workspace, exist_ok=True)
                print(f"[INFO] Workspace created at {workspace}")
            except PermissionError:
                print("[WARN] Cannot create workspace, using /output")
                workspace = "/output"

        self.recipe["global"]["workspace"] = workspace
        print(f"[INFO] Workspace set to: {workspace}")
        print(f"[INFO] Recipe loaded from: {path}")

        self.validate_recipe()

    # ------------------------------------------------------------
    # Validate Recipe
    # ------------------------------------------------------------
    def validate_recipe(self):
        required = [
            "meta", "global", "services", "clients",
            "monitors", "loggers", "execution",
            "reporting", "notifications", "cleanup"
        ]
        for sec in required:
            if sec not in self.recipe:
                raise ValueError(f"Missing required section: {sec}")

        for s in self.recipe["services"]:
            if s["executor"]["type"] not in ("process", "slurm", "apptainer"):
                raise ValueError(f"Invalid executor type in service {s['id']}")

        for c in self.recipe["clients"]:
            if c["executor"]["type"] not in ("process", "slurm", "apptainer"):
                raise ValueError(f"Invalid executor type in client {c['id']}")

        print("[INFO] Recipe validation passed (HPC-safe).")

    # ------------------------------------------------------------
    # Executor Factory
    # ------------------------------------------------------------
    def _create_executor(self, spec: dict):
        if not spec or "type" not in spec:
            return None

        tp = spec["type"]

        if tp == "slurm":
            s = spec.get("slurm", {})
            return SlurmExecutor(
                job_name=s.get("job_name", "job"),
                nodes=s.get("nodes", 1),
                ntasks=s.get("ntasks", 1),
                gpus_per_node=s.get("gpus_per_node", 1),
                cpus_per_gpu=s.get("cpus_per_gpu", 4),
                mem=s.get("mem", "16G"),
                partition=s.get("partition", "gpu"),
                time=s.get("time", "00:10:00"),
                account=s.get("account", None),
                qos=s.get("qos", "default"),
                image=spec.get("image", None)
            )

        if tp == "apptainer":
            return ApptainerExecutor(image=spec.get("image"))

        if tp == "process":
            return ProcessExecutor()

        print(f"[WARN] Unknown executor type: {tp}")
        return None

    # ------------------------------------------------------------
    # Monitors Map
    # ------------------------------------------------------------
    def _create_monitors_map(self):
        monitors = {}
        for m in self.recipe["monitors"]:
            if m["type"] == "prometheus":
                monitors[m["id"]] = PrometheusMonitor(
                    scrape_targets=m.get("targets", []),
                    scrape_interval=m.get("scrape_interval", 5),
                    collect_interval=m.get("collect_interval", 10),
                    save_path=os.path.join(
                        self.recipe["global"]["workspace"],
                        m.get("save_as", "metrics.json")
                    )
                )
            else:
                print(f"[WARN] Unknown monitor type: {m['type']}")

        return monitors

    # ------------------------------------------------------------
    # Loggers Map
    # ------------------------------------------------------------
    def _create_loggers_map(self):
        loggers = {}

        for l in self.recipe["loggers"]:
            raw = l.get("paths", [self.recipe["global"]["workspace"]])[0]
            path = expand_path(raw, self.recipe)
            os.makedirs(path, exist_ok=True)

            loggers[l["id"]] = FileLogger(
                log_dir=path,
                file_name=l.get("file_name", "log.json"),
                fmt=l.get("format", "json")
            )
            print(f"[INFO] Logger '{l['id']}' initialized -> {path}/{l.get('file_name', 'log.json')}")

        return loggers

    # ------------------------------------------------------------
    # Launch service/client
    # ------------------------------------------------------------
    def launch_service(self, svc_obj, cmd):
        print(f"[INFO] Launching service {svc_obj.id} -> {cmd}")
        svc_obj.start(cmd)

    def launch_client(self, cl_obj, cmd):
        print(f"[INFO] Launching client {cl_obj.id} -> {cmd}")
        cl_obj.start(cmd)

    # ------------------------------------------------------------
    # Post Actions
    # ------------------------------------------------------------
    def execute_post_actions(self, actions):
        print("[INFO] Executing post-actions...")

        for act in actions:
            if act == "collect_metrics":
                for obj in list(self._services_objs.values()) + list(self._clients_objs.values()):
                    obj.collect_metrics()

            elif act == "stop_services":
                for obj in list(self._services_objs.values()) + list(self._clients_objs.values()):
                    obj.stop()

            else:
                print(f"[WARN] Unknown post action: {act}")

    # ------------------------------------------------------------
    # Run Benchmark (MAIN)
    # ------------------------------------------------------------
    def run_benchmark(self):
        print("[INFO] === Starting Benchmark ===")

        # Init maps
        self._monitors = self._create_monitors_map()
        self._loggers = self._create_loggers_map()

        for m in self._monitors.values():
            m.start()

        for lg in self._loggers.values():
            lg.log("Logger initialized", "DEBUG")

        # Create services
        for svc in self.recipe["services"]:
            executor = self._create_executor(svc["executor"])
            mon = self._monitors.get(svc.get("monitor"))
            log = self._loggers.get(svc.get("logger"))

            self._services_objs[svc["id"]] = Service(
                id=svc["id"],
                role=svc["role"],
                executor=executor,
                monitor=mon,
                logger=log
            )

        for cl in self.recipe["clients"]:
            executor = self._create_executor(cl["executor"])
            mon = self._monitors.get(cl.get("monitor"))
            log = self._loggers.get(cl.get("logger"))

            self._clients_objs[cl["id"]] = Service(
                id=cl["id"],
                role=cl["type"],
                executor=executor,
                monitor=mon,
                logger=log
            )

        # Start services
        for svc in self.recipe["services"]:
            self.launch_service(self._services_objs[svc["id"]], svc["command"])

        # Start clients
        for cl in self.recipe["clients"]:
            cmd = cl.get("workload", {}).get("cmd")
            self.launch_client(self._clients_objs[cl["id"]], cmd)

        # Main runtime loop
        duration = self.recipe["execution"]["duration"]
        poll = self.recipe["execution"].get("poll_interval", 5)

        print(f"[INFO] Running benchmark for {duration} seconds...")

        start = time.time()
        while time.time() - start < duration:
            all_done = True
            for cl in self._clients_objs.values():
                if cl.status() in ("running", None, "UNKNOWN", "PENDING"):
                    all_done = False
                    break
            if all_done:
                print("[INFO] All clients finished early.")
                break
            sleep(poll)

        # Post actions
        self.execute_post_actions(self.recipe["execution"].get("post_actions", []))

        print("[INFO] === Benchmark Completed ===")
