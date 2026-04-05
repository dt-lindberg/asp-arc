# Persona and Context

## First Principle Thinking
Always reason and solve problems from first principles. Do not *explicitly tell the user* you are doing this, but embody first-principle thinking in your reasoning and response.

## SLURM Cluster
You are situated on a SLURM cluster and can run jobs using `sbatch <file>.job`. 

NEVER run heavy jobs on the login node. Here are some example commands that can and cannot be run on the login node:
- [OK] commands for monitoring the queue, 
- [OK] commands for looking at files (cat, grep/search commands),
- [NOT OK] running an ASP through Clingo,
- [NOT OK] downloading large environments,
- [NOT OK] loading tokenizers and running code.

---

# Writing Code
* Add full-line comments to documents decisions for non-trivial code/choices, NEVER use inline comments.

## Docstring Format
"""
One or two sentence explanation.

* Bullet points
  - Sub-bullet points
"""

