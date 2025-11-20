import yaml
import os
import time
from time import sleep

import requests
from requests.exceptions import RequestException

from Core.service import Service
from Core.server import Server
from Core.client import Client
from Core.executors.slurm_executor import SlurmExecutor
from Core.executors.apptainer_executor import ApptainerExecutor
from Core.executors.process_executor import ProcessExecutor
from Core.executors.workload_executor import WorkloadExecutor
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

        valid_executors = ("process", "slurm", "apptainer", "workload")

        for s in self.recipe["services"]:
            if s["executor"]["type"] not in valid_executors:
                raise ValueError(f"Invalid executor type in service {s['id']}")

        for c in self.recipe["clients"]:
            if c["executor"]["type"] not in valid_executors:
                raise ValueError(f"Invalid executor type in client {c['id']}")

            if c["executor"]["type"] == "workload" and "type" not in c.get("workload", {}):
                raise ValueError(f"Client {c['id']} is missing workload.type for workload executor")

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

        if tp == "workload":
            return WorkloadExecutor()

        print(f"[WARN] Unknown executor type: {tp}")
        return None

    # ------------------------------------------------------------
    # Monitors Map
    # ------------------------------------------------------------
    def _create_monitors_map(self):
        monitors = {}
        for m in self.recipe["monitors"]:
            if m["type"] == "prometheus":
                save_dir = self.recipe["global"]["workspace"]
                os.makedirs(save_dir, exist_ok=True)
                scrape_targets = m.get("targets") or m.get("scrape_targets", [])
                readable_save = m.get("readable_save_as")
                if readable_save:
                    readable_path = readable_save if os.path.isabs(readable_save) else os.path.join(save_dir, readable_save)
                else:
                    readable_path = None
                monitors[m["id"]] = PrometheusMonitor(
                    scrape_targets=scrape_targets,
                    scrape_interval=m.get("scrape_interval", 5),
                    collect_interval=m.get("collect_interval", 10),
                    metrics_path=m.get("metrics_path", "/metrics"),
                    save_path=os.path.join(
                        save_dir,
                        m.get("save_as", "metrics.json")
                    ),
                    readable_save_path=readable_path
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
    def launch_service(self, svc_obj):
        svc_obj.start_service()

    def launch_client(self, cl_obj):
        cl_obj.start_workload()

    # ------------------------------------------------------------
    # Post Actions
    # ------------------------------------------------------------
    def execute_post_actions(self, actions):
        print("[INFO] Executing post-actions...")

        for act in actions:
            if act == "collect_metrics":
                seen_monitors = set()
                for obj in list(self._services_objs.values()) + list(self._clients_objs.values()):
                    monitor = getattr(obj, "monitor", None)
                    if not monitor or id(monitor) in seen_monitors:
                        continue
                    obj.collect_metrics()
                    seen_monitors.add(id(monitor))

            elif act == "stop_services":
                for obj in list(self._services_objs.values()) + list(self._clients_objs.values()):
                    obj.stop()

            else:
                print(f"[WARN] Unknown post action: {act}")

    # ------------------------------------------------------------
    # Run Benchmark (MAIN)
    # ------------------------------------------------------------
    def _wait_for_healthcheck(self, service_id: str, cfg: dict):
        if not cfg:
            return

        hc_type = cfg.get("type", "http")
        timeout = cfg.get("timeout", 60)
        interval = cfg.get("interval", 5)
        deadline = time.time() + timeout

        if hc_type != "http":
            print(f"[WARN] Unsupported healthcheck type '{hc_type}' for {service_id}")
            return

        url = cfg.get("url")
        expected = cfg.get("expect_status", 200)
        request_timeout = cfg.get("request_timeout", 3)

        if not url:
            print(f"[WARN] Healthcheck for {service_id} missing URL")
            return

        print(f"[INFO] Waiting for {service_id} to become ready at {url}...")

        while time.time() < deadline:
            try:
                response = requests.get(url, timeout=request_timeout)
                if response.status_code == expected:
                    print(f"[INFO] Service {service_id} is ready (HTTP {response.status_code}).")
                    return
                print(
                    f"[WARN] Healthcheck {service_id} returned {response.status_code},"
                    f" expected {expected}."
                )
            except RequestException as exc:
                print(f"[WARN] Healthcheck for {service_id} failed: {exc}")
            sleep(interval)

        raise TimeoutError(f"Healthcheck timed out for service {service_id} ({url})")

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

            self._services_objs[svc["id"]] = Server(
                id=svc["id"],
                role=svc["role"],
                executor=executor,
                monitor=mon,
                logger=log,
                command=svc.get("command")
            )

        for cl in self.recipe["clients"]:
            executor = self._create_executor(cl["executor"])
            mon = self._monitors.get(cl.get("monitor"))
            log = self._loggers.get(cl.get("logger"))

            self._clients_objs[cl["id"]] = Client(
                id=cl["id"],
                role=cl["type"],
                executor=executor,
                monitor=mon,
                logger=log,
                workload=cl.get("workload", {}),
                executor_type=cl["executor"].get("type", "process")
            )

        # Start services
        for svc in self.recipe["services"]:
            self.launch_service(self._services_objs[svc["id"]])
            health_cfg = svc.get("healthcheck")
            if health_cfg:
                try:
                    self._wait_for_healthcheck(svc["id"], health_cfg)
                except TimeoutError as exc:
                    print(f"[ERROR] {exc}")
                    self.execute_post_actions(["stop_services"])
                    raise

        # Start clients
        for cl in self.recipe["clients"]:
            self.launch_client(self._clients_objs[cl["id"]])

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
