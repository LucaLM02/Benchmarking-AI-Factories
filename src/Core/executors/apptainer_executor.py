import subprocess
from Core.abstracts import Executor

class ApptainerExecutor(Executor):
    """Executor per gestire container Apptainer."""
    
    def __init__(self, image: str, nv: bool = True):
        """
        Inizializza l'executor Apptainer.
        
        Args:
            image: percorso o URI dell'immagine Apptainer
            nv: abilita supporto NVIDIA GPU (default True)
        """
        self.image = image
        self.nv = nv
        self._proc = None

    def run(self, command: str, **kwargs) -> str:
        """
        Esegue un comando all'interno del container Apptainer.
        
        Args:
            command: comando da eseguire
            kwargs: argomenti aggiuntivi per Popen
        
        Returns:
            str: ID del processo (PID)
        """
        base = ["apptainer", "exec"]
        if self.nv:
            base.append("--nv")
        base.append(self.image)
        
        if isinstance(command, str):
            cmd_list = base + ["sh", "-c", command]
        else:
            cmd_list = base + list(command)
            
        print(f"[ApptainerExecutor] Running: {' '.join(cmd_list)}")
        self._proc = subprocess.Popen(cmd_list)
        return str(self._proc.pid)

    def stop(self) -> None:
        """Termina il processo del container se in esecuzione."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
        self._proc = None

    def status(self) -> str:
        """
        Verifica lo stato del processo.
        
        Returns:
            str: 'no process', 'running' o 'finished'
        """
        if not self._proc:
            return "no process"
        return "running" if self._proc.poll() is None else "finished"