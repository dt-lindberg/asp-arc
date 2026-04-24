# Autonomous ASP Synthesis Research
You have been tasked with autonomously improving the performance of an iterative Answer Set Programming (ASP) synthesis pipeline that aims to solve complex logical puzzles - namely, puzzles from the Abstraction and Reasoning Corpus (ARC-AGI).

---

## Setup
You are on a GPU cluster host called Snellius as user 'dlindberg'. You do NOT have root permissions and can only act in the directory `/home/dlindberg/`. It runs a Slurm manager.

### Important Cluster Etiquette
* To run Bash commands for monitoring and/or streaming outputs, **use background processes**. For example, to monitor when a job finishes.
* The login node is only intended for super-light jobs. 
  - Examples of acceptable processes to run on the login node: monitoring jobs and outputs, copying, editing, and moving files.
  - Examples of unacceptable to run on login node: scripts that load tokenizers, that download large packages, or CPU-intensive tasks like Clingo compilation (submit cpu-jobs for such tasks if needed).

---

## Experimentation
Each experiment runs on a single Nvidia H100 (80GB) GPU with access to 16 CPU cores. The script runs for a **fixed time budget of 1 hour** (wall clock time, including startup/compilation). You launch it with `sbatch`.
* Note: sometimes it takes a bit of time for the job to *actually start*, as there might be a queue for the GPUs. This does NOT count towards the 10 minutes.

The repository is using a local LLM from Nvidia called Nemotron-Cascade-2-30B-A3, it is a highly efficient MoE reasoning model. Its environment is already configured in .venv and is used by the existing pipeline.

### What you CAN do
- Your goal is to tweak and modify the pipeline around the LLM to improve its performance (measured in number of ARC-AGI puzzles solved using ASP).
- Modify code, prompts, add tools, refinement procedures, sub-agents, run multiple experts, etc...
- Search the web for (recent) information, documentation, and inspiration.

If you need inspiration, inspect the `feature/agentic-nemotron` branch. It implements some tools, tailored syntax repairs, and multiple expert agents.

### What you CANNOT do
- Modify the evaluation harness. Each experiment is to be evaluated on how well it solves ARC-AGI puzzles.
- Modify the underlying LLM, you are stuck using the Nemotron model; work around its limitations.
- Change the run-time. Each experiment can take at most 1 hour, this is to keep the rate of iteration high.
- Remove the use of ASP to solve the puzzles. This is the whole point, to make a pipeline that is as good as possible in solving ARC-AGI puzzles **using ASP**.

**The goal is simple**: Get the highest score possible, where the score is defined as "number of puzzles solved".

### Simplicity criterion
All else being equal, simpler is better. A small improvement that adds ugly complexity is not worth it. Conversely, removing something and getting equal or better results is a great outcome — that's a simplification win. When evaluating whether to keep a change, weigh the complexity cost against the improvement magnitude.
* One more puzzle solved that adds many experts and refinement iterations => probably not worth it.
* One more puzzle solved from deleting code => definitely keep.
* No improvement but much simpler code => definitely keep.

### Progress file
Keep a table that tracks the results of your experiments in a progress file at the root. **Make sure it is NOT tracked by Git**. With each experiments, add its commit hash, one-sentence description, performance, and the status (i.e., should the change be kept, removed, or if it crashed). For example:
```
commit, description, score, status
c3d4ef1,"baseline",0,keep
a1b2c3d,"Added edit-code tool",3/10,keep
d8e7c1a,"Added 3 more refinement attempts",3/10,remove
c3d4e5f,"Added 100 diverse experts (Out Of Memory)",0,crash
```

---

## The experiment loop
The experiment runs on a dedicated branch (e.g. `autoresearch/apr24`).

**LOOP FOREVER**:

1. Look at the git state: the current branch/commit we're on.
2. Tune the code-base with an experimental idea by directly hacking the code.
3. `git commit` your changes.
4. Run the experiment: `sbatch run.job`.
5. Monitor the run, do NOT monitor or read the raw (stdout) of runs, do NOT let redundant information flood your context.
6. Check the results in `outputs` or `audit` and record them in the progress file.
7. If the performance increased sufficiently, you 'advance' the branch by keeping the commit.
8. If the performance decreased, revert the changes you made to the code (*keeping the untracked progress file!*).

The idea is that you are a completely autonomous researcher trying things out. If they work, keep. If they don't, discard. And you're advancing the branch so that you can iterate. If you feel like you're getting stuck in some way, you can rewind but you should probably do this very very sparingly (if ever).

**Crashes**: If a run crashes (OOM, or a bug, or etc.), use your judgment: If it's something dumb and easy to fix (e.g. a typo, a missing import), fix it and re-run. If the idea itself is fundamentally broken, just skip it, log "crash" as the status in the progress file and move on.

**NEVER STOP**: Once the experiment loop has begun, do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep, or gone from a computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — research online, re-read the in-scope files for new angles, try combining previous near-misses, try more radical architectural changes. The loop runs until the human interrupts you, period.

GOOD LUCK!
