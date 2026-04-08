Currently, each run generates data in three places:
- outputs/.out files are the logs of the run
- caches/.json files contain prompts, thinknig, and response data from all calls, but are not associated with a specific step or puzzle ID or any such thing. It's a bit disconnected from the logic that's actually going on.
- results/.json files contain the "final results" with some prompts, thinking, response, and success in puzzle train/test examples, answer sets etc. This is by far the most complete auditable history.

---

I feel like it's completely unnecessary to have the caches/. It was developed from a legacy repo that wanted to store their raw response data from APIs, but I'm not interested in that. I'm not interested in caching prompts and resuming them later, I don't care about this. 

What I care about, is to have an auditable trace that outlines exactly all of the steps that happened from generation of the initial program to the final program. In addition to all of the existing keys per puzzle in the results json:
[
  "all_train_correct",
  "candidates",
  "dataset",
  "final_correct",
  "full_program",
  "n_test_examples",
  "n_train_examples",
  "puzzle_id",
  "refinements",
  "run_id",
  "steps",
  "syntax_agent",
  "test_correct",
  "test_predictions",
  "test_verifications",
  "train_verifications"
]

I want to add the following:
- 
