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

# Load Apptainer
module add Apptainer || { echo "[ERROR] Failed to load Apptainer"; exit 1; }

# Build container if not already done
if [ ! -f "${PROJECT_DIR}/benchmark.sif" ]; then
    echo "[INFO] Building Apptainer image..."
    apptainer build "${PROJECT_DIR}/benchmark.sif" "${PROJECT_DIR}/apptainer.def"
fi

# Path to recipe
RECIPE_PATH="${PROJECT_DIR}/Recipes/Meluxina_DataIngestionRecipe.yaml"

# Debug info
echo "[DEBUG] Checking image path: ${PROJECT_DIR}/benchmark.sif"
ls -lh ${PROJECT_DIR}/benchmark.sif || echo "[ERROR] Image not found!"

# Run the benchmark
echo "[INFO] Running benchmark..."
apptainer run \
  --bind "${PROJECT_DIR}:/workspace:ro" \
  --bind "${WORKSPACE}:/output:rw" \
  "${PROJECT_DIR}/benchmark.sif" \
  --load /workspace/Recipes/Meluxina_DataIngestionRecipe.yaml \
  --workspace /output \
  --run

echo "[INFO] Benchmark completed. Results available in: ${WORKSPACE}"
