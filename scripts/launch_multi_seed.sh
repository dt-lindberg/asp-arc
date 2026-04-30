#!/bin/bash
# Submit N parallel SLURM jobs for the asp-arc multi-seed experiment.
#
# Usage:
#   scripts/launch_multi_seed.sh          # 5 jobs with seeds 11, 23, 47, 91, 137
#   scripts/launch_multi_seed.sh --num 5  # use 5 puzzles per seed
#   scripts/launch_multi_seed.sh --single # single job (seed=132) for testing
#
# Each job processes NUM puzzles (default 3, matches --num default).
# Results land under results/seed<seed>_<run_id>/.
#
# All jobs share the same dataset and engine config from config.py.
# Each gets its own seed and a unique run_name so outputs never collide.
#
# Environment overrides:
#   DATASET    dataset name (default: arc-v1-training)
#   NUM        puzzles per seed (default: 3)
#   ENGINE     engine label (default: nemotron-cascade-2)

set -euo pipefail

MODE="${1:---multi}"
NUM="${NUM:-3}"
DATASET="${DATASET:-arc-v1-training}"
ENGINE="${ENGINE:-nemotron-cascade-2}"
SEEDS=(11 23 47 91 137)
SINGLE_SEED="${SEED:-132}"
JOB="run_seeded.job"

case "${MODE}" in
    --multi)
        echo "Submitting ${#SEEDS[@]} multi-seed job(s)..."
        for SEED in "${SEEDS[@]}"; do
            echo "  seed=${SEED}  num=${NUM}  dataset=${DATASET}"
            sbatch \
                --job-name="ARCSeed_${SEED}" \
                --output="slurm_logs/seed_${SEED}_%A.out" \
                --export=ALL,SEED="${SEED}",NUM="${NUM}",DATASET="${DATASET}",ENGINE="${ENGINE}" \
                "${JOB}"
        done
        ;;
    --single)
        echo "Submitting single seed=${SINGLE_SEED} num=${NUM}"
        sbatch \
            --job-name="ARCSeed_Single" \
            --output="slurm_logs/seed_single_%A.out" \
            --export=ALL,SEED="${SINGLE_SEED}",NUM="${NUM}",DATASET="${DATASET}",ENGINE="${ENGINE}" \
            "${JOB}"
        ;;
    *)
        echo "Usage: $0 [--multi|--single]"
        echo ""
        echo "Environment overrides:"
        echo "  DATASET  dataset name (default: arc-v1-training)"
        echo "  NUM      puzzles per seed (default: 3)"
        echo "  ENGINE   engine label (default: nemotron-cascade-2)"
        echo "  SEED     seed for --single mode (default: 132)"
        exit 1
        ;;
esac

echo ""
echo "All jobs submitted. Watch with: squeue -u dlindberg"
