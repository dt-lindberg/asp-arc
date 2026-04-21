"""Agent utilities"""

import re
from config.config_agent import THINKING


def split_thinking(text):
    """Split raw vLLM output into (thinking, response).

    * thinking: content inside the first <think>...</think> block
    * response: remaining text after stripping the thinking block

    Cases:
    1. Full block in output: <think>...</think>response
    2. <think> was prepended to prompt, so output starts directly with thinking
       content and ends with </think>response (no opening tag in output).
    3. If THINKING==True, but case 1 and 2 find no thinking tags, the model
        never stopped thinking. We return the full response as thinking.
    """
    # Case 1: both <think> and </think> present
    thinking_re = re.compile(r"<think>(.*?)</think>", re.DOTALL)
    match = thinking_re.search(text)
    if match:
        thinking = match.group(1).strip()
        response = thinking_re.sub("", text).strip()
        return thinking, response

    # Case 2: only </think> is present (<think> was added to prompt)
    end_idx = text.find("</think>")
    if end_idx != -1:
        thinking = text[:end_idx].strip()
        response = text[end_idx + len("</think>") :].strip()
        return thinking, response

    # Case 3: the entire response is thinking
    if THINKING:
        return text.strip(), ""

    return "", text.strip()
