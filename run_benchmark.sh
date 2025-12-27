#!/bin/bash
#SBATCH --job-name=benchmark_test
#SBATCH --qos=default
#SBATCH --account=p200981
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:30:00
#SBATCH --partition=gpu
#SBATCH --chdir=/project/scratch/p200981/%u/benchmarks/Benchmarking-AI-Factories
#SBATCH --output=/project/scratch/p200981/%u/benchmarks/slurm_logs/%x_%j.out
#SBATCH --error=/project/scratch/p200981/%u/benchmarks/slurm_logs/%x_%j.err

# ------------------------------------------
# INITIALIZE MODULE SYSTEM
# ------------------------------------------
# This is critical for batch jobs on MeluXina
if [ -f /etc/profile.d/modules.sh ]; then
    source /etc/profile.d/modules.sh
elif [ -f /usr/share/Modules/init/bash ]; then
    source /usr/share/Modules/init/bash
else
    echo "[ERROR] Module system not found"
    exit 1
fi

# Add MeluXina software modules to MODULEPATH
# This path contains all the software modules (Python, Apptainer, etc.)
echo "[INFO] Setting up MeluXina module environment..."
module use /apps/USE/easybuild/release/2024.1/modules/all

# Load the default MeluXina environment (this is loaded automatically in interactive sessions)
module load env/release/2024.1

echo "[INFO] Module system initialized"
echo "[INFO] MODULEPATH: $MODULEPATH"

# ------------------------------------------
# USER CONFIGURATION
# ------------------------------------------
PROJECT_ID="p200981"          # your MeluXina project ID
USER_ID="${USER}"             # automatically your username
JOB_NAME="benchmark_run"

# Assume the script is executed from the project root
PROJECT_DIR="$(pwd)"
export PROJECT_DIR
echo "[INFO] Current working directory set as project root: ${PROJECT_DIR}"

# Define workspace dynamically under SCRATCH (project area)
SCRATCH_BASE="/project/scratch/${PROJECT_ID}/${USER_ID}"
mkdir -p "${SCRATCH_BASE}/benchmarks" "${SCRATCH_BASE}/benchmarks/slurm_logs"

WORKSPACE="${SCRATCH_BASE}/benchmarks/${JOB_NAME}_$(date +%Y%m%d_%H%M%S)"
export WORKSPACE

RECIPE_PATH="${PROJECT_DIR}/Recipes/vLLM_InferenceRecipe.yaml"

# Create workspace directory for this run
mkdir -p "${WORKSPACE}"

echo "[INFO] Workspace created at: ${WORKSPACE}"
echo "[INFO] Single-node execution: services and clients share this Slurm allocation via local processes."

# -----------------------------
# LOAD PYTHON + CREATE VENV + INSTALL REQUIREMENTS
# -----------------------------
echo "[INFO] Loading Python module..."

# Now that env/release/2024.1 is loaded, we can load Python modules directly
# Try from newest to oldest
if module load Python/3.12.4-GCCcore-12.3.0 2>/dev/null; then
    echo "[INFO] Python 3.12.4 module loaded"
elif module load Python/3.12.3-GCCcore-13.3.0 2>/dev/null; then
    echo "[INFO] Python 3.12.3 module loaded"
elif module load Python/3.11.10-GCCcore-13.3.0 2>/dev/null; then
    echo "[INFO] Python 3.11.10 module loaded"
elif module load Python/3.11.3-GCCcore-13.3.0 2>/dev/null; then
    echo "[INFO] Python 3.11.3 module loaded"
elif module load Python/3.11.3-GCCcore-12.3.0 2>/dev/null; then
    echo "[INFO] Python 3.11.3 module loaded"
elif module load Python/3.10.8-GCCcore-12.3.0 2>/dev/null; then
    echo "[INFO] Python 3.10.8 module loaded"
else
    echo "[ERROR] Failed to load any Python module"
    echo "[INFO] Showing available Python modules:"
    module avail Python 2>&1 | head -20
    exit 1
fi

# Verify Python version
PYTHON_VERSION=$(python3 --version 2>&1)
echo "[INFO] Using Python: $PYTHON_VERSION"
echo "[INFO] Python path: $(which python3)"

VENV_DIR="${WORKSPACE}/venv"

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] Creating Python virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Activate venv
echo "[INFO] Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Install requirements
REQ_FILE="${PROJECT_DIR}/requirements-core.txt"
if [ -f "$REQ_FILE" ]; then
    echo "[INFO] Installing Python dependencies from $REQ_FILE"
    pip install --upgrade pip --quiet
    pip install -r "$REQ_FILE" --quiet
else
    echo "[WARN] No requirements file found at $REQ_FILE"
fi

echo "[INFO] Python environment ready"

# Load Apptainer
echo "[INFO] Loading Apptainer module..."
if module load Apptainer 2>/dev/null; then
    echo "[INFO] Apptainer module loaded"
elif module load apptainer 2>/dev/null; then
    echo "[INFO] apptainer module loaded (lowercase)"
elif module load Singularity 2>/dev/null; then
    echo "[INFO] Singularity module loaded (alternative)"
else
    echo "[WARN] Unable to load Apptainer module"
    echo "[INFO] Checking if apptainer/singularity is available in PATH..."
    if command -v apptainer &> /dev/null; then
        echo "[INFO] Using apptainer from PATH: $(which apptainer)"
    elif command -v singularity &> /dev/null; then
        echo "[INFO] Using singularity from PATH: $(which singularity)"
    else
        echo "[ERROR] No container runtime available"
        exit 1
    fi
fi

export PYTHONPATH="${PROJECT_DIR}/src:${PYTHONPATH}"

# -----------------------------
# RUN CLI
# -----------------------------
echo "[INFO] Running CLI with host workspace..."
echo "[INFO] Recipe: ${RECIPE_PATH}"
echo "[INFO] Workspace: ${WORKSPACE}"

python3 src/Interface/CLI.py \
    --load "${RECIPE_PATH}" \
    --workspace "${WORKSPACE}" \
    --run

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "[INFO] Benchmark finished successfully."
    echo "[INFO] Results available at: ${WORKSPACE}"
else
    echo "[ERROR] Benchmark failed with exit code: ${EXIT_CODE}"
    exit $EXIT_CODE
fi