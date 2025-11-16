#!/bin/bash
#SBATCH --job-name=benchmark_test
#SBATCH --qos=default
#SBATCH --account=p200981
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --partition=gpu
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err

# ------------------------------------------
# USER CONFIGURATION
# ------------------------------------------
PROJECT_ID="p200981"          # your MeluXina project ID
USER_ID="${USER}"             # automatically your username
JOB_NAME="benchmark_run"

# Assume the script is executed from the project root
PROJECT_DIR="$(pwd)"
echo "[INFO] Current working directory set as project root: ${PROJECT_DIR}"

# Define workspace dynamically under SCRATCH (project area)
SCRATCH_BASE="/project/scratch/${PROJECT_ID}/${USER_ID}"

WORKSPACE="${SCRATCH_BASE}/benchmarks/${JOB_NAME}_$(date +%Y%m%d_%H%M%S)"

# Create workspace and log directories
mkdir -p "${PROJECT_DIR}/logs" "${WORKSPACE}"

echo "[INFO] Workspace created at: ${WORKSPACE}"

# -----------------------------
# LOAD PYTHON + CREATE VENV + INSTALL REQUIREMENTS
# -----------------------------
module load Python || {
    echo "[ERROR] Unable to load Python module";
    exit 1;
}

VENV_DIR="${WORKSPACE}/venv"

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] Creating Python virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Install requirements
REQ_FILE="${PROJECT_DIR}/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    echo "[INFO] Installing Python dependencies from $REQ_FILE"
    pip install --upgrade pip
    pip install -r "$REQ_FILE"
else
    echo "[WARN] No requirements.txt found at $REQ_FILE"
fi

echo "[INFO] Python environment ready"

module add Apptainer || {
    echo "[ERROR] Unable to load Apptainer module";
    exit 1;
}

export PYTHONPATH="${PROJECT_DIR}/src:${PYTHONPATH}"

# -----------------------------
# RUN CLI (NO APPTAINER HERE)
# -----------------------------
echo "[INFO] Running CLI with host workspace..."

python3 src/Interface/CLI.py \
    --load "${RECIPE_PATH}" \
    --run

echo "[INFO] Benchmark finished."
echo "[INFO] Results available at: ${WORKSPACE}"