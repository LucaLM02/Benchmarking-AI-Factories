import subprocess
from Core.abstracts import Executor

class SlurmExecutor(Executor):
    def __init__(
        self,
        job_name="service_job",
        nodes=1,
        ntasks=1,
        cpus_per_task=1,
        mem="4G",
        time="00:30:00",
        partition="cpu",
        account="p200981",
        gres=None,        # es: "gpu:1"
        image=None        # opzionale: usato dal BenchmarkManager per sostituire {{image}}
    ):
        self.job_name = job_name
        self.nodes = nodes
        self.ntasks = ntasks
        self.cpus_per_task = cpus_per_task
        self.mem = mem
        self.time = time
        self.partition = partition
        self.account = account
        self.gres = gres
        self.image = image
        self.job_id = None

    def run(self, command: str):
        gres_flag = f"--gres={self.gres}" if self.gres else ""
        image_env = f"--export=APPTAINER_IMAGE={self.image}" if self.image else "--export=ALL"

        slurm_cmd = (
            f"sbatch "
            f"-N {self.nodes} "
            f"-n {self.ntasks} "
            f"--cpus-per-task={self.cpus_per_task} "
            f"--mem={self.mem} "
            f"--time={self.time} "
            f"--partition={self.partition} "
            f"--account={self.account} "
            f"{gres_flag} "
            f"--job-name={self.job_name} "
            f"{image_env} "
            f"--wrap='{command}'"
        )

        print(f"[SlurmExecutor] Submitting job: {slurm_cmd}")

        try:
            output = subprocess.check_output(slurm_cmd, shell=True, text=True)
            self.job_id = output.strip().split()[-1]
            print(f"[SlurmExecutor] Job ID: {self.job_id}")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Slurm job submission failed: {e}")
            raise

        return self.job_id

    def stop(self):
        if self.job_id:
            subprocess.run(f"scancel {self.job_id}", shell=True)
            print(f"[SlurmExecutor] Job {self.job_id} cancelled.")
            self.job_id = None

    def status(self):
        if not self.job_id:
            return "no job"
        try:
            output = subprocess.check_output(
                f"squeue -j {self.job_id} -h -o %T",
                shell=True,
                text=True
            )
            return output.strip()
        except subprocess.CalledProcessError:
            return "not found"
