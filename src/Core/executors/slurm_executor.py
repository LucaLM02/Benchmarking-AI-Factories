import subprocess
from Core.abstracts import Executor


class SlurmExecutor(Executor):
    """
    HPC GPU-based Slurm executor.
    Always submits jobs to GPU nodes in MeluXina.
    """

    def __init__(self,
                 job_name="gpu_job",
                 nodes=1,
                 ntasks=1,
                 gpus_per_node=1,
                 cpus_per_gpu=4,
                 mem="16G",
                 partition="gpu",
                 image=None):
        self.job_name = job_name
        self.nodes = nodes
        self.ntasks = ntasks
        self.gpus_per_node = gpus_per_node
        self.cpus_per_gpu = cpus_per_gpu
        self.mem = mem
        self.partition = partition
        self.image = image
        self.job_id = None

    def run(self, command: str):

        # Wrap command inside Apptainer if image is provided
        if self.image:
            command = f"apptainer exec {self.image} {command}"

        slurm_cmd = (
            f"sbatch "
            f"-N {self.nodes} "
            f"-n {self.ntasks} "
            f"--partition={self.partition} "
            f"--gres=gpu:{self.gpus_per_node} "
            f"--cpus-per-task={self.cpus_per_gpu} "
            f"--mem={self.mem} "
            f"--job-name={self.job_name} "
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
