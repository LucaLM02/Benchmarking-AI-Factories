#!/bin/bash
# ============================================================================
# Build Apptainer SIF images on MeluXina cluster
# ============================================================================
# This script builds Apptainer SIF images directly on MeluXina login nodes.
# Use this if you don't have Docker/Apptainer locally.
#
# Usage:
#   1. SSH into MeluXina: ssh meluxina
#   2. Navigate to project: cd /project/scratch/p200981/$USER/benchmarks/Benchmarking-AI-Factories
#   3. Run: bash scripts/pull_images_meluxina.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGES_DIR="${PROJECT_ROOT}/images"

# Docker images to convert
VLLM_DOCKER_IMAGE="docker://vllm/vllm-openai:latest"
MINIO_DOCKER_IMAGE="docker://minio/minio:latest"

log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $*"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $*" >&2
}

check_environment() {
    log_info "Checking MeluXina environment..."
    
    # Load Apptainer module if available
    if command -v module &> /dev/null; then
        module load Apptainer 2>/dev/null || true
    fi
    
    if ! command -v apptainer &> /dev/null; then
        log_error "Apptainer not found. Try: module load Apptainer"
        exit 1
    fi
    
    log_info "Apptainer version: $(apptainer --version)"
}

build_sif() {
    local docker_uri=$1
    local sif_name=$2
    local sif_path="${IMAGES_DIR}/${sif_name}"
    
    log_info "Building SIF: ${sif_name}"
    log_info "Source: ${docker_uri}"
    
    # Remove existing SIF to avoid issues
    if [ -f "${sif_path}" ]; then
        log_info "Removing existing SIF file..."
        rm -f "${sif_path}"
    fi
    
    # Set temp directory for large builds
    export APPTAINER_TMPDIR="${PROJECT_ROOT}/.apptainer_tmp"
    mkdir -p "${APPTAINER_TMPDIR}"
    
    # Build SIF from Docker Hub
    log_info "Pulling and converting (this may take 10-30 minutes)..."
    apptainer build "${sif_path}" "${docker_uri}"
    
    # Cleanup temp
    rm -rf "${APPTAINER_TMPDIR}"
    
    if [ -f "${sif_path}" ]; then
        local size=$(du -h "${sif_path}" | cut -f1)
        log_info "Successfully created: ${sif_path} (${size})"
    else
        log_error "Failed to create SIF file"
        exit 1
    fi
}

main() {
    log_info "=== MeluXina SIF Image Builder ==="
    
    check_environment
    
    # Create images directory
    mkdir -p "${IMAGES_DIR}"
    
    # Parse arguments
    BUILD_VLLM=false
    BUILD_MINIO=false
    
    if [ $# -eq 0 ]; then
        BUILD_VLLM=true  # Default to vLLM only
    else
        for arg in "$@"; do
            case $arg in
                vllm) BUILD_VLLM=true ;;
                minio) BUILD_MINIO=true ;;
                all) BUILD_VLLM=true; BUILD_MINIO=true ;;
                *) log_error "Unknown argument: $arg. Use: vllm, minio, or all"; exit 1 ;;
            esac
        done
    fi
    
    # Build requested images
    if [ "$BUILD_VLLM" = true ]; then
        build_sif "${VLLM_DOCKER_IMAGE}" "vllm-openai_latest.sif"
    fi
    
    if [ "$BUILD_MINIO" = true ]; then
        build_sif "${MINIO_DOCKER_IMAGE}" "minio_latest.sif"
    fi
    
    log_info "=== Build Complete ==="
    log_info "SIF images saved to: ${IMAGES_DIR}"
    log_info ""
    log_info "Available images:"
    ls -lh "${IMAGES_DIR}"/*.sif 2>/dev/null || echo "  (none)"
}

main "$@"
