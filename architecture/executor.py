import subprocess
import shlex

class SlurmExecutor(Executor):
    def __init__(self, job_name="service_job", nodes=1, ntasks=1):
        self.job_name = job_name
        self.nodes = nodes
        self.ntasks = ntasks
        self.job_id = None

    def run(self, command: str):
        slurm_cmd = f"sbatch -N {self.nodes} -n {self.ntasks} --job-name={self.job_name} --wrap='{command}'"
        print(f"[SlurmExecutor] Submitting job: {slurm_cmd}")
        output = subprocess.check_output(slurm_cmd, shell=True, text=True)
        
        self.job_id = output.strip().split()[-1]
        print(f"[SlurmExecutor] Job ID: {self.job_id}")
        return self.job_id

    def stop(self):
        if not self.job_id:
            print("No active job.")
            return
        subprocess.run(f"scancel {self.job_id}", shell=True)
        print(f"Canceled job {self.job_id}")
        self.job_id = None

    def status(self):
        if not self.job_id:
            return "no job"
        try:
            output = subprocess.check_output(f"squeue -j {self.job_id} -h -o %T", shell=True, text=True)
            return output.strip()
        except subprocess.CalledProcessError:
            return "not found"
