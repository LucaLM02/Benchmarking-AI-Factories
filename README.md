# Benchmarking-AI-Factories

**EUMaster4HPC Student Challenge 2025-2026**  
**Benchmarking AI Factories on MeluXina Supercomputer**

This repository contains design documents, source code, logs, benchmarking framework, monitoring dashboards, and final reports related to the challenge **"Benchmarking AI Factories on MeluXina"**.  
The goal is to develop a **unified benchmarking framework** capable of evaluating the performance and scalability of different **AI Factory components** (storage, inference, vector databases, orchestration, and monitoring) using the **MeluXina supercomputer**.

---

## 🎯 Objectives

- **Design and implement** a modular benchmarking framework for AI Factory workloads.
- **Evaluate and compare** storage, inference, and retrieval systems under realistic HPC-scale conditions.
- **Develop reusable benchmarking tools** with modular architecture (Python + Slurm + Apptainer).
- **Produce performance insights**, dashboards, and recommendations for future AI Factory designs.

---

## 🧩 Components Tested
The benchmark targets the following AI Factory building blocks:
- **Storage systems** (File systems, Object storage such as MinIO/S3)
- **Relational databases** (PostgreSQL, etc.)
- **Inference servers** (Triton, vLLM)
- **Vector databases** (Chroma, Faiss, Milvus, Weaviate)
- **Monitoring and orchestration** (Prometheus, Grafana, Slurm)

Each component is tested independently and as part of an **end-to-end AI pipeline**, with metrics such as **throughput, latency, scalability, and resource utilization** collected automatically.

---

## ⚙️ Methodology

1. Define benchmark **recipes** in YAML format describing:
   - Services and clients (CPU/GPU, nodes, execution mode)
   - Monitors and loggers
   - Execution parameters (warmup, duration, replicas)
   - Reporting and notification settings

2. Run benchmarks on **MeluXina HPC** using **Apptainer containers** orchestrated via **Slurm**.

3. Collect metrics and logs into unified dashboards (Prometheus + Grafana) and generate CSV/PDF reports.

4. Analyze results to provide insights on performance scaling and system bottlenecks.

## 🧩 UML Design

The following diagram shows the class design of the Benchmarking-AI-Factories framework:

![Benchmark Design Diagram](docs/design.png)

## 🚀 Quick Tutorial – How to Run the Benchmark on MeluXina

Follow these steps to reproduce or execute a benchmark.

### 1️⃣ Access MeluXina
Connect to MeluXina using your assigned credentials:
```bash
ssh <your-user-ID>@login.lxp.lu -p 8822 -i ~/.ssh/id_ed25519_mlux

```

### 2️⃣ Allocate a compute node
Request an interactive compute node

### 3️⃣ Load required module
```bash
module add Apptainer

```

### 4️⃣ Clone this repository

git clone https://github.com/LucaLM02/Benchmarking-AI-Factories.git
cd Benchmarking-AI-Factories

```
### 5️⃣ Configure project information
Edit the run_benchmark.sh file to set your MeluXina project ID:
```bash
PROJECT_ID="pxxxxxx"

```

### 6️⃣ Run the benchmark
```bash
apptainer run \
  --bind $(pwd):/workspace:ro \
  --bind ${WORKSPACE}:/output:rw \
  benchmark.sif \
  --load /workspace/Recipes/Meluxina_DataIngestionRecipe.yaml \
  --workspace /output \
  --run

```

### 7️⃣ Check the job status

```bash
squeue -u $USER

```



