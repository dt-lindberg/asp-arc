# Background
You are working on investigating whether LLMs can generate correct Answer Set Programming (ASP) code to solve logic puzzles.

You live on a SLURM cluster, which means you cannot run jobs here on the login node, anything that requires more processing than inspecting files and moving some stuff around should be submitted using the `sbatch` command on `.job` files that specify the hardware requirements.
- These are already pre-defined in existing repository, can just copy-paste.

# Goal
The goal is to create a first implementation of a repository that solved ARC-AGI puzzles by having LLMs generate code in Answer Set Programming (ASP). 

This first implementation is meant to be simple and build on the existing repository. The overarching goal is to test the hypothesis: "*Extracting constants, then predicates, then choice rules and lastly constraints boosts the LLMs ability in solving ARC puzzles*".

- Let's basically re-use the prompts from `asp-gen-refinements` (see below) that are relevant, with tweaks necessary for the ARC puzzles, e.g., the puzzle formatting.
- Store all intermediate data as jsons in a simple but effective structure. (intermediate data includes the puzzle ID, LLMs input's, thinking, response, ASP errors, etc etc.)
- The goal is to have a first draft that is familiar to the existing code-base.
- You should not evaluate your progress by how many puzzles the LLM solves, but by how well the pipeline functions. The evaluation is looking into the pipeline and making sure that everything makes sense; the input the LLM receives, the responses it returns, the errors caught and surfaced by Clingo, how they're packaged into prompts, the puzzle grid-diff, the eval script for puzzles, etc.

## Philosophy/Logistics
There are some frameworks, existing work, scaff-holding, code, and theory that should guide this process. Everything should be documented, runs must be tracked, results stored, and automatic evaluations must guide what's working an what's not working.

- You are working on a git branch, commit your changes with descriptive commit messages.
## Prior Work
### ASP Generation with Refinements
`../asp-gen-refinements/` defines the precursor work from which this one builds. 

It's an extended implementation of [Leveraging Large Language Models to Generate Answer Set Programs](https://proceedings.kr.org/2023/37/kr2023-0037-ishay-et-al.pdf). [Lab page](https://azreasoners.github.io/ARG-webpage/)

The original pipeline prompts an LLM to translate natural-language logic puzzles into Answer Set Programming (ASP) rules, then evaluates them with Clingo. This fork extends it with support for additional LLM backends, an automatic refinement loop that corrects syntactic and semantic errors, and a Streamlit interface for inspecting results.

It's using a vLLM backend to run Qwen3-30B-A3 (4bit quantized) using batched inference. The batched inference is key in order to achieve high throughput. There are requirements and detailed instructions on how to run it, the whole repository defines everything that is needed for this model. Centralized configuration file specifies variables like context length and max generated tokens per sequence.

- The `../asp-gen-refinements/` should act as a place to begin building from. I expect lots of files to be useful for this repo as well, for instance:
	- logger.py
	- requirements.txt / requirements_vllm.txt
	- run.job
	- think_logits_processor.py
	- vllm_engine.py
	- and main, pipeline, refinement_loop can be used for inspiration, but adapted to the new purpose.

### ARC-AGI harness
`arc-agi-harness.md` is inspired from prior work. There is lots of content in here, not all of it is applicable, here's what I want to take from it:
- Puzzle representation in prompt and puzzle diff viewer
	- With that, problem and prompt formatting
- Having an automatic evaluation script

## Loading ARC-AGI puzzles 
Stored in `./arc-puzzles`, both ARC-AGI-1 and 2 from the official git repositories. Data format is JSON files: `{"train": [{"input": grid, "output": grid}], "test": [...]}`.

### ASP encodings of ARC-AGI puzzles
ARC-AGI puzzles should have some fixed encodings. For example, the predicate that the model should output will always be:
- `output(X, Y, C)` where X and Y indicate the cell coordinate and C the colour.
- The choice rule that generates the search space is almost always the same, it's always choosing exactly 1 colour for each possible output cell, e.g. something like: `1 {output(X, Y, C) : C} 1 :- Output_cell(X, Y).` What might change is the dimensions of the output grid, so the LLM should be allowed to specify the expected output dimension size.

### Using input-output examples as labels
The nature of the ARC-AGI puzzles admits one to verify programs, like the ASP code, on the given input-output examples. For instance, if the LLM produces program P, that takes as input some puzzle grid, and outputs an answer set of output cells (representing the output grid), we can run program P on the input-output examples we've been given. There are usually 2-3 examples per puzzles, but sometimes even more.

This provides a strong signal if the program is correct or not. Mistakes on the given examples can surface errors with the program before it is submitted on the test instance and evaluated.

# Tasks to get started
- Run a sub-agent or two on the `../asp-gen-refinements/` repository to get a comprehensive report on what it contains, the information flow, where to find what. Make sure your sub-agents return a concise report with line numbers indicating where crucial functions etc can be found.
- Copy over useful files from the `../asp-gen-refinements/` repository as a starting point, these can of course be modified for the current purpose.
- Establish a way to automatically evaluate whether or not a programs answer set is correct. Given the fixed encoding, I think this will be easy. If it turns out to be a problem due to syntactic differences, we can think about solutions. You should flag this as a concern to me.
- Run tests on 3 or so ARC-AGI puzzles. Make sure to really inspect the outcome, that included the intermediate data and logs, to make sure that the program behaves as expected and that the LLM receives the expected inputs.
- Once finished, summarize what you've done, accomplished, learnt, pay extra attention to the decisions you had to make, because I'm sure to have forgotten a lot of specifications in here.
	- Also, add `./docs/` directory with documentation that might be relevant for future agents working in here.
	- For re-usable knowledge about, e.g., how to work with a certain flow or file, you can create skills in `.claude/skills/<skill_name>/SKILL.md`, make sure to use a YAML frontmatter with (-name and -description), making the description short and concise and specifying *when* the skill should be used.


Do not stop working on this task until it's complete. Do not stop to ask questions or get confirmation. You are running unsupervised and autonomously. 
