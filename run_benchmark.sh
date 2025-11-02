#!/bin/bash
#SBATCH --job-name=ai_benchmark           # Default job name (can be overridden)
#SBATCH --nodes=1                         # Number of nodes
#SBATCH --ntasks=1                        # Number of tasks
#SBATCH --cpus-per-task=8                 # CPU cores per task
#SBATCH --mem=32G                         # Memory allocation
#SBATCH --time=00:15:00                   # Max runtime (hh:mm:ss)
#SBATCH --output=logs/%x_%j.out           # STDOUT log file
#SBATCH --error=logs/%x_%j.err            # STDERR log file
#SBATCH --partition=gpu                   # Partition

# ============================================================
# 1. USER CONFIGURATION
# ============================================================

# Usage:
# sbatch run_benchmark.sh <recipe_path> [job_name]
#
# Example:
# sbatch run_benchmark.sh Recipes/Meluxina_DataIngestionRecipe.yaml ingestion_test

# The first argument is the recipe path (relative to repo root)
RECIPE_PATH=${1:-"Recipes/Meluxina_DataIngestionRecipe.yaml"}

# Optional second argument overrides the job name
JOB_NAME=${2:-"benchmark_job"}

# ============================================================
# 2. ENVIRONMENT SETUP
# ============================================================

# Load Apptainer (Singularity) module
module add Apptainer

# Define the project directory automatically (the current folder)
PROJECT_DIR=$(pwd)

# Define the path to the Apptainer image
IMAGE_PATH="${PROJECT_DIR}/benchmark.sif"

# Define a workspace dynamically (e.g., /scratch/$USER/benchmarks/<job_name>)
WORKSPACE="/scratch/${USER}/benchmarks/${JOB_NAME}_$(date +%Y%m%d_%H%M%S)"

# Create logs and workspace directories
mkdir -p "${PROJECT_DIR}/logs" "${WORKSPACE}"

# ============================================================
# 3. APPTAINER IMAGE SETUP
# ============================================================

# If the container image does not exist, build it
if [ ! -f "${IMAGE_PATH}" ]; then
    echo "[INFO] Apptainer image not found. Building from apptainer.def..."
    apptainer build "${IMAGE_PATH}" "${PROJECT_DIR}/apptainer.def"
else
    echo "[INFO] Using existing Apptainer image: ${IMAGE_PATH}"
fi

# ============================================================
# 4. PRINT JOB DETAILS
# ============================================================

echo ""
echo "=== JOB CONFIGURATION ==="
echo "User:           ${USER}"
echo "Host:           $(hostname)"
echo "Project dir:    ${PROJECT_DIR}"
echo "Recipe file:    ${RECIPE_PATH}"
echo "Workspace dir:  ${WORKSPACE}"
echo "Apptainer img:  ${IMAGE_PATH}"
echo "=========================="
echo ""

# ============================================================
# 5. RUN BENCHMARK
# ============================================================

# The `--bind` ensures the project folder is mounted as /workspace
# Inside the container, the benchmark script can access everything under /workspace
apptainer run \
    --bind "${PROJECT_DIR}:/workspace:rw" \
    "${IMAGE_PATH}" \
    --load "/workspace/${RECIPE_PATH}" \
    --workspace "${WORKSPACE}" \
    --run

# ============================================================
# 6. JOB SUMMARY
# ============================================================

echo ""
echo "[INFO] Benchmark finished."
echo "[INFO] Results available in: ${WORKSPACE}"
