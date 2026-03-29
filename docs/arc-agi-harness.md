---
name: ARC-AGI Harness
description: Describes the ARC-AGI harness created by Poetiq AI. Includes information of refinement loop, formatting ARC-AGI puzzles, cell diffs, feedback traces, and more. Use this when requested or when in need of inspiration for how to design an efficient ARC-AGI harness.
---

# Attributes
Link: N/A
Git (optional): https://github.com/poetiq-ai/poetiq-arc-agi-solver/tree/main?tab=readme-ov-file
Academic paper: No

**THIS WAS ORIGINALLY WRITTEN FOR PYTHON; `==` HIGHLIGHTS PYTHON SPECIFIC CONTENT AND COMMENTS MADE BY THE HUMAN; ALL CONTENT IS MEANT TO BE ADAPTED FOR ASP**

## 1. Architecture Overview
The codebase is a ~1,300-line ==Python system== that solves ARC-AGI puzzles by asking an LLM to ==**write Python code**== that performs the grid transformation, then **executing that code in a sandbox** and **iteratively refining** it based on feedback. Multiple independent solver instances ("experts") run in parallel, and their outputs are aggregated via a **voting/consensus** mechanism.
- ==Will still be a "Python system" but LLMs will write ASP code instead of Python code==

## Module Map
```
main.py                         # CLI entry point, async orchestration, scoring
arc_agi/
  solve.py                      # Thin wrapper: calls solve_parallel_coding with CONFIG_LIST
  solve_parallel_coding.py      # Multi-expert parallelism + voting/ranking
  solve_coding.py               # Core iterative solver (the main loop)
  prompts.py                    # 3 solver system prompts + 1 feedback prompt template
  llm.py                        # litellm-based async LLM caller with retries + rate limiting
  sandbox.py                    # Subprocess-based safe code execution
  config.py                     # Expert configurations (model, params, voting flags)
  types.py                      # TypedDict definitions for all data structures
  scoring.py                    # Task-level accuracy (attempt_1 OR attempt_2 vs ground truth)
  io.py                         # Build Kaggle 2-attempt submission format
  utils.py                      # canonical_test_key helper
```
- ==Must be updated with new architecture==

## Data Flow
```
main.py
  │
  ├── Loads JSON challenges (ARC-AGI format: train examples + test inputs)
  ├── For EACH puzzle, spawns async task:
  │     │
  │     └── solve() → solve_parallel_coding()
  │           │
  │           ├── Spawns N expert tasks (N=1, 2, or 8) via asyncio.gather
  │           │     │
  │           │     └── solve_coding() — the inner loop per expert
  │           │           │
  │           │           └── Up to 10 iterations:
  │           │                 1. Format puzzle as text
  │           │                 2. Append feedback from prior failures
  │           │                 3. Call LLM → get response with Python code
  │           │                 4. Parse code from markdown
  │           │                 5. Execute on ALL train examples in sandbox
  │           │                 6. If all train pass → return immediately (early exit)
  │           │                 7. Otherwise, build feedback & score, continue
  │           │
  │           └── Vote/rank across all expert results
  │                 → Return ordered list of candidate solutions
  │
  ├── build_kaggle_two_attempts() → pick top-2 candidates for submission
  ├── Score against ground truth (if available)
  └── Write cumulative JSON output
```
- ==Running experts in parallel will still work, but needs to be thought through. I will host **one model on one GPU** using vLLM, which allows us to run massive batches (batch size =~ 256). This is the only way to obtain really high throughput on a single GPU due to the memory bandwidth bottleneck (copying weights on HRAM to SRAM). One option is to simply run the same model with, for instance, batch_size=8, but this will yield relatively low throughput. A better alternative would be to run multiple puzzles simultaneously, and each puzzle makes use of multiple experts, for instance, 16 puzzles and 16 experts => batch_size=256. Another option would be to run way more expert, i.e., run one puzzle at a time but use 256 experts, i.e., 256 copies of the same model making different guesses, keep temperature high >=0.7.== 
- ==To begin with, it makes sense to just keep things as simple as possible by running one puzzle at a time (as original) with 8 experts, so batch_size=8.==

---

## 2. The Core Solving Method

## 2.1 Algorithm: LLM-Driven Program Synthesis with Iterative Refinement
This is **NOT** a DSL-based search, a neural network, or a traditional program synthesis system. The core approach is:
1. **Show the LLM the puzzle examples as text**
2. **Ask it to write a ==Python `transform(grid) → grid` function==**
	1. ==Here we'll ask it to write ASP code==
3. **Execute that function on the training examples**
	1. ==Must figure out how to "execute" the ASP on training examples==
4. **If it fails, show the LLM what went wrong and ask it to try again**
	1. ==Must figure out how to create diff from answer sets==
5. **Repeat up to 10 times**
6. **Run multiple independent solvers in parallel and vote on results**

The LLM is the **entire reasoning engine**. There is no hand-coded DSL, no search over a space of primitives, no neural network trained on ARC data, and no symbolic solver. The system relies entirely on the LLM's ability to:
- Visually reason about grid transformations from textual representations    
- Generate correct ==Python code== implementing the transformation
- Debug and refine code based on execution feedback

## 2.2 Puzzle Representation
Grids are encoded as **space-separated digit matrices** wrapped in `<Diagram>` tags:
```
Example #1
Input:
<Diagram>
0 0 1
0 1 0
1 0 0
</Diagram>

Output:
<Diagram>
1 1 1
1 1 1
1 1 1
</Diagram>
```

This is generated by `format_problem()` in `solve_coding.py:242-281`. Each cell value (0-9) is rendered as a single digit, rows are newline-separated, and values within a row are space-separated.

Key detail: **training example order is shuffled** per iteration (when `shuffle_examples=True`, which is the default). Each iteration uses a different seed (`seed + iteration_number`), so the LLM sees the examples in a different order each time. This is a diversity mechanism to get the LLM to consider different hypotheses.

## 2.3 The Role of the LLM
**The LLM is the sole solver.** The default model is ==`gemini/gemini-3-pro-preview` (Google's Gemini 3 Pro).== The system supports multiple models via litellm:
- ==My system will only support the self-hosted local model, probably qwen3-30B-A3==

| Model                         | Rate Limit | Special Config              |
| ----------------------------- | ---------- | --------------------------- |
| `gemini/gemini-3-pro-preview` | 1 RPS      | (none — default)            |
| `gemini/gemini-2.5-pro`       | 2 RPS      | thinking budget: 16K tokens |
| `openai/gpt-5`                | 1 RPS      | reasoning_effort: "high"    |
| `openai/gpt-5.1`              | 1 RPS      | reasoning_effort: "high"    |
| `anthropic/claude-sonnet-4-5` | 1 RPS      | thinking budget: 32K tokens |
| `anthropic/claude-haiku-4-5`  | 1 RPS      | thinking budget: 32K tokens |
| `xai/grok-4-fast`             | 1 RPS      | (none)                      |
| `xai/grok-4`                  | 1 RPS      | (none)                      |
| `groq/openai/gpt-oss-120b`    | 1 RPS      | (none)                      |

From `llm.py:29-39`, reasoning-capable models have their extended thinking features explicitly enabled (Claude's `thinking` parameter, Gemini's `thinking` parameter, OpenAI's `reasoning_effort`).

The LLM is called as a **single-turn user message** (no multi-turn conversation):
```python
resp = await acompletion(
    model=model,
    messages=[{"role": "user", "content": message}],
    temperature=temperature,
    ...
)
```
(`llm.py:62-69`)

Temperature is set to **1.0** (high) — this maximizes diversity of generated solutions across iterations and experts.

## 2.4 No DSL, No Search
There is explicitly **no DSL** and **no combinatorial search**. The system relies on the LLM to:
- Analyze the visual patterns in the grid
- Formulate a transformation hypothesis
- Write arbitrary ==Python code using numpy, scipy, cv2, or standard libraries==

The generated code is completely unconstrained — ==it can use any Python construct, any library ==available in the sandbox. This is pure "code generation" rather than "program synthesis from a fixed grammar."
- ==Generating ASP is also flexible, as in there's no fixed DSL, it's just a different language== 
---

## 3. The Pipeline in Detail

## 3.1 Step 1: Problem Formatting (`solve_coding.py:242-281`)
The puzzle's training examples and test inputs are converted to a text format:

```python
def format_problem(problem, shuffle=False, seed=None):
    # Optionally shuffle training example order
    if shuffle and len(train) > 1:
        rng = np.random.default_rng(seed)
        perm = rng.permutation(len(train))
        train = [train[i] for i in perm]

    # Format each training example as Input/Output diagram pairs
    for example_num, example in enumerate(train, start=1):
        example_str += f"Example #{example_num}\nInput:\n<Diagram>\n{grid_text}\n</Diagram>\n..."

    # Format test input(s) as Challenge
    for challenge_num, challenge in enumerate(test, start=1):
        challenge_str += f"Challenge #{challenge_num}\nInput:\n<Diagram>\n{grid_text}\n</Diagram>\n"
```

## 3.2 Step 2: Prompt Construction (`solve_coding.py:57-71`)
The system prompt is one of three variants (SOLVER_PROMPT_1, 2, or 3), with `$$problem$$` replaced by the formatted problem. If there are previous failed solutions, a feedback block is appended.

```python
message = _build_prompt(solver_prompt, problem=problem_str)

if selected:  # previous solutions exist
    examples_block = create_examples(selected, max_examples=max_solutions, improving_order=improving_order)
    message += "\n\n" + _build_prompt(feedback_prompt, feedback=examples_block)
```

The feedback selection is **probabilistic**: each previous solution is included with probability `selection_probability` (default: 1.0, meaning all are included). Up to `max_solutions` (default: 5) previous attempts are shown, sorted by score in **improving order** (worst-to-best, so the LLM sees the best attempt last).

## 3.3 Step 3: LLM Call (`llm.py:42-140`)
An async call to the LLM via litellm with:
- Rate limiting (1-2 RPS per model)
- Retry on rate limits, server errors (infinite retries for transient errors)
- Retry on timeouts/other errors (up to `per_iteration_retries`, default 2)
- Global budget tracking: `max_total_timeouts` (15 per problem) and optional `max_total_time`

## 3.4 Step 4: Code Parsing (`solve_coding.py:208-210`)
==The LLM response is parsed for a Python code block:==
```python
def _parse_code_from_llm(response: str) -> Optional[str]:
    m = re.search(r"```python\s*(.*?)```", response, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None
```
- ==Must look for ASP / Prolog blocks, i.e. "asp/ASP/Prolog/PROLOG"==
If no code block is found, the iteration is skipped (continues to next).

## 3.5 Step 5: Sandbox Execution (`sandbox.py:8-48`)
The extracted code is wrapped into a complete script and executed in a subprocess:

```python
def _build_script(code: str) -> str:
    return f"""
{code}
if __name__ == '__main__':
    import json, numpy as np, scipy
    from sys import stdin
    data = json.load(stdin)
    res = transform(np.array(data['input']))
    print(json.dumps({{"ok": True, 'result': res.tolist()}}))
"""
```

Execution details:
- **Runs in a temporary directory** (tempfile.TemporaryDirectory)
- **Timeout: 1.5 seconds** per execution (configurable via `timeout_s`)
	- ==This is not enough for ASP, timeout will have to be at least 1 minute but probably more==
- **Input**: JSON via stdin with the grid as `{"input": [[...]]}`    
- ==**Deterministic**: `PYTHONHASHSEED=0` environment variable==
- Each training example and each test input is executed **separately** (one subprocess per grid)

## 3.6 Step 6: Evaluation (`solve_coding.py:293-332`)
For each training example, the sandbox output is compared to the ground truth:
- **Exact match**: shape must match AND all values must be equal (`np.array_equal`)
- **Soft score**: fraction of cells that match (0.0 to 1.0), used for ranking

```python
success = bool(arr.shape == truth.shape and np.array_equal(arr, truth))
soft = _soft_score(arr, truth)  # np.mean(pred == truth)
```

==It's unclear how exactly to go from the produced answer sets into a coherent grid we can evaluate. The only reasonable method here might be to fix the names of output cells so that we can deterministically extract the output grid from the answer sets. Otherwise, we'd have to dynamically figure out which facts/predicates corresponds to the output grid==
- ==Whatever we do, we can cast this back into the pipelines output grid representation for downstream compatibility==

## 3.7 Step 7: Early Exit or Feedback (`solve_coding.py:104-114`)
If **ALL training examples pass** (exact match on every one):
```python
if all(r["success"] for r in train_res):
    return ARCAGIResult(train_results=train_res, results=test_res, iteration=it + 1, ...)
```
→ **Immediate return** with the test outputs from this code. No further iterations needed.

If not all training examples pass, build feedback and continue:
```python
feedback, score = _build_feedback(train_res, train_in, train_out) solutions.append(ARCAGISolution(code=code, feedback=feedback, score=score))
```

## 3.8 Step 8: Feedback Construction (`solve_coding.py:343-409`)
The feedback is rich and detailed. For each training example:
- If correct: `"Solves Example #N correctly."`
- If wrong shape: reports shape mismatch (e.g., `"Shape mismatch: your prediction's shape was (3, 3), while the correct shape was (5, 5)."`)
- If wrong values (same shape): shows a **diff grid** where correct values are shown as-is and incorrect values are shown as `prediction/correct`:

```
Correctly predicted values are shown as-is while the incorrectly predicted values
are shown in the format 'prediction/correct':

1 0/1 0
0 1 0
0/1 0 1

Output accuracy: 0.67 (0 is worst, 1 is best).
```

This diff visualization (`_array_diff` at line 194-205) is a key design choice — it gives the LLM cell-level feedback on what went wrong.

Errors from the sandbox (runtime errors, parse failures) are also included in the feedback.
- ==This parts contains a lot of potential for ASP. Here, we could make use of detailed feedback about the number of answer sets produced, what they look like, if the program is overconstrained, and of course more straight forward, if there are syntactical issues==
- ==The LLMs I'm using often produced syntactical issues so we'll have to surface these as well==
- ==Connect this to the 'solver in the loop' paper==

## 3.9 Step 9: Iteration Loop Control
The loop runs for up to `max_iterations` (default: 10) iterations. After the loop ends without a perfect train match, the system returns either:
- The **best result** (highest train score across all iterations) if `return_best_result=True` (default)
- The **last result** otherwise

---

## 4. Multi-Expert Voting System (`solve_parallel_coding.py`)

## 4.1 Parallel Experts
Multiple experts run **the same algorithm** with the same model and config, but with **different random seeds**:
```python
for it, cfg in enumerate(expert_configs):
    cfg["seed"] += it * cfg["max_iterations"]
```

This ensures each expert shuffles training examples differently and (via temperature=1.0) gets different LLM responses. The experts run concurrently via `asyncio.gather`.

Three configurations are documented:
- **Poetiq(Gemini-3-a)**: 1 expert (no voting)
- **Poetiq(Gemini-3-b)**: 2 experts
- **Poetiq(Gemini-3-c)**: 8 experts

## 4.2 Voting/Ranking Algorithm (New Voting Mode)
After all experts finish, their results are grouped by **identical test outputs**:
```python
key = canonical_test_key(res.get("results", []))  # stringified list of outputs
```

Results are partitioned into:
- **Passers**: experts where ALL training examples were solved correctly
- **Failures**: experts where at least one training example failed

The ranking algorithm (`use_new_voting=True`, the default):
1. **Merge failures into passers** if their test outputs match a passing solution (`count_failed_matches=True`). This boosts the vote count of passing solutions that non-passing runs also converged to.
2. **Sort passer groups by vote count** (descending) — the group with the most experts agreeing on the same test output is ranked first.
3. **Optional iteration tiebreak** (`iters_tiebreak`): within groups of equal vote count, prefer solutions found in fewer iterations (earlier convergence suggests higher confidence). Disabled by default.
4. **Diversity-first selection**: take **one representative** from each passer group (ordered by votes), then one from each failure group (ordered by votes, tie-broken by soft score).
5. **Remaining members** appended after: remaining passers, then remaining failures.

The final ordered list is fed to `build_kaggle_two_attempts()`, which takes the **top 2 distinct solutions** as `attempt_1` and `attempt_2` for the Kaggle submission.

## 4.3 Why Voting Helps
Different experts with different random seeds and ==temperature=1.0== will often generate different code. When multiple independent experts converge on the **same test output**, it's strong evidence that the output is correct. This is essentially a **self-consistency** / **majority voting** approach applied to code generation.
- ==Will consider using a lower temperature==

---

## 5. Prompting Strategies
## 5.1 Three Prompt Variants
All three prompts ask the LLM to write a ==`transform(grid: np.ndarray) -> np.ndarray`== function. They differ in style and detail:

**SOLVER_PROMPT_1** (`prompts.py:1-145`):
- Most detailed, with 3 worked examples (border removal, grid reversal, center pixel)
- Step-by-step methodology: Analyze → Hypothesize → Implement → Test → Output
- Suggests object manipulation, color changes, spatial arrangements, addition/removal
- Examples use `[[...]]` list-of-lists format

**SOLVER_PROMPT_2** (`prompts.py:147-223`):
- Emphasizes "world-class expert" and "do not give up"
- Iterative process: Initial Analysis → Iterative Testing → Coding Guidelines → Output
- Mentions cv2 (OpenCV) as available
- 1 worked example using `<Diagram>` format
- More emphasis on symmetry analysis

**SOLVER_PROMPT_3** (`prompts.py:225-365`):
- Nearly identical to SOLVER_PROMPT_2 but with 3 worked examples instead of 1
- Adds "The code should be as concise as possible"
- Uses `<Diagram>` format in examples

The default config uses **SOLVER_PROMPT_1**.

- ==This is another interesting part of the process. These prompts will obviously have to be revised for ASP. It's interesting to think if we could make use of the prompting pipeline introduced by Ishay et al. here. Of course, their pipeline assumes that the LLM is given a grid-puzzle described in natural language with hints (constraints), but their might be interesting lessons to be drawn from their work nevertheless==
	- ==Maybe specific examples/instructions can be re-used. Maybe we can still do some of the iterative processing like first defining constants and predicates that might be useful, and then writing the generation (choice rules), and then the integrity constraints==
	- ==The question is how this would feed back into the refinement loop, do we have to go over all of these steps again or do we only go over the program and keep these fixed?==

## 5.2 Feedback Prompt (`prompts.py:367-373`)
```
**EXISTING PARTIAL/INCORRECT SOLUTIONS:**

Following are some of the best, though not completely correct, solutions so far.
For each solution, its code, corresponding feedback regarding its output on the
example problems, and a numeric score between 0. (worst) and 1. (best) indicating
the quality of outputs is also provided. Study these solutions and corresponding
feedback and produce a new solution fixing all the issues.
```

Each previous solution is wrapped in XML-like tags:

```xml
<solution_1>

<solution_code> 
```python 
{code}
</solution_code>  

<solution_evaluation>  
{feedback with diff grids}  
</solution_evaluation>  

<solution_score>  
0.67  
</solution_score>  

</solution_1>
```

### 5.3 Key Prompting Design Choices
1. **Single-turn, not multi-turn**: Each iteration is a fresh prompt, not a conversation. The entire context (puzzle + all feedback) is sent as one user message.
2. **Feedback is accumulated**: Up to 5 best previous solutions are shown each iteration, sorted worst-to-best (`improving_order=True`). The LLM sees progressively better attempts, with the best one last.
3. **Stochastic feedback selection**: Each previous solution is included with probability `selection_probability` (default 1.0, so all are included). This parameter could introduce diversity by randomly omitting some feedback.
4. **Example shuffling**: Training examples are shown in a different random order each iteration, preventing the LLM from fixating on a particular example ordering.

---

## 6. Test-Time Compute and Iterative Refinement
This system is a clear example of **test-time compute scaling**:
- **Per expert**: Up to 10 LLM calls per puzzle (iterations), each potentially with extended thinking (32K tokens for Claude, 16K for Gemini 2.5)
- **Per puzzle**: Up to 8 experts × 10 iterations = 80 LLM calls
- **Feedback loop**: Each iteration builds on ALL previous failed attempts, with rich cell-level diff feedback
	- ==This might blow the small LLMs context out of the water very quickly. Will need to check what amount of context is reasonable here==
- **Early termination**: If a perfect training solution is found, the expert stops immediately

The total compute budget per puzzle can be enormous:
- 8 experts × 10 iterations × (large prompt + long response) = massive token consumption
- With reasoning models, the internal "thinking" tokens add even more compute
- ==Super long context might also explode the KV-cache, with heavy batch processing this might become an issue; I think we can use some ideas of theoretical guarantees and empirical testing to deduce what is a good limit here==

This is the core insight: **throw massive test-time compute at each puzzle** via repeated LLM calls with feedback, and use voting across independent runs to filter for correct answers.

---
## 7. Grid/Pixel Encoding for the LLM
The encoding is deliberately simple — space-separated digits:
```python
def _example_to_diagram(example):
    """Converts an ARC-AGI example (list of lists) to a diagram (ascii grid)."""
    diagram = ""
    for row in example:
        row_str = " ".join([str(col) for col in row]) + "\n"
        diagram += row_str
    return diagram[:-1]  # Strip final newline
```

For a 3×3 grid with values 0-9:
```
0 1 2
3 4 5
6 7 8
```

Wrapped in `<Diagram>` tags within the prompt. No color names, no pixel art, no images — just raw integer values. The LLM must "see" the spatial patterns from these digit grids.

---
## 8. Ensembling, Voting, and Retry Mechanisms

## 8.1 Retry Mechanisms (per expert)
- **LLM call retries**: Transient API errors (rate limits, server errors) are retried indefinitely with 5-second delays. Non-transient errors use `per_iteration_retries` (default: 2).
- **Timeout budget**: Each expert gets up to 15 timeouts per problem (`max_total_timeouts`).
- **Failed code parsing**: If no ==Python code== block is found in the LLM response, the iteration is silently skipped.
- **Sandbox failures**: Execution errors, timeouts, or parse failures become feedback for the next iteration.

## 8.2 Ensembling via Voting
The system uses a **self-consistency** approach:
1. Run N independent experts (same model, different seeds)
2. Group results by identical test outputs
3. Rank by consensus (vote count)
4. Select top-2 for submission

This is conceptually similar to "sample N times and take the majority vote" but applied to code outputs rather than direct answers.

## 8.3 Two-Attempt Submission
The Kaggle/ARC-AGI format allows **2 attempts per test input**. The system fills these with the top-2 ranked solutions:

```python
def build_kaggle_two_attempts(results, test_in):
    for ar in results:
        grid = _coerce_grid(rr.get("output", []))
        if grid != []:
            attempts.append(grid)
            if len(attempts) == 2:
                break
    return {"attempt_1": attempts, "attempt_2": attempts}
```

This means the diversity-first ranking matters: attempt_1 gets the highest-voted solution, attempt_2 gets the next-best _different_ solution.

---

## 9. Performance Claims
From the README:

> This repository allows reproduction of **Poetiq's** record-breaking submission to the ARC-AGI-1 and ARC-AGI-2 benchmarks.

> Our method is now on top of the official leaderboard.

The README references two blog posts:
- "Traversing the Frontier of Superintelligence" (launch post)
- "Poetiq Shatters ARC-AGI-2 State of the Art at Half the Cost" (verified results)

The repository includes screenshots of leaderboard results (`officialtable_boxed.png`, `arc2captured.png`) and performance charts (`arcagi1.png`, `arcagi2.png`).

The charts show results across multiple models (Gemini 3, GPT-5, Claude, Grok) on ARC-AGI-1 and ARC-AGI-2, with the Gemini 3 configurations being reproducible from this repo.

---
## 10. Summary of Key Design Decisions

| Decision               | Choice                                     | Rationale                                                           |
| ---------------------- | ------------------------------------------ | ------------------------------------------------------------------- |
| **Solving approach**   | LLM writes ==Python code==                 | Unconstrained — can express any transformation                      |
| **LLM role**           | Sole reasoning engine                      | No hand-coded heuristics or DSL                                     |
| **Code execution**     | Sandboxed subprocess, 1.5s timeout         | Safe, deterministic, ==fast== (==might actually be slow with ASP==) |
| **Feedback format**    | Cell-level diff grid + accuracy score      | Precise signal for refinement                                       |
| **Conversation style** | Single-turn (fresh prompt each iteration)  | Avoids context accumulation issues                                  |
| **Temperature**        | 1.0                                        | Maximizes diversity across iterations/experts                       |
| **Max iterations**     | 10 per expert                              | Budget for refinement                                               |
| **Experts**            | ==1/2/8 parallel instances==               | Voting improves accuracy                                            |
| **Voting**             | Group by identical outputs, rank by count  | Self-consistency filtering                                          |
| **Example ordering**   | Shuffled per iteration                     | Prevents fixation on one ordering                                   |
| **Feedback ordering**  | Worst-to-best                              | LLM sees best attempt last                                          |
| **Grid encoding**      | Space-separated digits in `<Diagram>` tags | Simple, unambiguous                                                 |
| **Attempts**           | 2 per test input                           | Diversity-first ranking fills both                                  |
| **Default model**      | ==Gemini 3 Pro Preview==                   | Best performance per the leaderboard                                |

---
## 11. What This System Is NOT
- **NOT a neural network trained on ARC**: No training or fine-tuning on ARC data
- **NOT a DSL-based program synthesizer**: No fixed grammar of primitives
- **NOT a search-based solver**: No enumeration over transformation spaces
- **NOT using vision/image input**: Grids are text, not images
- **NOT multi-turn conversational**: Each iteration is an independent single-turn prompt
- **NOT using retrieval/RAG**: No database of known transformations

It is, fundamentally, **a wrapper around an LLM that asks it to write code, runs that code, shows the LLM what went wrong, and repeats** — scaled up with parallel experts and voting. The elegance is in the simplicity.
