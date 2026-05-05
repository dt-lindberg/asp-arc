"""Prompt-dependent extraction and formatting of NVARC data fields."""

import re


def extract_asp_block(response: str) -> str:
    """Extract the single ```asp...``` block from the model response.

    Raises ValueError if there are 0 or more than 1 such block.
    """
    blocks = re.findall(r"```asp\n(.*?)```", response, re.DOTALL)
    if len(blocks) == 0:
        raise ValueError("No ```asp block found in response")
    if len(blocks) > 1:
        raise ValueError(f"{len(blocks)} ```asp blocks found in response (expected exactly 1)")
    return blocks[0].strip()


def grid_to_input_facts(grid: list) -> str:
    """Convert a 2D grid to input/3 facts plus color/1 facts for all ARC colors."""
    lines = [
        f"input({r},{c},{v})."
        for r, row in enumerate(grid)
        for c, v in enumerate(row)
    ]
    lines.append("color(0..9).")
    return "\n".join(lines)


def extract_puzzle_xml(prompt: str) -> str:
    """Extract the <puzzle>...</puzzle> block from an outputs/ prompt."""
    m = re.search(r"(<puzzle>.*?</puzzle>)", prompt, re.DOTALL)
    if not m:
        raise ValueError("No <puzzle>...</puzzle> block found in prompt")
    return m.group(1)


def extract_python_code(completion: str) -> str:
    """Extract the first ```python...``` block from an outputs/ completion."""
    m = re.search(r"```python\n(.*?)```", completion, re.DOTALL)
    if not m:
        raise ValueError("No ```python block found in completion")
    return m.group(1).strip()


def build_prompt(template: str, puzzle_xml: str, python_code: str) -> str:
    return (
        template
        .replace("<<<PUZZLE_XML>>>", puzzle_xml)
        .replace("<<<PYTHON_CODE>>>", python_code)
    )
