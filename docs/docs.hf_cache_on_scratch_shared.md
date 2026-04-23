# Hugging Face cache on `/scratch-shared`

## Problem

The home filesystem (`/home/dlindberg`) has a 200 GiB quota and was out of space.
The dominant consumer was `~/.cache/huggingface/hub/`, holding Qwen3.6 FP8
weights (~50 GB per checkpoint). Every invocation of the pipeline calls
`huggingface_hub.snapshot_download(...)` at `src/agent/vllm_engine.py:52`,
which — with no `cache_dir` and no `HF_HOME` set — defaults to
`$HOME/.cache/huggingface`.

## Change

Two SLURM job scripts now export an HF cache location on shared scratch before
any Python runs:

```bash
export HF_HOME=/scratch-shared/dlindberg/hf_cache
export HF_HUB_CACHE=$HF_HOME/hub
mkdir -p "$HF_HUB_CACHE"
```

Files touched:

- `install_env_qwen36.job` — pre-download step (`snapshot_download` in a heredoc).
- `run.job` — runtime pipeline (`VLLMEngine` → `snapshot_download` → vLLM load).

No Python code was changed. The existing `snapshot_download(repo_id=...,
allow_patterns=[...])` call picks up `HF_HOME` automatically, and vLLM's model
loader does the same.

## Justification

### Why `/scratch-shared` and not home, `$TMPDIR`, or `/projects`?

From the SURF Snellius filesystems wiki
(<https://servicedesk.surf.nl/wiki/spaces/WIKI/pages/85295828/Snellius+filesystems>):

| Filesystem        | Quota        | Persistence               | Verdict                                   |
|-------------------|--------------|---------------------------|-------------------------------------------|
| `/home/<user>`    | 200 GiB      | Persistent, backed up     | Too small; "not intended for large data sets" |
| `/scratch-shared` | 8 TiB        | Files >14 days auto-purged | Chosen — roomy, shared across nodes       |
| `/scratch-local`  | (node-local) | Files >6 days auto-purged | Rejected — per-node, forces re-download on every job and shorter retention |
| `/projects/<x>`   | on request   | Persistent, no auto-purge | Rejected for now — requires a SURF ticket |

`/scratch-shared` is the correct compromise: large enough, visible from every
compute node, and the 14-day purge is acceptable because the cost of a cold
miss is a one-time re-download (minutes on the cluster's network link) and
`snapshot_download` handles it transparently.

### Why `HF_HOME` (and `HF_HUB_CACHE`) and not `cache_dir=`?

- `HF_HOME` is the single umbrella env var documented by
  [`huggingface_hub`](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables#hfhome)
  and
  [`transformers`](https://huggingface.co/docs/transformers/installation#cache-setup).
  Setting it redirects the hub cache, token file, and datasets cache with one
  export.
- `HF_HUB_CACHE` is the newer, more specific override documented in
  [`huggingface_hub`](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables#hfhubcache);
  setting both is the recommended belt-and-suspenders pattern and is stable
  across library versions.
- vLLM inherits whatever the HF libraries decide — it does not own a cache
  path; it just resolves models through `huggingface_hub`.
  ([vLLM model resolution docs](https://docs.vllm.ai/en/latest/models/supported_models.html))

Passing `cache_dir=` on the `snapshot_download` call site would also work, but
would bury the path inside Python, leave `transformers`/vLLM lookups pointing
at the home cache on any code path we didn't patch, and add a config knob to
maintain. Env-var-only keeps the cache location a deployment concern, not a
code concern.

### Why `snapshot_download` handles the purge gracefully

From the
[`snapshot_download` reference](https://huggingface.co/docs/huggingface_hub/package_reference/file_download#huggingface_hub.snapshot_download):
it "downloads a whole snapshot of a repo's files" and uses the hub cache as
content-addressable storage — existing files with matching hashes are skipped,
missing files are fetched. So when `/scratch-shared` purges the cache after 14
days of inactivity, the next job sees an empty directory and repopulates it;
when the cache is warm, nothing is downloaded. This matches the "seamless"
requirement without any code.

### Why `mkdir -p "$HF_HUB_CACHE"` is inside the job script

`/scratch-shared/dlindberg` exists by default, but `hf_cache/hub/` inside it
may not on a fresh account or after a full purge. `snapshot_download` creates
subdirectories it owns but does not necessarily create the cache root on some
library versions; creating it up-front in bash is cheap and avoids a
first-run surprise.

## Out of scope

- **Cleaning up the old home cache.** `rm -rf ~/.cache/huggingface` recovers
  the home-quota space but is destructive; leaving it to the user.
- **Migrating to `/projects/<name>`.** Better long-term home for weights (no
  auto-purge) but blocked on a SURF request. Revisit if the re-download
  cadence becomes annoying.
