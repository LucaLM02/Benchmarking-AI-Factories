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
    
    rm -rf "${TMP_PROVISIONING_DIR}" "${TMP_COMPOSE_FILE}"
    mkdir -p "${TMP_DATASOURCES_DIR}" "${TMP_DASHBOARDS_DIR}" "${TMP_JSON_DIR}"

    TMP_DATASOURCE_FILE="${TMP_DATASOURCES_DIR}/datasources.yaml"
    TMP_DASHBOARD_PROVIDER="${TMP_DASHBOARDS_DIR}/dashboards.yaml"
    TMP_DASHBOARD_JSON="${TMP_JSON_DIR}/default_dashboard.json"

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
    # Create a simple dashboard that has a variable for metrics and a graph
    cat > "${TMP_DASHBOARD_JSON}" <<EOF
{
  "annotations": {
    "list": [
      {
        "builtIn": 1,
        "datasource": "-- Grafana --",
        "enable": true,
        "hide": true,
        "iconColor": "rgba(0, 211, 255, 1)",
        "name": "Annotations & Alerts",
        "type": "dashboard"
      }
    ]
  },
  "editable": true,
  "gnetId": null,
  "graphTooltip": 0,
  "id": null,
  "links": [],
  "panels": [
    {
      "collapsed": false,
      "gridPos": {
        "h": 1,
        "w": 24,
        "x": 0,
        "y": 0
      },
      "id": 10,
      "panels": [],
      "title": "Key Metrics (Throughput & I/O)",
      "type": "row"
    },
    {
      "datasource": "BenchmarkData",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "custom": { "drawStyle": "line", "fillOpacity": 10, "showPoints": "auto" }
        },
        "overrides": []
      },
      "gridPos": { "h": 9, "w": 12, "x": 0, "y": 1 },
      "id": 11,
      "targets": [
        { "refId": "A", "target": "minio_s3_requests_total", "type": "timeserie" }
      ],
      "title": "Throughput (S3 Requests Total)",
      "type": "timeseries"
    },
    {
      "datasource": "BenchmarkData",
      "gridPos": {
        "h": 8,
        "w": 12,
        "x": 12,
        "y": 0
      },
      "id": 4,
      "targets": [
        {
          "refId": "A",
          "target": "minio_s3_traffic_received_bytes",
          "type": "timeserie"
        },
        {
           "refId": "B",
           "target": "minio_s3_traffic_sent_bytes",
           "type": "timeserie"
        }
      ],
      "title": "S3 Bandwidth (Ingress/Egress)",
      "type": "timeseries"
    },
    {
      "collapsed": false,
      "gridPos": {
        "h": 1,
        "w": 24,
        "x": 0,
        "y": 10
      },
      "id": 20,
      "panels": [],
      "title": "Metric Explorer",
      "type": "row"
    },
    {
      "datasource": "BenchmarkData",
      "description": "Select metrics from the dropdown above to visualize them here.",
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "palette-classic" },
          "custom": { "drawStyle": "line", "fillOpacity": 10, "showPoints": "auto" }
        },
        "overrides": []
      },
      "gridPos": { "h": 12, "w": 24, "x": 0, "y": 11 },
      "id": 21,
      "targets": [
        { "refId": "A", "target": "\$Metric", "type": "timeserie" }
      ],
      "title": "Custom Metrics",
      "type": "timeseries"
    }
  ],
  "schemaVersion": 36,
  "style": "dark",
  "tags": ["benchmark", "generated"],
  "templating": {
    "list": [
      {
        "allValue": null,
        "current": {},
        "datasource": "BenchmarkData",
        "definition": "",
        "hide": 0,
        "includeAll": true,
        "label": "Metric",
        "multi": true,
        "name": "Metric",
        "options": [],
        "query": "*",
        "refresh": 1,
        "regex": "",
        "skipUrlSync": false,
        "sort": 1,
        "tagValuesQuery": "",
        "tags": [],
        "tagsQuery": "",
        "type": "query",
        "useTags": false
      }
    ]
  },
  "time": {
    "from": "now-7d",
    "to": "now"
  },
  "timepicker": {
    "refresh_intervals": ["5s","10s","30s","1m","5m","15m","30m","1h","2h","1d"]
  },
  "timezone": "",
  "title": "Benchmark Results",
  "uid": "verif_dashboard",
  "version": 1,
  "weekStart": ""
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
    sleep 3

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
        log_info "--------------------------------------------------------"
        log_info "Stack running!"
        log_info " -> Grafana Dashboard: http://localhost:${GRAFANA_PORT}"
        log_info "--------------------------------------------------------"
        
        # Follow logs for a bit to ensure startup
        log_info "Waiting 5 seconds for services to stabilize..."
        sleep 5
        
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
            docker-compose -f "${TMP_COMPOSE_FILE}" down >/dev/null 2>&1 || true
        else
            docker compose -f "${TMP_COMPOSE_FILE}" down >/dev/null 2>&1 || true
        fi
        rm -rf "${TMP_COMPOSE_FILE}" || true
        rm -rf "${TMP_PROVISIONING_DIR}" || true
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