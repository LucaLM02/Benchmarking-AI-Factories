#!/bin/bash
#SBATCH --job-name=benchmark_test
#SBATCH --qos=default
#SBATCH --account=p200981
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --partition=gpu
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

set -euo pipefail

# ------------------------------------------
# CONFIGURATION FROM meluxina_benchmark.sh
# ------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMIT_DIR="${SLURM_SUBMIT_DIR:-${SCRIPT_DIR}}"
# Prefer config alongside the script; fall back to submit dir if needed
CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/scripts/meluxina_cluster.conf}"
if [[ ! -f "${CONFIG_FILE}" ]]; then
    CONFIG_FILE="${SUBMIT_DIR}/scripts/meluxina_cluster.conf"
fi

if [[ -f "${CONFIG_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_FILE}"
else
    echo "[WARN] Config file ${CONFIG_FILE} not found. Falling back to defaults."
fi

# ------------------------------------------
# USER CONFIGURATION
# ------------------------------------------
PROJECT_ID="${PROJECT_ID:-p200981}"     # MeluXina project ID (overridable via config)
USER_ID="${USER_ID:-${USER}}"           # automatically your username
JOB_NAME="${JOB_NAME:-benchmark_run}"

# Resolve project root from config, otherwise from this script location
REMOTE_PROJECT_DIR="${REMOTE_PROJECT_DIR:-${SCRIPT_DIR}}"
if [[ -d "${REMOTE_PROJECT_DIR}" ]]; then
    cd "${REMOTE_PROJECT_DIR}"
fi
PROJECT_DIR="$(pwd)"
echo "[INFO] Using project root: ${PROJECT_DIR}"

# Define workspace dynamically under REMOTE_WORKSPACE (project area)
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-/project/scratch/${PROJECT_ID}/${USER_ID}/benchmarks}"
RUN_ID="${RUN_ID:-${JOB_NAME}_$(date +%Y%m%d_%H%M%S)}"

WORKSPACE="${REMOTE_WORKSPACE}/${RUN_ID}"
export WORKSPACE

LOG_DIR="${WORKSPACE}/logs"
RECIPE_PATH="${RECIPE_PATH:-Recipes/Meluxina_DataIngestionRecipe.yaml}"
if [[ "${RECIPE_PATH}" != /* ]]; then
    RECIPE_PATH="${PROJECT_DIR}/${RECIPE_PATH}"
fi

# Create workspace and log directories
mkdir -p "${LOG_DIR}" "${WORKSPACE}"

echo "[INFO] Workspace created at: ${WORKSPACE}"
RUNTIME_LOG="${LOG_DIR}/${JOB_NAME}_${SLURM_JOB_ID:-${RUN_ID}}.log"
echo "[INFO] Capturing runtime logs to: ${RUNTIME_LOG}"
# keep Slurm output while duplicating logs inside the scratch workspace
exec > >(tee -a "${RUNTIME_LOG}") 2>&1

echo "[INFO] Single-node execution: services and clients share this Slurm allocation via local processes."

# -----------------------------
# LOAD PYTHON + CREATE VENV + INSTALL REQUIREMENTS
# -----------------------------
# Make sure the environment has the module function available (non-login shells may miss it)
if ! command -v module >/dev/null 2>&1; then
    for init_file in "/etc/profile" "/etc/profile.d/modules.sh" "/usr/share/Modules/init/bash"; do
        if [[ -f "${init_file}" ]]; then
            # shellcheck source=/dev/null
            source "${init_file}"
        fi
    done
fi

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
    --workspace "${WORKSPACE}" \
    --run

if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    SUBMIT_LOG_DIR="${SLURM_SUBMIT_DIR:-${PROJECT_DIR}}"
    for file in "${SLURM_JOB_NAME}_${SLURM_JOB_ID}.out" "${SLURM_JOB_NAME}_${SLURM_JOB_ID}.err"; do
        SRC="${SUBMIT_LOG_DIR}/${file}"
        if [[ -f "${SRC}" ]]; then
            mv "${SRC}" "${LOG_DIR}/"
            echo "[INFO] Moved Slurm log ${SRC} -> ${LOG_DIR}/"
        fi
    done
fi

echo "[INFO] Benchmark finished."
echo "[INFO] Results available at: ${WORKSPACE}"
