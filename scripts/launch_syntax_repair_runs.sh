#!/bin/bash
# Submit N parallel SLURM jobs for the syntax-repair experiment.
#
# Usage:
#   scripts/launch_syntax_repair_runs.sh think    # 5 jobs with thinking enabled
#   scripts/launch_syntax_repair_runs.sh nothink  # 5 jobs with thinking disabled
#
# Each job samples NUM puzzles from the full audit corpus using its seed.
# Results land under results/syntax_repair_runs/<seed>_<mode>/.

set -euo pipefail

MODE="${1:-think}"
case "${MODE}" in
    think)   THINK=true ;;
    nothink) THINK=false ;;
    *)       echo "Mode must be 'think' or 'nothink'"; exit 1 ;;
esac

SEEDS=(11 23 47 91 137)
NUM="${NUM:-20}"
# Match the audit run used by the baseline (Run 1 / Run 4 in the experiment doc),
# so we replicate the original 26.7% measurement. Override with AUDIT_RUN=... if
# you want a different pool, or set ALL_AUDITS=1 for the full corpus.
AUDIT_RUN="${AUDIT_RUN:-20260426_123318}"
ALL_AUDITS_FLAG="${ALL_AUDITS:-0}"
JOB="run_syntax_repair.job"

mkdir -p slurm_logs

for SEED in "${SEEDS[@]}"; do
    RUN_NAME="seed${SEED}_${MODE}"
    echo "Submitting seed=${SEED} thinking=${THINK} audit=${AUDIT_RUN} run_name=${RUN_NAME}"
    sbatch \
        --job-name="SyRep_${RUN_NAME}" \
        --output="slurm_logs/${RUN_NAME}_%A.out" \
        --export=ALL,SEED="${SEED}",NUM="${NUM}",RUN_NAME="${RUN_NAME}",AUDIT_RUN="${AUDIT_RUN}",ALL_AUDITS="${ALL_AUDITS_FLAG}",AGENT_THINKING="${THINK}" \
        "${JOB}"
done

echo ""
echo "All jobs submitted. Watch with: squeue -u dlindberg"
