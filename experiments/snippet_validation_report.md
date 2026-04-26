# Snippet validation report — `src/prompts/initial_prompt.txt`

Ran every code snippet from `<clingo_syntax_reference>` and the worked
`<asp>...</asp>` block through Clingo 5.6 (parse + ground + solve), with a
small harness providing `input/3`, `color/1`, `row/1`, `col/1`,
`output_cell/2`, etc. so that fragment snippets have predicates to bind to.

## Summary

- **33 snippets** extracted (32 from the syntax guide + 1 worked example).
- **7 skipped** as pseudo-syntax templates (`pred(t1,...,tn).`, `head :- body.`,
  `l { atom : condition } u`, etc.) — these contain literal placeholders, not
  real Clingo.
- **23/26 testable snippets agree with their label** (Wrong/Unsafe fail,
  Correct/Safe pass).
- **3 mismatches** — all "Correct:" examples that do not actually compile
  standalone. Details below.

## Mismatches (3)

### 1. Line 53 — `1 { pattern : eq(X, 0) } 1`

Labeled **Correct**, but it's not a complete statement (missing `.`),
`pattern` is undefined, and `X` is unbound. Fails on parse.

Surrounding context:
```
Wrong:     1 { pattern : 0 } 1      % 0 is a number, not a predicate
Correct:   1 { pattern : eq(X, 0) } 1   % or use a boolean predicate
```

The "Wrong" version is genuinely wrong (bare constant after `:`). The "Correct"
counterpart only fixes the *condition* but leaves the rest malformed. If the
LLM copies this fragment verbatim it produces a parse error.

**Suggested fix:** replace with a complete, grounded example such as
```
Correct:   1 { output(R, C, Color) : color(Color) } 1 :- output_cell(R, C).
```
(which is exactly the canonical pattern in the worked example, line 179).

### 2. Line 57 — `(C + L) \ 2 = 0.`

Labeled **Correct**, demonstrating that `\` is the modulo operator (vs. the
Wrong `#mod`). But shown as a standalone fact with a terminating period,
`C` and `L` are not bound by any positive predicate. Fails on grounding
("unsafe variables").

```
Correct:   (C + L) \ 2 = 0.
Wrong:     (C + L) #mod 2 = 0.
```

If the LLM lifts the "Correct" line as-is into a program, it produces
an unsafe-variable error — exactly the dominant failure mode (R3: 889
unsafe errors).

**Suggested fix:** wrap as a complete safe rule:
```
Correct:   even_sum(R, C) :- row(R), col(C), (R + C) \ 2 = 0.
```

### 3. Line 79 — `:- not output(R1,C1,V1).`

Labeled **Correct**, demonstrating the fix for `not (group)`. But the
constraint binds nothing — `R1`, `C1`, `V1` only appear under `not`, which
does not bind. Fails on grounding (unsafe).

```
Wrong:     :- not (output(R1,C1,V1), input(I,J,V1), used(I,J,R1,C1)).
Correct:   :- not output(R1,C1,V1).
```

Same risk: if the LLM lifts this verbatim it produces unsafe-variable errors.
The point being made (about the `not (group)` syntax) is sound, but the
example doesn't survive on its own.

**Suggested fix:** make the fix a complete, safe constraint:
```
Correct:   :- input(R1,C1,V1), V1 != 0, not output(R1,C1,V1).
```

## Verified

The R3 fix to **Section 6** is correct and important: the *Unsafe* example
(line 117) `colCount(C, N) :- N = #count{R : input(R, C, Color), Color != 0}.`
genuinely fails grounding because `C` and `Color` are local to the aggregate.
The *Safe* fix (line 119) with `col(C)` outside compiles. Deepseek-v4's
diagnosis of the original "C and V are global" claim being wrong is correct.

The worked `<asp>...</asp>` example (lines 168–185) parses, grounds, and
solves cleanly under the harness inputs.

All four numbered Unsafe/Safe pairs in Section 4 (lines 85–102) behave as
labeled. All Wrong examples in Sections 1–3 are correctly Wrong (parse or
ground errors).

## Implication

Three "Correct" examples in the guide are themselves syntactically
incomplete/unsafe when read as standalone Clingo. This is a plausible
contributor to the unsafe-variable error pile-up: the model is being shown
example *fragments* labeled as the right answer, and may be reproducing them
verbatim. The fixes above turn each into a complete safe rule without
changing the lesson being taught.
