#!/bin/bash -l   

#SBATCH --time=00:15:00
#SBATCH --qos=default
#SBATCH --partition=gpu
#SBATCH --account=p200981   # Your project ID
#SBATCH --nodes=1           # Number of nodes
#SBATCH --ntasks=1          # Number of tasks
#SBATCH --ntasks-per-node=1 # Tasks per node
#SBATCH --output=%j.out
#SBATCH --error=%j.err

# Print job info
echo "Date              = $(date)"
echo "Hostname          = $(hostname -s)"
echo "Working Directory = $(pwd)"

# Load necessary modules
module add Apptainer

# Pull and run the container
apptainer pull docker://ollama/ollama
apptainer exec --nv ollama_latest.sif ollama serve

