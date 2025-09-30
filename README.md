# Benchmarking-AI-Factories
EUMaster4HPC Student Challenge 2025-2026 - Benchmarking AI Factories on MeluXina supercomputer. This repository contains design documents, logs, source code, benchmarking framework, monitoring dashboards, and final reports.

## Design Proposal

src/
├─ SLURMs/          
│  ├─ job_1.sh
│  ├─ job_2.sh
│  └─ ...
├─ jobs/       
│  ├─ job_1.py
│  ├─ job_2.c
│  └─ ...
├─ out/              # .out/.err files
├─ config.yaml       # parameters for each job
├─ UI.py             # master script to a) configure jobs b) run jobs c) visualize .out/.err files