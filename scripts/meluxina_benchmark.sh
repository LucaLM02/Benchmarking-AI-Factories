#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${SCRIPT_DIR}/meluxina_cluster.conf}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "[ERROR] Missing configuration file at ${CONFIG_FILE}" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${CONFIG_FILE}"

REQUIRED_VARS=(PROJECT_ID SLURM_PARTITION SLURM_TIME_LIMIT REMOTE_WORKSPACE REMOTE_PROJECT_DIR RECIPE_PATH REPO_URL LOCAL_RESULTS_DIR)
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "[ERROR] Variable ${var} is not defined in ${CONFIG_FILE}" >&2
    exit 1
  fi
done

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_ID="run_$(date +%Y%m%d_%H%M%S)"
REMOTE_RUN_DIR="${REMOTE_WORKSPACE}/${RUN_ID}"

echo "[INFO] Launching MeluXina benchmark job ${RUN_ID}..."

echo "[INFO] Copying project to MeluXina..."
ssh meluxina "mkdir -p '${REMOTE_PROJECT_DIR}'"
rsync -av --delete \
  --exclude ".git" \
  --exclude "__pycache__" \
  "${PROJECT_ROOT}/" "meluxina:${REMOTE_PROJECT_DIR}/"

JOB_ID=$(ssh meluxina bash <<EOF
set -euo pipefail
cd "${REMOTE_PROJECT_DIR}"
sbatch -q default -p "${SLURM_PARTITION}" --time="${SLURM_TIME_LIMIT}" -A "${PROJECT_ID}" \
  --export=ALL,RUN_ID="${RUN_ID}",CONFIG_FILE="${REMOTE_PROJECT_DIR}/scripts/meluxina_cluster.conf" \
  run_benchmark.sh | awk '{print \$4}'
EOF
)

echo "[INFO] Job submitted with ID: ${JOB_ID}"
echo "[INFO] Waiting for job ${JOB_ID} to complete..."

# Loop finché il job è in coda o in esecuzione
while ssh meluxina "squeue -j ${JOB_ID} -h | grep -q ."; do
  echo "[INFO] Job ${JOB_ID} still running..."
  sleep 10
done

echo "[INFO] Job ${JOB_ID} completed."
ssh meluxina "sacct -j ${JOB_ID} --format=JobID,State,Elapsed --noheader | head -n1"


mkdir -p "${LOCAL_RESULTS_DIR}"

echo "[INFO] Copying Prometheus artifacts locally..."
scp -r meluxina:"${REMOTE_RUN_DIR}/prom_snapshot.json" "${LOCAL_RESULTS_DIR}/${RUN_ID}"
scp -r meluxina:"${REMOTE_RUN_DIR}/prom_snapshot_grafana.json" "${LOCAL_RESULTS_DIR}/${RUN_ID}" || true

echo "[INFO] Results available under ${LOCAL_RESULTS_DIR}"
echo "[INFO] Latest run data:"
echo "  Snapshot: ${LOCAL_RESULTS_DIR}/${RUN_ID}/prom_snapshot.json"
echo "  Grafana series: ${LOCAL_RESULTS_DIR}/${RUN_ID}/prom_snapshot_grafana.json"

if command -v docker >/dev/null 2>&1; then
  if [[ "${START_GRAFANA:-1}" == "1" ]]; then
    echo "[INFO] Starting local Grafana container on port ${GRAFANA_PORT:-3010} (requires JSON API plugin)."
    docker rm -f grafana-ai-factory >/dev/null 2>&1 || true
    docker run -d \
      --name grafana-ai-factory \
      -p "${GRAFANA_PORT:-3010}:3000" \
      -e "GF_INSTALL_PLUGINS=grafana-simple-json-datasource" \
      grafana/grafana:10.4.4 >/dev/null
    echo "[INFO] Grafana is running at http://localhost:${GRAFANA_PORT:-3010}"
    echo "[INFO] Configure a JSON data source pointing to your FastAPI endpoint (see tools/launch_api.py) to visualize the exported metrics."
  else
    echo "[INFO] START_GRAFANA=0, skipping Grafana auto-start."
  fi
else
  echo "[WARN] Docker not found, skipping Grafana startup."
fi
