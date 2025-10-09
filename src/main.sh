#!/bin/bash -l

#SBATCH --time=00:15:00
#SBATCH --qos=default
#SBATCH --account=p200981
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --ntasks-per-node=1

module load Python/3.12.3-GCCcore-13.3.0

echo "### START benchmark on $(hostname) ###"
python main.py
echo "### END benchmark on $(hostname) ###"


