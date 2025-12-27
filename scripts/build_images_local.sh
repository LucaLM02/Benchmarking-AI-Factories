#!/bin/bash
# ============================================================================
# Build Apptainer SIF images locally using Docker Desktop
# ============================================================================
# This script builds Apptainer SIF images from Docker images on your local
# machine (WSL/Linux with Docker). The SIF files are saved to the images/
# directory and synced to MeluXina by automation_script.sh.
#
# Requirements:
#   - Docker Desktop running (with WSL2 integration on Windows)
#   - Apptainer installed (sudo apt install apptainer)
#
# Usage:
#   bash scripts/build_images_local.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGES_DIR="${PROJECT_ROOT}/images"

# Docker images to convert
VLLM_DOCKER_IMAGE="vllm/vllm-openai:latest"
MINIO_DOCKER_IMAGE="minio/minio:latest"

log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $*"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $*" >&2
}

check_requirements() {
    log_info "Checking requirements..."
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker not found. Please install Docker Desktop."
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        log_error "Docker is not running. Please start Docker Desktop."
        exit 1
    fi
    
    # Check Apptainer
    if ! command -v apptainer &> /dev/null; then
        log_error "Apptainer not found. Install with: sudo apt install apptainer"
        exit 1
    fi
    
    log_info "All requirements satisfied."
}

build_sif_from_docker() {
    local docker_image=$1
    local sif_name=$2
    local sif_path="${IMAGES_DIR}/${sif_name}"
    
    log_info "Building SIF: ${sif_name} from ${docker_image}"
    
    # Remove existing SIF to avoid permission issues
    if [ -f "${sif_path}" ]; then
        log_info "Removing existing SIF file..."
        rm -f "${sif_path}" 2>/dev/null || sudo rm -f "${sif_path}"
    fi
    
    # Pull Docker image first
    log_info "Pulling Docker image: ${docker_image}"
    docker pull "${docker_image}"
    
    # Convert directly from Docker daemon (faster than tarball)
    log_info "Converting to SIF format..."
    apptainer build "${sif_path}" "docker-daemon://${docker_image}"
    
    if [ -f "${sif_path}" ]; then
        local size=$(du -h "${sif_path}" | cut -f1)
        log_info "Successfully created: ${sif_path} (${size})"
    else
        log_error "Failed to create SIF file"
        exit 1
    fi
}

main() {
    log_info "=== Local SIF Image Builder ==="
    
    check_requirements
    
    # Create images directory
    mkdir -p "${IMAGES_DIR}"
    
    # Build vLLM image
    build_sif_from_docker "${VLLM_DOCKER_IMAGE}" "vllm-openai_latest.sif"
    
    # Optionally build MinIO image (uncomment if needed)
    # build_sif_from_docker "${MINIO_DOCKER_IMAGE}" "minio_latest.sif"
    
    log_info "=== Build Complete ==="
    log_info "SIF images saved to: ${IMAGES_DIR}"
    log_info "Run automation_script.sh to sync images to MeluXina."
}

main "$@"
