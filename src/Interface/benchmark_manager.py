import yaml
import os
import subprocess
import time
from time import sleep

from Core.service import Service
from Core.executors.slurm_executor import SlurmExecutor
from Core.executors.apptainer_executor import ApptainerExecutor
from Core.executors.apptainer_executor import ProcessExecutor 
from Core.monitors.prometheus_monitor import PrometheusMonitor
from Core.loggers.file_logger import FileLogger

def expand_path(path: str, recipe: dict) -> str:
    """Expand ${global.workspace} and environment variables in paths."""
    if not path:
        return path
    if "${global.workspace}" in path:
        path = path.replace("${global.workspace}", recipe["global"]["workspace"])
    path = os.path.expandvars(os.path.expanduser(path))
    return path

class BenchmarkManager:
    def __init__(self):
        self.recipe = None
        self.recipe_path = None

    def override_workspace(self, new_workspace: str):
        """Override the workspace path defined in the recipe."""
        import os
        if not self.recipe:
            print("[ERROR] No recipe loaded — cannot override workspace.")
            return

        expanded = os.path.expandvars(os.path.expanduser(new_workspace))
        os.makedirs(expanded, exist_ok=True)

        self.recipe["global"]["workspace"] = expanded
        self._workspace_overridden = True
        print(f"[INFO] Workspace overridden -> {expanded}")

    def load_recipe(self, path: str, override_workspace: str = None):
        """Load and parse a YAML recipe file and set up the workspace."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Recipe file {path} does not exist.")
        
        with open(path, 'r') as f:
            self.recipe = yaml.safe_load(f)

        # Expand environment variables
        for key, value in self.recipe.get("global", {}).items():
            if isinstance(value, str):
                self.recipe["global"][key] = os.path.expandvars(os.path.expanduser(value))

        # --- Handle workspace logic ---
        workspace = override_workspace or self.recipe["global"].get("workspace", None)
        
        if workspace is None:
            raise ValueError("global.workspace is required or must be provided via CLI.")

        # Use existing workspace if already created (e.g., by .sh script)
        if os.path.exists(workspace):
            print(f"[INFO] Using existing workspace: {workspace}")
        else:
            try:
                os.makedirs(workspace, exist_ok=True)
                print(f"[INFO] Workspace created: {workspace}")
            except PermissionError:
                print(f"[WARN] Cannot create workspace at {workspace}. Falling back to /output.")
                workspace = "/output"

        self.recipe["global"]["workspace"] = workspace
        print(f"[INFO] Workspace set to: {workspace}")

        self.recipe_path = path
        print(f"[INFO] Recipe loaded from {path}")
        self.validate_recipe()

    def validate_recipe(self):
        """Rigorous validation of the loaded recipe. Raises ValueError with a clear message on first problem found."""
        if self.recipe is None:
            raise ValueError("No recipe loaded.")

        if not isinstance(self.recipe, dict):
            raise ValueError("Recipe must be a YAML mapping (dictionary) at top level.")

        required_sections = ["meta", "global", "services", "clients", "monitors", "loggers", "execution", "reporting", "notifications", "cleanup"]
        for section in required_sections:
            if section not in self.recipe:
                raise ValueError(f"Missing required top-level section '{section}' in recipe.")

        # meta
        meta = self.recipe.get("meta", {})
        if not isinstance(meta, dict):
            raise ValueError("Section 'meta' must be a mapping.")
        for key in ("name", "description", "author", "created_at"):
            if key not in meta or not meta.get(key):
                raise ValueError(f"meta.{key} is required and must be non-empty.")

        # global
        global_s = self.recipe.get("global", {})
        if not isinstance(global_s, dict):
            raise ValueError("Section 'global' must be a mapping.")
        if "workspace" not in global_s or not isinstance(global_s["workspace"], str):
            raise ValueError("global.workspace is required and must be a string.")
        if "timeout" in global_s and not isinstance(global_s["timeout"], int):
            raise ValueError("global.timeout, if present, must be an integer (seconds).")

        # services
        services = self.recipe.get("services", [])
        if not isinstance(services, list):
            raise ValueError("Section 'services' must be a list.")
        if len(services) == 0:
            raise ValueError("At least one service must be defined in 'services'.")
        ids = set()
        for i, svc in enumerate(services):
            if not isinstance(svc, dict):
                raise ValueError(f"services[{i}] must be a mapping.")
            for key in ("id", "role", "executor"):
                if key not in svc:
                    raise ValueError(f"services[{i}] missing required field '{key}'.")
            sid = svc["id"]
            if sid in ids:
                raise ValueError(f"Duplicate service id '{sid}'.")
            ids.add(sid)
            executor = svc["executor"]
            if not isinstance(executor, dict):
                raise ValueError(f"services[{i}].executor must be a mapping.")
            if "type" not in executor:
                raise ValueError(f"services[{i}].executor.type is required.")
            ex_type = executor.get("type")
            if ex_type == "apptainer":
                if "image" not in executor or not executor["image"]:
                    raise ValueError(f"services[{i}] executor.type=apptainer requires 'image'.")
            elif ex_type == "slurm":
                if "slurm" not in executor or not isinstance(executor["slurm"], dict):
                    raise ValueError(f"services[{i}] executor.type=slurm requires a 'slurm' mapping.")
            else:
                raise ValueError(f"services[{i}] executor.type must be 'apptainer' or 'slurm', got '{ex_type}'.")

        # clients
        clients = self.recipe.get("clients", [])
        if not isinstance(clients, list):
            raise ValueError("Section 'clients' must be a list.")
        if len(clients) == 0:
            raise ValueError("At least one client must be defined in 'clients'.")
        client_ids = set()
        for i, cl in enumerate(clients):
            if not isinstance(cl, dict):
                raise ValueError(f"clients[{i}] must be a mapping.")
            for key in ("id", "type", "executor"):
                if key not in cl:
                    raise ValueError(f"clients[{i}] missing required field '{key}'.")
            cid = cl["id"]
            if cid in client_ids:
                raise ValueError(f"Duplicate client id '{cid}'.")
            client_ids.add(cid)
            execr = cl["executor"]
            if not isinstance(execr, dict):
                raise ValueError(f"clients[{i}].executor must be a mapping.")
            if "type" not in execr:
                raise ValueError(f"clients[{i}].executor.type is required.")
            if execr["type"] == "slurm" and "slurm" in execr:
                sl = execr["slurm"]
                if not isinstance(sl, dict):
                    raise ValueError(f"clients[{i}].executor.slurm must be a mapping.")
            # workload optional but if present must be mapping
            if "workload" in cl and not isinstance(cl["workload"], dict):
                raise ValueError(f"clients[{i}].workload must be a mapping if present.")

        # monitors
        monitors = self.recipe.get("monitors", [])
        if not isinstance(monitors, list):
            raise ValueError("Section 'monitors' must be a list.")
        for i, m in enumerate(monitors):
            if not isinstance(m, dict):
                raise ValueError(f"monitors[{i}] must be a mapping.")
            for key in ("id", "type"):
                if key not in m:
                    raise ValueError(f"monitors[{i}] missing required field '{key}'.")
            if "config" in m and not isinstance(m["config"], dict):
                raise ValueError(f"monitors[{i}].config must be a mapping if present.")

        # loggers
        loggers = self.recipe.get("loggers", [])
        if not isinstance(loggers, list):
            raise ValueError("Section 'loggers' must be a list.")
        for i, l in enumerate(loggers):
            if not isinstance(l, dict):
                raise ValueError(f"loggers[{i}] must be a mapping.")
            for key in ("id", "type"):
                if key not in l:
                    raise ValueError(f"loggers[{i}] missing required field '{key}'.")
            if "paths" in l and not isinstance(l["paths"], list):
                raise ValueError(f"loggers[{i}].paths must be a list if present.")

        # execution
        execution = self.recipe.get("execution", {})
        if not isinstance(execution, dict):
            raise ValueError("Section 'execution' must be a mapping.")
        if "duration" not in execution or not isinstance(execution["duration"], int):
            raise ValueError("execution.duration is required and must be an integer (seconds).")
        if "warmup" in execution and not isinstance(execution["warmup"], int):
            raise ValueError("execution.warmup must be an integer if present.")
        if "post_actions" in execution and not isinstance(execution["post_actions"], list):
            raise ValueError("execution.post_actions must be a list if present.")

        # reporting
        reporting = self.recipe.get("reporting", {})
        if not isinstance(reporting, dict):
            raise ValueError("Section 'reporting' must be a mapping.")
        outputs = reporting.get("outputs", [])
        if not isinstance(outputs, list):
            raise ValueError("reporting.outputs must be a list.")
        for i, o in enumerate(outputs):
            if not isinstance(o, dict) or "type" not in o or "file" not in o:
                raise ValueError(f"reporting.outputs[{i}] must be a mapping containing 'type' and 'file'.")

        # notifications
        notifications = self.recipe.get("notifications", {})
        if notifications is not None and not isinstance(notifications, dict):
            raise ValueError("Section 'notifications' must be a mapping if present.")
        if "webhook" in notifications and not isinstance(notifications["webhook"], str):
            raise ValueError("notifications.webhook must be a string URL if present.")

        # cleanup
        cleanup = self.recipe.get("cleanup", {})
        if not isinstance(cleanup, dict):
            raise ValueError("Section 'cleanup' must be a mapping.")
        for key in ("remove_containers", "preserve_artifacts"):
            if key in cleanup and not isinstance(cleanup[key], bool):
                raise ValueError(f"cleanup.{key} must be a boolean if present.")

        print("[INFO] Recipe validation passed (rigorous checks).")

    def show_summary(self):
        """Print a detailed, user-oriented summary/check of all recipe sections in English."""
        if self.recipe is None:
            print("[ERROR] No recipe loaded.")
            return

        print("\n=== Recipe Full Summary / Validation Check ===\n")

        # Meta
        meta = self.recipe.get("meta", {})
        print("META:")
        print(f"  Name        : {meta.get('name','<missing>')}")
        print(f"  Description : {meta.get('description','<missing>')}")
        print(f"  Author      : {meta.get('author','<missing>')}")
        print(f"  Created at  : {meta.get('created_at','<missing>')}")
        print(f"  Commit      : {meta.get('commit','(optional)')}")
        print(f"  Image hash  : {meta.get('image_hash','(optional)')}")
        print("  -> Ensure metadata correctly identifies the benchmark and provenance.\n")

        # Global
        global_s = self.recipe.get("global", {})
        print("GLOBAL:")
        print(f"  Workspace   : {global_s.get('workspace','<missing>')}")
        print(f"  Timeout     : {global_s.get('timeout','(optional)')}")
        env = global_s.get("environment", [])
        print(f"  Environment : {env if env else '(none)'}")
        print("  -> Verify workspace path, timeout and environment variables.\n")

        # Services
        services = self.recipe.get("services", [])
        print("SERVICES:")
        print(f"  Total services defined: {len(services)}")
        for s in services:
            print(f"  - id: {s.get('id','<missing>')}")
            print(f"    role : {s.get('role','<missing>')}")
            ex = s.get('executor', {})
            print(f"    executor.type : {ex.get('type','<missing>')}")
            if ex.get('type') == 'apptainer':
                print(f"    executor.image: {ex.get('image','<missing>')}")
            elif ex.get('type') == 'slurm':
                print(f"    executor.slurm : {ex.get('slurm','<missing>')}")
            print(f"    healthcheck    : {s.get('healthcheck','(optional)')}")
            print(f"    monitor/logger : {s.get('monitor','(none)')} / {s.get('logger','(none)')}")
        print("  -> Check each service id, executor configuration and healthchecks.\n")

        # Clients
        clients = self.recipe.get("clients", [])
        print("CLIENTS:")
        print(f"  Total clients defined: {len(clients)}")
        for c in clients:
            print(f"  - id: {c.get('id','<missing>')}")
            print(f"    type : {c.get('type','<missing>')}")
            ex = c.get('executor', {})
            print(f"    executor.type : {ex.get('type','<missing>')}")
            if ex.get('type') == 'slurm':
                print(f"    executor.slurm : {ex.get('slurm','<missing>')}")
            print(f"    workload       : {c.get('workload','(optional)')}")
            print(f"    retries        : {c.get('retries','(optional)')}")
        print("  -> Ensure clients target services correctly and executor resources are adequate.\n")

        # Monitors
        monitors = self.recipe.get("monitors", [])
        print("MONITORS:")
        print(f"  Total monitors defined: {len(monitors)}")
        for m in monitors:
            print(f"  - id: {m.get('id','<missing>')}  type: {m.get('type','<missing>')}")
            print(f"    config: {m.get('config','(optional)')}")
            print(f"    collect_interval: {m.get('collect_interval','(optional)')}")
            print(f"    save_as: {m.get('save_as','(optional)')}")
        print("  -> Confirm scrape targets and collection intervals.\n")

        # Loggers
        loggers = self.recipe.get("loggers", [])
        print("LOGGERS:")
        print(f"  Total loggers defined: {len(loggers)}")
        for l in loggers:
            print(f"  - id: {l.get('id','<missing>')}  type: {l.get('type','<missing>')}")
            print(f"    paths : {l.get('paths','(optional)')}")
            print(f"    format: {l.get('format','(optional)')}")
        print("  -> Verify logging paths, formats and retention expectations.\n")

        # Execution
        execution = self.recipe.get("execution", {})
        print("EXECUTION:")
        print(f"  Warmup seconds : {execution.get('warmup','(optional)')}")
        print(f"  Duration sec   : {execution.get('duration','<missing>')}")
        print(f"  Replicas       : {execution.get('replicas','(optional)')}")
        print(f"  Post actions   : {execution.get('post_actions','(optional)')}")
        print("  -> Make sure duration/warmup suit the workload and post_actions are correct.\n")

        # Reporting
        reporting = self.recipe.get("reporting", {})
        print("REPORTING:")
        outputs = reporting.get("outputs", [])
        print(f"  Outputs defined: {len(outputs)}")
        for o in outputs:
            print(f"  - type: {o.get('type','<missing>')}  file: {o.get('file','<missing>')}")
        print("  -> Check output formats and target filenames.\n")

        # Notifications
        notifications = self.recipe.get("notifications", {})
        print("NOTIFICATIONS:")
        print(f"  Settings: {notifications if notifications else '(none)'}")
        print("  -> Validate webhook URLs or other notification channels.\n")

        # Cleanup
        cleanup = self.recipe.get("cleanup", {})
        print("CLEANUP:")
        print(f"  remove_containers   : {cleanup.get('remove_containers','(optional)')}")
        print(f"  preserve_artifacts  : {cleanup.get('preserve_artifacts','(optional)')}")
        print("  -> Confirm cleanup policy to avoid losing important artifacts.\n")

        print("=== End of Summary ===\n")

    # ------------------------
    # Benchmark execution
    # ------------------------

    def _create_executor(self, spec: dict):
        """Factory for executors. Supports 'slurm' and 'apptainer' (simple wrapper)."""
        if not spec or "type" not in spec:
            return None

        ex_type = spec["type"]
        if ex_type == "slurm":
            sl = spec.get("slurm", {}) or {}
            job_name = sl.get("job_name", spec.get("id", "job"))
            nodes = sl.get("nodes", 1)
            ntasks = sl.get("ntasks", 1)
            return SlurmExecutor(job_name=job_name, nodes=nodes, ntasks=ntasks)

        if ex_type == "apptainer":
            image = spec.get("image", "")
            return ApptainerExecutor(image=image)
        
        if ex_type == "process":
            return ProcessExecutor()

        print(f"[WARN] Unknown executor.type '{ex_type}', using no-op executor.")
        return None

    def _create_monitors_map(self):
        """Instantiate monitor objects declared in recipe and return dict by id."""
        monitors_map = {}
        for m in self.recipe.get("monitors", []):
            mid = m.get("id")
            mtype = m.get("type")
            if mtype == "prometheus":
                targets = m.get("targets", [])
                scrape_interval = m.get("scrape_interval", 5)
                collect_interval = m.get("collect_interval", 10)
                save_as = m.get("save_as", f"{mid}_metrics.json")
                monitors_map[mid] = PrometheusMonitor(scrape_targets=targets,
                                                      scrape_interval=scrape_interval,
                                                      collect_interval=collect_interval,
                                                      save_path=save_as)
            else:
                print(f"[WARN] Monitor type '{mtype}' not implemented for id '{mid}'.")
        return monitors_map

    def _create_loggers_map(self):
        """Instantiate logger objects declared in recipe and return dict by id."""
        loggers_map = {}
        for logger_cfg in self.recipe.get("loggers", []):
            lid = logger_cfg.get("id")
            ltype = logger_cfg.get("type", "").lower()

            if ltype == "file":
                # Get path pattern (use first entry if list)
                paths = logger_cfg.get("paths", [])
                if isinstance(paths, list) and len(paths) > 0:
                    raw_path = paths[0]
                else:
                    raw_path = self.recipe.get("global", {}).get("workspace", "/tmp")

                # Expand ${global.workspace} and env vars
                path = expand_path(raw_path, self.recipe)
                os.makedirs(path, exist_ok=True)

                file_name = logger_cfg.get("file_name", f"{lid}.log")
                fmt = logger_cfg.get("format", "json")

                loggers_map[lid] = FileLogger(log_dir=path, file_name=file_name, fmt=fmt)
                print(f"[INFO] Logger '{lid}' initialized -> {path}/{file_name}")

            else:
                print(f"[WARN] Logger type '{ltype}' not implemented for id '{lid}'.")

        return loggers_map

    def launch_service(self, svc_obj: Service, cmd_spec):
        """Start a Service instance using provided command specification."""
        if svc_obj is None:
            print("[ERROR] No service object provided to launch_service.")
            return
        # command can be a list or string; Service.start expects a command string
        if isinstance(cmd_spec, list):
            cmd = " ".join(cmd_spec)
        else:
            cmd = str(cmd_spec)
        print(f"[INFO] Launching service {svc_obj.id} with command: {cmd}")
        try:
            svc_obj.start(cmd)
        except Exception as e:
            print(f"[ERROR] Failed to start service {svc_obj.id}: {e}")

    def launch_client(self, client_obj: Service, cmd_spec):
        """Start a client (modeled as Service) with its workload/command."""
        if client_obj is None:
            print("[ERROR] No client object provided to launch_client.")
            return
        if isinstance(cmd_spec, list):
            cmd = " ".join(cmd_spec)
        else:
            cmd = str(cmd_spec)
        print(f"[INFO] Launching client {client_obj.id} with command: {cmd}")
        try:
            client_obj.start(cmd)
        except Exception as e:
            print(f"[ERROR] Failed to start client {client_obj.id}: {e}")

    def start_monitors(self, monitors_map=None):
        """Start all instantiated monitors (non-blocking)."""
        if monitors_map is None:
            monitors_map = self._create_monitors_map()
        self._monitors = monitors_map
        for mid, mon in self._monitors.items():
            try:
                mon.start()
            except Exception as e:
                print(f"[WARN] Monitor {mid} failed to start: {e}")

    def start_loggers(self, loggers_map=None):
        """Prepare loggers (nothing blocking to do for file loggers)."""
        if loggers_map is None:
            loggers_map = self._create_loggers_map()
        self._loggers = loggers_map
        for lid, lg in self._loggers.items():
            try:
                lg.log(f"Logger {lid} initialized.", "DEBUG")
            except Exception as e:
                print(f"[WARN] Logger {lid} initialization error: {e}")

    def execute_post_actions(self, actions):
        """Execute post benchmark actions like collect metrics/logs and stop services."""
        print("[INFO] Executing post-actions...")
        for action in actions:
            if isinstance(action, dict):
                atype = action.get("type")
            else:
                atype = action
            if atype == "collect_metrics":
                for sid, s in getattr(self, "_services_objs", {}).items():
                    try:
                        s.collect_metrics()
                    except Exception as e:
                        print(f"[WARN] Collect metrics for {sid} failed: {e}")
            elif atype == "stop_services":
                for sid, s in getattr(self, "_services_objs", {}).items():
                    try:
                        s.stop()
                    except Exception as e:
                        print(f"[WARN] Stop service {sid} failed: {e}")
                for cid, c in getattr(self, "_clients_objs", {}).items():
                    try:
                        c.stop()
                    except Exception as e:
                        print(f"[WARN] Stop client {cid} failed: {e}")
            elif isinstance(action, str) and action.startswith("export_logs"):
                # format: "export_logs:logger_id"
                parts = action.split(":", 1)
                lid = parts[1] if len(parts) > 1 else None
                if lid and lid in getattr(self, "_loggers", {}):
                    path = self._loggers[lid].export()
                    print(f"[INFO] Exported logs for {lid} -> {path}")
                else:
                    print(f"[WARN] export_logs target '{lid}' not found.")
            else:
                print(f"[INFO] Unknown post-action: {action}")

    def run_benchmark(self):
        """Run the full benchmark based on the loaded recipe using concrete classes."""
        if self.recipe is None:
            print("[ERROR] No recipe loaded.")
            return

        print("[INFO] === Starting Benchmark ===")

        # instantiate monitors and loggers from recipe
        monitors_map = self._create_monitors_map()
        loggers_map = self._create_loggers_map()
        self.start_monitors(monitors_map)
        self.start_loggers(loggers_map)

        # build services objects
        services_objs = {}
        for svc in self.recipe.get("services", []):
            sid = svc["id"]
            executor_spec = svc.get("executor", {})
            executor = self._create_executor(executor_spec)
            monitor_ref = svc.get("monitor")
            logger_ref = svc.get("logger")
            monitor_obj = monitors_map.get(monitor_ref) if monitor_ref else None
            logger_obj = loggers_map.get(logger_ref) if logger_ref else None
            svc_obj = Service(id=sid, role=svc.get("role"), executor=executor, monitor=monitor_obj, logger=logger_obj)
            services_objs[sid] = svc_obj
        self._services_objs = services_objs

        # build clients objects (reuse Service class)
        clients_objs = {}
        for cl in self.recipe.get("clients", []):
            cid = cl["id"]
            executor_spec = cl.get("executor", {})
            executor = self._create_executor(executor_spec)
            monitor_ref = cl.get("monitor")
            logger_ref = cl.get("logger")
            monitor_obj = monitors_map.get(monitor_ref) if monitor_ref else None
            logger_obj = loggers_map.get(logger_ref) if logger_ref else None
            client_obj = Service(id=cid, role=cl.get("type", "client"), executor=executor, monitor=monitor_obj, logger=logger_obj)
            clients_objs[cid] = client_obj
        self._clients_objs = clients_objs

        # launch services
        for svc in self.recipe.get("services", []):
            sid = svc["id"]
            cmd_spec = svc.get("executor", {}).get("cmd") or svc.get("command", "")
            self.launch_service(self._services_objs[sid], cmd_spec)

        # launch clients
        for cl in self.recipe.get("clients", []):
            cid = cl["id"]
            # prefer explicit workload command, else executor.cmd
            cmd_spec = cl.get("workload", {}).get("cmd") if isinstance(cl.get("workload"), dict) else cl.get("executor", {}).get("cmd")
            self.launch_client(self._clients_objs[cid], cmd_spec or "")

        # monitor runtime: wait until duration or all clients finished
        duration = self.recipe.get("execution", {}).get("duration", 600)
        poll_interval = self.recipe.get("execution", {}).get("poll_interval", 5)
        print(f"[INFO] Running benchmark for up to {duration} seconds...")
        start_time = time.time()
        while time.time() - start_time < duration:
            all_done = True
            for cid, client in self._clients_objs.items():
                try:
                    st = client.status()
                    if st in ("running", "RUNNING", "PENDING", "UNKNOWN", None):
                        all_done = False
                        break
                except Exception:
                    all_done = False
                    break
            if all_done:
                print("[INFO] All clients completed before timeout.")
                break
            time.sleep(poll_interval)

        # execute post actions
        post_actions = self.recipe.get("execution", {}).get("post_actions", [])
        self.execute_post_actions(post_actions)

        print("[INFO] === Benchmark Completed ===\n")
