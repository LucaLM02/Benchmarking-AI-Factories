import yaml
import os
import subprocess
from time import sleep

class BenchmarkManager:
    def __init__(self):
        self.recipe = None
        self.recipe_path = None

    def load_recipe(self, path: str):
        """Load and parse a YAML recipe file."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Recipe file {path} does not exist.")
        
        with open(path, 'r') as f:
            self.recipe = yaml.safe_load(f)
        
        self.recipe_path = path
        print(f"[INFO] Recipe loaded from {path}")
        self.validate_recipe()

    def validate_recipe(self):
        """Basic validation of the loaded recipe."""
        if self.recipe is None:
            raise ValueError("No recipe loaded.")
        
        required_sections = ["meta", "global", "services", "clients"]
        for section in required_sections:
            if section not in self.recipe:
                raise ValueError(f"Missing required section '{section}' in recipe.")
        
        print("[INFO] Recipe validation passed.")

    def show_summary(self):
        """Print a summary of the recipe."""
        if self.recipe is None:
            print("[ERROR] No recipe loaded.")
            return
        
        meta = self.recipe.get("meta", {})
        services = self.recipe.get("services", [])
        clients = self.recipe.get("clients", [])

        print("\n=== Recipe Summary ===")
        print(f"Name: {meta.get('name','N/A')}")
        print(f"Description: {meta.get('description','N/A')}")
        print(f"Author: {meta.get('author','N/A')}")
        print(f"Created at: {meta.get('created_at','N/A')}")
        print(f"Services: {len(services)}")
        for s in services:
            print(f"  - {s['id']} ({s['role']})")
        print(f"Clients: {len(clients)}")
        for c in clients:
            print(f"  - {c['id']} ({c['type']})")
        print("=====================\n")

    # ------------------------
    # Benchmark execution
    # ------------------------

    def launch_service(self, service):
        """Launch a single service (placeholder)."""
        print(f"[INFO] Launching service {service['id']} on {service['executor']['slurm']['nodes']} node(s)...")
        # For now we just simulate launch
        cmd = service.get("executor", {}).get("cmd", [])
        image = service.get("executor", {}).get("image", "")
        if cmd and image:
            full_cmd = ["apptainer", "exec", "--nv", image] + cmd
            print(f"[DEBUG] Running command: {' '.join(full_cmd)}")
            # subprocess.Popen(full_cmd) # Uncomment in real implementation
        sleep(1)  # simulate startup delay

    def launch_client(self, client):
        """Launch a single client (placeholder)."""
        print(f"[INFO] Launching client {client['id']} with {client['executor']['slurm']['nodes']} node(s)...")
        #...

    def start_monitors(self):
        """Start monitoring services (placeholder)."""
        print("[INFO] Starting monitors...")
        #...

    def start_loggers(self):
        """Start loggers (placeholder)."""
        print("[INFO] Starting loggers...")
        #...

    def execute_post_actions(self, actions):
        """Execute post benchmark actions like collect metrics/logs and stop services."""
        print("[INFO] Executing post-actions...")
        for action in actions:
            print(f" - {action}")
            #...

    def run_benchmark(self):
        """Run the full benchmark based on the loaded recipe."""
        if self.recipe is None:
            print("[ERROR] No recipe loaded.")
            return
        
        print("[INFO] === Starting Benchmark ===")
        
        # 1. Launch monitors and loggers first
        self.start_monitors()
        self.start_loggers()

        # 2. Launch services
        for service in self.recipe.get("services", []):
            self.launch_service(service)

        # 3. Launch clients
        for client in self.recipe.get("clients", []):
            self.launch_client(client)

        # 4. Simulate benchmark duration
        duration = self.recipe.get("execution", {}).get("duration", 600)
        print(f"[INFO] Running benchmark for {duration} seconds...")
        start_time = time.time()
        while time.time() - start_time < duration:
            # Check job status
            result = subprocess.run(["squeue", "-u", "username"], capture_output=True, text=True)
            if "client-job-name" not in result.stdout:
                break  # client terminato
            time.sleep(5)  # poll ogni 5 secondi

        # 5. Execute post-actions
        post_actions = self.recipe.get("execution", {}).get("post_actions", [])
        self.execute_post_actions(post_actions)

        print("[INFO] === Benchmark Completed ===\n")
