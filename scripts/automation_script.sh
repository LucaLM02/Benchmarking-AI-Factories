#!/bin/bash
# ============================================================================
# Automation script for running benchmarks on MeluXina from local machine (WSL/Docker Support)
# ============================================================================

set -e  # Exit on error

# ------------------------------------------
# CONFIGURATION
# ------------------------------------------
MELUXINA_HOST="meluxina"
MELUXINA_PROJECT_ID="p200981"
MELUXINA_USER="u103212"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
REMOTE_BASE_DIR="/project/scratch/${MELUXINA_PROJECT_ID}/${MELUXINA_USER}/benchmarks"
REMOTE_PROJECT_DIR="${REMOTE_BASE_DIR}/${PROJECT_NAME}"
REMOTE_SLURM_LOG_DIR="${REMOTE_BASE_DIR}/slurm_logs"
LOCAL_RESULTS_DIR="$(dirname "${PROJECT_ROOT}")/results_${PROJECT_NAME}_$(date +%Y%m%d_%H%M%S)"
GRAFANA_PORT="${GRAFANA_PORT:-3010}"
FASTAPI_PORT="${FASTAPI_PORT:-8000}"
START_GRAFANA="${START_GRAFANA:-1}"
JOB_TIMEOUT="${JOB_TIMEOUT:-3600}"  # 1 hour max wait
VENV_DIR="${PROJECT_ROOT}/.venv"
CONTAINER_RUNTIME=""
STACK_STARTED=0
TMP_COMPOSE_FILE=""

# ------------------------------------------
# SERVICE-SPECIFIC METRIC DEFAULTS
# ------------------------------------------
# Detect service type from recipe file (set later in detect_service_type)
SERVICE_TYPE="unknown"

# S3/MinIO metric defaults
S3_METRIC1="minio_s3_requests_total"
S3_METRIC2="minio_s3_traffic_sent_bytes"
S3_METRIC3="minio_s3_requests_ttfb_seconds_distribution"

# vLLM metric defaults
VLLM_METRIC1="http_request_duration_highr_seconds_count"
VLLM_METRIC2="vllm:num_requests_running"
VLLM_METRIC3="http_request_duration_highr_seconds_bucket"

# Active metrics (set by detect_service_type)
DASHBOARD_METRIC1=""
DASHBOARD_METRIC2=""
DASHBOARD_METRIC3=""

# ------------------------------------------
# FUNCTIONS
# ------------------------------------------

log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $*"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $*" >&2
}

log_warn() {
    echo "[WARN] $(date '+%Y-%m-%d %H:%M:%S') - $*"
}

detect_service_type() {
    # Detect service type by reading the RECIPE_PATH from run_benchmark.sh
    local run_script="${PROJECT_ROOT}/run_benchmark.sh"
    local recipe_path=""
    
    # Extract RECIPE_PATH value from run_benchmark.sh
    if [ -f "$run_script" ]; then
        recipe_path=$(grep -oP 'RECIPE_PATH="\K[^"]+' "$run_script" 2>/dev/null | head -1)
        # Handle ${PROJECT_DIR} variable substitution
        recipe_path=$(echo "$recipe_path" | sed "s|\${PROJECT_DIR}|${PROJECT_ROOT}|g")
    fi
    
    if [ -z "$recipe_path" ] || [ ! -f "$recipe_path" ]; then
        log_warn "Could not find recipe path in run_benchmark.sh, scanning Recipes folder..."
        recipe_path=$(find "${PROJECT_ROOT}/Recipes" -name "*.yaml" -o -name "*.yml" 2>/dev/null | head -1)
    fi
    
    if [ -n "$recipe_path" ] && [ -f "$recipe_path" ]; then
        local recipe_name=$(basename "$recipe_path" | tr '[:upper:]' '[:lower:]')
        local recipe_content=$(cat "$recipe_path" 2>/dev/null | tr '[:upper:]' '[:lower:]')
        log_info "Active recipe: $(basename "$recipe_path")"
        
        # Check for vLLM indicators (in name OR content)
        if [[ "$recipe_name" == *"vllm"* ]] || [[ "$recipe_content" == *"vllm"* ]]; then
            SERVICE_TYPE="vllm"
            DASHBOARD_METRIC1="${VLLM_METRIC1}"
            DASHBOARD_METRIC2="${VLLM_METRIC2}"
            DASHBOARD_METRIC3="${VLLM_METRIC3}"
            log_info "Detected service type: vLLM (metrics: RPS, Concurrent Requests, Latency)"
            return
        fi
        
        # Check for S3/MinIO indicators (in name OR content)
        if [[ "$recipe_name" == *"s3"* ]] || [[ "$recipe_name" == *"minio"* ]] || \
           [[ "$recipe_content" == *"minio"* ]] || [[ "$recipe_content" == *"s3-upload"* ]] || \
           [[ "$recipe_content" == *"s3_upload"* ]]; then
            SERVICE_TYPE="s3"
            DASHBOARD_METRIC1="${S3_METRIC1}"
            DASHBOARD_METRIC2="${S3_METRIC2}"
            DASHBOARD_METRIC3="${S3_METRIC3}"
            log_info "Detected service type: S3/MinIO (metrics: Throughput, Bandwidth, TTFB)"
            return
        fi
    fi
    
    # Default to vLLM if no specific detection
    SERVICE_TYPE="vllm"
    DASHBOARD_METRIC1="${VLLM_METRIC1}"
    DASHBOARD_METRIC2="${VLLM_METRIC2}"
    DASHBOARD_METRIC3="${VLLM_METRIC3}"
    log_warn "Could not detect service type, defaulting to vLLM"
}

force_clean_path() {
    local target_path=$1
    if [ -e "$target_path" ]; then
        # Try normal remove first
        rm -rf "$target_path" 2>/dev/null || true
        
        # If still exists, use docker to remove (as it might be root-owned)
        if [ -e "$target_path" ] && command -v docker >/dev/null 2>&1; then
             log_info "Removing root-owned path via Docker: $target_path"
             # Use alpine to delete the file/folder mapping volume
             # We mount the PARENT directory to /work and delete the BASENAME
             local parent_dir
             parent_dir=$(dirname "$target_path")
             local base_name
             base_name=$(basename "$target_path")
             
             docker run --rm -v "${parent_dir}:/work" -w /work alpine rm -rf "${base_name}" || true
        fi
    fi
}

check_local_requirements() {
    if [[ ! -f "${PROJECT_ROOT}/run_benchmark.sh" ]]; then
        log_error "run_benchmark.sh not found in ${PROJECT_ROOT}. Ensure you run this from the repository root."
        exit 1
    fi

    # Check for Docker (Required for WSL/Docker Desktop flow)
    if command -v docker >/dev/null 2>&1; then
        docker info >/dev/null 2>&1 || { log_error "Docker is installed but not running. Please start Docker Desktop."; exit 1; }
        CONTAINER_RUNTIME="docker"
        log_info "Using Docker as container runtime"
    else
        log_error "Docker not found. Please install Docker Desktop for Windows (WSL integration enabled)."
        exit 1
    fi
}

check_ssh_connection() {
    log_info "Checking SSH connection to MeluXina..."
    if ! ssh -o ConnectTimeout=10 "${MELUXINA_HOST}" "echo 'Connection successful'" >/dev/null 2>&1; then
        log_error "Cannot connect to ${MELUXINA_HOST}. Check your SSH config and VPN."
        exit 1
    fi
    log_info "SSH connection OK"
}

setup_local_venv() {
    # Even if we use Docker for running the app, we might need basic local tools
    # or this step can be minimal if we fully rely on Docker.
    # Keeping it for script compatibility but making it optional/silent.
    log_info "Checking local environment..."
    if [ ! -d "${VENV_DIR}" ]; then
        log_info "Creating virtual environment at ${VENV_DIR} (for local helper scripts if needed)"
        python3 -m venv "${VENV_DIR}"
    fi
    source "${VENV_DIR}/bin/activate"
}

sync_project_to_remote() {
    log_info "Syncing project '${PROJECT_NAME}' to MeluXina..."
    
    ssh "${MELUXINA_HOST}" "mkdir -p ${REMOTE_PROJECT_DIR} ${REMOTE_SLURM_LOG_DIR}"
    
    rsync -az --delete \
        --exclude='.git' \
        --exclude='*.pyc' \
        --exclude='__pycache__' \
        --exclude='venv' \
        --exclude='.venv' \
        --exclude='.idea' \
        --exclude='.vscode' \
        --exclude='results_*' \
        --exclude='logs/*' \
        --exclude='src/Core/analytics' \
        "${PROJECT_ROOT}/" \
        "${MELUXINA_HOST}:${REMOTE_PROJECT_DIR}/"
    
    log_info "Project synced successfully"
}

submit_job() {
    log_info "Submitting job to MeluXina..." >&2
    
    local JOB_OUTPUT
    JOB_OUTPUT=$(ssh "${MELUXINA_HOST}" "cd ${REMOTE_PROJECT_DIR} && sbatch run_benchmark.sh")
    
    JOB_ID=$(echo "${JOB_OUTPUT}" | grep "Submitted batch job" | grep -oP '\d+' | head -1)
    
    if [[ -z "${JOB_ID}" ]]; then
        log_error "Failed to extract job ID from output:"
        echo "${JOB_OUTPUT}" >&2
        exit 1
    fi
    
    log_info "Job submitted with ID: ${JOB_ID}" >&2
    echo "${JOB_ID}"
}

wait_for_job() {
    local job_id=$1
    local elapsed=0
    
    log_info "Waiting for job ${job_id} to complete (timeout: ${JOB_TIMEOUT}s)..."
    
    while true; do
        SACCT_OUTPUT=$(ssh "${MELUXINA_HOST}" "sacct -j ${job_id} --format=State --noheader" 2>&1)
        JOB_STATE=$(echo "${SACCT_OUTPUT}" | head -n 1 | xargs)

        if [[ -z "${JOB_STATE}" ]]; then
            SQUEUE_OUTPUT=$(ssh "${MELUXINA_HOST}" "squeue -j ${job_id} -h -o '%T'" 2>&1)
            JOB_STATE=$(echo "${SQUEUE_OUTPUT}" | xargs)
            if [[ -z "${JOB_STATE}" ]]; then
                JOB_STATE="PENDING"
            fi
        fi

        if [[ $elapsed -ge $JOB_TIMEOUT ]]; then
            echo
            log_error "Job timeout reached (${JOB_TIMEOUT}s)"
            ssh "${MELUXINA_HOST}" "scancel ${job_id}"
            return 1
        fi

        case "${JOB_STATE}" in
            COMPLETED)
                echo
                log_info "Job ${job_id} completed successfully"
                return 0
                ;;
            FAILED|CANCELLED|TIMEOUT|NODE_FAIL|PREEMPTED|OUT_OF_MEMORY)
                echo
                log_error "Job ${job_id} failed with state: ${JOB_STATE}"
                return 1
                ;;
            *)
                echo -ne "\r[INFO] Job status: ${JOB_STATE:-UNKNOWN} (elapsed: ${elapsed}s)    "
                sleep 10
                elapsed=$((elapsed + 10))
                ;;
        esac
    done
}

find_latest_workspace() {
    local REMOTE_WORKSPACE
    REMOTE_WORKSPACE=$(ssh "${MELUXINA_HOST}" "ls -td ${REMOTE_BASE_DIR}/benchmark_run_* 2>/dev/null | head -1")
    REMOTE_WORKSPACE=$(echo "${REMOTE_WORKSPACE}" | tr -d '\n\r' | xargs)
    
    if [[ -z "${REMOTE_WORKSPACE}" ]]; then
        log_error "No workspace found on MeluXina."
        exit 1
    fi
    echo "${REMOTE_WORKSPACE}"
}

sync_results_from_remote() {
    local remote_workspace=$1
    
    log_info "Syncing results from MeluXina..."
    mkdir -p "${LOCAL_RESULTS_DIR}"
    
    if ! rsync -az \
        "${MELUXINA_HOST}:${remote_workspace}/" \
        "${LOCAL_RESULTS_DIR}/"; then
        log_error "Failed to sync workspace results"
        return 1
    fi
    
    rsync -az "${MELUXINA_HOST}:${REMOTE_SLURM_LOG_DIR}/" "${LOCAL_RESULTS_DIR}/slurm_logs/" 2>/dev/null || true
    rsync -az "${MELUXINA_HOST}:${REMOTE_PROJECT_DIR}/logs/" "${LOCAL_RESULTS_DIR}/logs/" 2>/dev/null || true
    
    log_info "Results synced to: ${LOCAL_RESULTS_DIR}"
}

start_full_stack_docker() {
    # Generate a temporary docker-compose file that includes:
    # 1. FastAPI Server (Code + Results mounted + Grafana SimpleJson endpoints)
    # 2. Grafana (Provisioned with SimpleJson datasource)

    # Create a cleaner directory structure for provisioning to avoid file-mount race conditions
    TMP_PROVISIONING_DIR="${PROJECT_ROOT}/.tmp_grafana_provisioning"
    TMP_DATASOURCES_DIR="${TMP_PROVISIONING_DIR}/datasources"
    TMP_DASHBOARDS_DIR="${TMP_PROVISIONING_DIR}/dashboards"
    TMP_JSON_DIR="${TMP_PROVISIONING_DIR}/json"
    TMP_COMPOSE_FILE="${PROJECT_ROOT}/.tmp_docker_compose_full.yml"
    
    # Force kill any existing containers that might lock files
    if command -v docker >/dev/null 2>&1; then
        docker rm -f benchmark-fastapi-wsl grafana-local >/dev/null 2>&1 || true
    fi

    # Clean contents but preserve directory if possible to help Docker Desktop binds
    mkdir -p "${TMP_PROVISIONING_DIR}"
    rm -rf "${TMP_PROVISIONING_DIR:?}/"* 2>/dev/null || force_clean_path "${TMP_PROVISIONING_DIR}"
    
    force_clean_path "${TMP_COMPOSE_FILE}"
    
    mkdir -p "${TMP_DATASOURCES_DIR}" "${TMP_DASHBOARDS_DIR}" "${TMP_JSON_DIR}"
    mkdir -p "${TMP_DATASOURCES_DIR}" "${TMP_DASHBOARDS_DIR}" "${TMP_JSON_DIR}"

    TMP_DATASOURCE_FILE="${TMP_DATASOURCES_DIR}/datasources.yaml"
    TMP_DASHBOARD_PROVIDER="${TMP_DASHBOARDS_DIR}/dashboards.yaml"
    TMP_DASHBOARD_JSON="${TMP_JSON_DIR}/default_dashboard.json"

    # Detect service type and set appropriate metric defaults
    detect_service_type

    log_info "Generating Grafana Provisioning config..."
    cat > "${TMP_DATASOURCE_FILE}" <<EOF
apiVersion: 1
datasources:
- name: BenchmarkData
  type: grafana-simple-json-datasource
  access: proxy
  url: http://fastapi-app:8000
  isDefault: true
  editable: false
EOF

    log_info "Generating Default Dashboard..."
    # Create a dashboard with 3 metric panels, each driven by a variable
    # Defaults are set based on common metrics for S3 (MinIO) and vLLM
    cat > "${TMP_DASHBOARD_JSON}" <<EOF
{
  "annotations": {
    "list": []
  },
  "editable": true,
  "graphTooltip": 0,
  "id": null,
  "links": [],
  "panels": [
    {
      "datasource": "BenchmarkData",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "custom": { "drawStyle": "line", "fillOpacity": 10, "showPoints": "auto", "spanNulls": true }
        },
        "overrides": []
      },
      "gridPos": { "h": 10, "w": 12, "x": 0, "y": 0 },
      "id": 1,
      "options": { "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        { "refId": "A", "target": "\$Metric1", "type": "timeserie" }
      ],
      "title": "Panel 1: \$Metric1",
      "type": "timeseries"
    },
    {
      "datasource": "BenchmarkData",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "custom": { "drawStyle": "line", "fillOpacity": 10, "showPoints": "auto", "spanNulls": true }
        },
        "overrides": []
      },
      "gridPos": { "h": 10, "w": 12, "x": 12, "y": 0 },
      "id": 2,
      "options": { "legend": { "displayMode": "list", "placement": "bottom" } },
      "targets": [
        { "refId": "A", "target": "\$Metric2", "type": "timeserie" }
      ],
      "title": "Panel 2: \$Metric2",
      "type": "timeseries"
    },
    {
      "datasource": "BenchmarkData",
      "fieldConfig": {
        "defaults": {}
      },
      "gridPos": { "h": 10, "w": 24, "x": 0, "y": 10 },
      "id": 3,
      "options": {
        "calculate": false,
        "cellGap": 1,
        "color": {
          "mode": "scheme",
          "scheme": "Spectral",
          "steps": 64
        },
        "yAxis": {
          "axisPlacement": "left",
          "unit": "s"
        }
      },
      "targets": [
        { "refId": "A", "target": "\$Metric3", "type": "timeserie" }
      ],
      "title": "Panel 3 (Heatmap): \$Metric3",
      "type": "heatmap"
    }
  ],
  "schemaVersion": 36,
  "style": "dark",
  "tags": ["benchmark", "generated"],
  "templating": {
    "list": [
      {
        "current": { "text": "${DASHBOARD_METRIC1}", "value": "${DASHBOARD_METRIC1}" },
        "datasource": "BenchmarkData",
        "definition": "",
        "hide": 0,
        "includeAll": false,
        "label": "Metric 1 (Throughput/RPS)",
        "multi": false,
        "name": "Metric1",
        "options": [],
        "query": "*",
        "refresh": 1,
        "regex": "",
        "sort": 1,
        "type": "query"
      },
      {
        "current": { "text": "${DASHBOARD_METRIC2}", "value": "${DASHBOARD_METRIC2}" },
        "datasource": "BenchmarkData",
        "definition": "",
        "hide": 0,
        "includeAll": false,
        "label": "Metric 2 (Concurrent/Bandwidth)",
        "multi": false,
        "name": "Metric2",
        "options": [],
        "query": "*",
        "refresh": 1,
        "regex": "",
        "sort": 1,
        "type": "query"
      },
      {
        "current": { "text": "${DASHBOARD_METRIC3}", "value": "${DASHBOARD_METRIC3}" },
        "datasource": "BenchmarkData",
        "definition": "",
        "hide": 0,
        "includeAll": false,
        "label": "Metric 3 (Latency Heatmap)",
        "multi": false,
        "name": "Metric3",
        "options": [],
        "query": "*",
        "refresh": 1,
        "regex": "",
        "sort": 1,
        "type": "query"
      }
    ]
  },
  "time": {
    "from": "now-15m",
    "to": "now"
  },
  "timepicker": {
    "refresh_intervals": ["5s","10s","30s","1m","5m"]
  },
  "refresh": "5s",
  "timezone": "",
  "title": "Benchmark Results",
  "uid": "benchmark_dashboard",
  "version": 1
}
EOF

    log_info "Generating Dashboard Provisioning config..."
    cat > "${TMP_DASHBOARD_PROVIDER}" <<EOF
apiVersion: 1
providers:
- name: 'Default'
  orgId: 1
  folder: ''
  type: file
  disableDeletion: false
  editable: true
  options:
    path: /var/lib/grafana/dashboards
EOF

    log_info "Generating Docker Compose file for full stack (FastAPI + Grafana)..."

    cat > "${TMP_COMPOSE_FILE}" <<EOF
version: '3.8'
services:
    # 1. FastAPI App Container
    fastapi-app:
        image: python:3.11-slim
        container_name: benchmark-fastapi-wsl
        working_dir: /app
        volumes:
            - "${PROJECT_ROOT}:/app"
            - "${LOCAL_RESULTS_DIR}:/data"
        environment:
            - RESULTS_DIR=/data
            - PYTHONDONTWRITEBYTECODE=1
            - PYTHONUNBUFFERED=1
        ports:
            - "${FASTAPI_PORT}:8000"
        command: >
            sh -c "
            if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi &&
            if [ -f src/Interface/fastapi_server.py ]; then
                echo 'Starting FastAPI...';
                python src/Interface/fastapi_server.py
            else
                echo 'FastAPI server script not found!';
                sleep 60;
            fi"

    # 2. Grafana
    grafana:
        image: grafana/grafana:10.4.4
        container_name: grafana-local
        environment:
            - GF_INSTALL_PLUGINS=grafana-simple-json-datasource
            - GF_SECURITY_ADMIN_PASSWORD=admin
            - GF_AUTH_ANONYMOUS_ENABLED=true
            - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
            - GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/default_dashboard.json
        volumes:
            # Mount DIRECTORIES instead of files to avoid Docker Desktop "not a directory" race conditions
            - "${TMP_DATASOURCES_DIR}:/etc/grafana/provisioning/datasources:ro"
            - "${TMP_DASHBOARDS_DIR}:/etc/grafana/provisioning/dashboards:ro"
            - "${TMP_JSON_DIR}:/var/lib/grafana/dashboards:ro"
        ports:
            - "${GRAFANA_PORT}:3000"
        depends_on:
            - fastapi-app
EOF


    
    log_info "Waiting for file synchronization (WSL -> Docker Desktop)..."
    sleep 8  # Increased from 3 to 8 for reliability

    # Choose compose command
    COMPOSE_CMD=""
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose -f ${TMP_COMPOSE_FILE}"
    else
        COMPOSE_CMD="docker compose -f ${TMP_COMPOSE_FILE}"
    fi

    log_info "Starting Docker Stack (FastAPI + Grafana) via ${COMPOSE_CMD}..."
    ${COMPOSE_CMD} up -d --remove-orphans

    if [ $? -eq 0 ]; then
        STACK_STARTED=1
        
        log_info "Waiting for Grafana to initialize (max 90s)..."
        local retries=0
        local max_retries=18
        local connected=0
        
        while [ $retries -lt $max_retries ]; do
            if curl -s "http://localhost:${GRAFANA_PORT}/api/health" | grep -q "ok"; then
                connected=1
                break
            fi
            echo -n "."
            sleep 5
            retries=$((retries+1))
        done
        echo ""

        if [ $connected -eq 1 ]; then
            log_info "--------------------------------------------------------"
            log_info "Stack running and ready!"
            log_info " -> Grafana Dashboard: http://localhost:${GRAFANA_PORT}"
            log_info "--------------------------------------------------------"
        else
            log_warn "Grafana did not respond within timeout, but container is running."
            log_info " -> Grafana Dashboard: http://localhost:${GRAFANA_PORT} (might be starting)"
        fi
        
        if [[ "${KEEP_SERVICES:-0}" == "1" ]]; then
            log_info "Services are running in Docker. Press Ctrl-C to stop and remove containers."
            # Loop specifically to keep script alive and trap signals
            while true; do sleep 1; done
        fi
    else
        log_error "Failed to start Docker stack."
        return 1
    fi
}

show_job_logs() {
    local job_id=$1
    log_info "Fetching job logs for job ${job_id}..."
    # (Logs logic same as before, abbreviated for clarity)
    ssh "${MELUXINA_HOST}" "cat ${REMOTE_SLURM_LOG_DIR}/benchmark_test_${job_id}.out 2>/dev/null" || true
    ssh "${MELUXINA_HOST}" "cat ${REMOTE_SLURM_LOG_DIR}/benchmark_test_${job_id}.err 2>/dev/null" || true
}

cleanup() {
    echo ""
    log_info "Performing cleanup..."
    
    if [[ ${STACK_STARTED} -eq 1 && -n "${TMP_COMPOSE_FILE}" && -f "${TMP_COMPOSE_FILE}" ]]; then
        log_info "Stopping Docker Compose stack..."
        if command -v docker-compose >/dev/null 2>&1; then
            docker-compose -f "${TMP_COMPOSE_FILE}" down --remove-orphans >/dev/null 2>&1 || true
        else
            docker compose -f "${TMP_COMPOSE_FILE}" down --remove-orphans >/dev/null 2>&1 || true
        fi
        
        # Explicitly kill nice and hard
        if command -v docker >/dev/null 2>&1; then
             docker rm -f benchmark-fastapi-wsl grafana-local >/dev/null 2>&1 || true
        fi

        force_clean_path "${TMP_COMPOSE_FILE}"
        force_clean_path "${TMP_PROVISIONING_DIR}"
    fi
    
    log_info "Cleanup finished."
    exit 0
}

# ------------------------------------------
# MAIN EXECUTION
# ------------------------------------------

main() {
    log_info "Starting MeluXina automation workflow (Docker Desktop/WSL Edition)"
    log_info "Project root: ${PROJECT_ROOT}"
    
    KEEP_SERVICES="${KEEP_SERVICES:-1}"
    trap cleanup INT TERM EXIT

    check_local_requirements
    setup_local_venv # Optional, mainly for rsync context or utility scripts
    
    check_ssh_connection
    sync_project_to_remote
    
    JOB_ID=$(submit_job)
    
    if ! wait_for_job "${JOB_ID}"; then
        log_error "Job failed or timed out"
        show_job_logs "${JOB_ID}"
        # Do not exit immediately, user might want to check logs
        exit 1
    fi
    
    REMOTE_WORKSPACE=$(find_latest_workspace)
    sync_results_from_remote "${REMOTE_WORKSPACE}"
    
    # Launch everything in Docker
    start_full_stack_docker
}

# Run main function
main "$@"