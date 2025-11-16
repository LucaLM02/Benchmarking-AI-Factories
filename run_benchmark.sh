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
# MODULES (only what is needed for Python)
# -----------------------------
module load Python/3.10.8 || {
    echo "[ERROR] Unable to load Python module";
    exit 1;
}

# -----------------------------
# RUN CLI (NO APPTAINER HERE)
# -----------------------------
echo "[INFO] Running CLI with host workspace..."

python3 src/Interface/CLI.py \
    --load "${RECIPE_PATH}" \
    --workspace "${WORKSPACE}" \
    --run

echo "[INFO] Benchmark finished."
echo "[INFO] Results available at: ${WORKSPACE}"