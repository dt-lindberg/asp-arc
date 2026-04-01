## Tool Definitions

Tools are defined as XML in the system prompt . Build it as a string you prepend to your existing system content:

```python
TOOL_SYSTEM_PROMPT = """\
# Tools

You have access to the following functions:

<tools>
<function>
<name>edit_code</name>
<description>Create/overwrite a file (pass `content`) or make a targeted patch (pass `old_str` + `new_str`).</description>
<parameters>
<parameter><name>file_path</name><type>string</type><description>Path to file</description></parameter>
<parameter><name>content</name><type>string</type><description>Full file content (new/overwrite)</description></parameter>
<parameter><name>old_str</name><type>string</type><description>Exact text to replace</description></parameter>
<parameter><name>new_str</name><type>string</type><description>Replacement text</description></parameter>
</parameters>
<required>["file_path"]</required>
</function>

<function>
<name>run_clingo</name>
<description>Run ASP code through Clingo. Returns answer sets or UNSAT.</description>
<parameters>
<parameter><name>code</name><type>string</type><description>ASP source (.lp content)</description></parameter>
<parameter><name>num_models</name><type>integer</type><description>Max answer sets, 0=all. Default 1</description></parameter>
<parameter><name>extra_args</name><type>string</type><description>Extra clingo flags e.g. "--time-limit=10"</description></parameter>
</parameters>
<required>["code"]</required>
</function>
</tools>

If you choose to call a function ONLY reply in the following format with NO suffix:

<tool_call>
<function=function_name>
<parameter=parameter_name>
value
</parameter>
</function>
</tool_call>

<IMPORTANT>
- Function calls MUST use the format above
- Required parameters MUST be specified
- You may reason BEFORE a tool call, never after
- If no tool is needed, answer the question normally
</IMPORTANT>"""
```

## Tool Implementations

```python
import os, re, subprocess, tempfile

def edit_code(file_path, content=None, old_str=None, new_str=None):
    os.makedirs(os.path.dirname(os.path.abspath(file_path)) or ".", exist_ok=True)
    if old_str is not None:
        text = open(file_path).read()
        if old_str not in text:
            return f"Error: old_str not found in '{file_path}'."
        open(file_path, "w").write(text.replace(old_str, new_str, 1))
        return f"Patched '{file_path}'."
    open(file_path, "w").write(content)
    return f"Wrote {len(content)} chars to '{file_path}'."

def run_clingo(code, num_models=1, extra_args=""):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lp", delete=False) as f:
        f.write(code); tmp = f.name
    try:
        cmd = ["clingo", tmp, str(num_models)] + (extra_args.split() if extra_args else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (r.stdout + (f"\n[stderr]: {r.stderr}" if r.stderr.strip() else "")).strip()
    except subprocess.TimeoutExpired:
        return "Error: Clingo timed out after 30s."
    finally:
        os.unlink(tmp)

TOOL_FNS = {"edit_code": edit_code, "run_clingo": run_clingo}
```

## Agentic Loop

The model emits `<tool_call>` XML — parse it from `out.text`, dispatch, then inject the result back as a **`user`** message with `<tool_response>` tags :

```python
def parse_tool_call(text):
    tc = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if not tc: return None
    fn = re.search(r"<function=(\w+)>(.*?)</function>", tc.group(1), re.DOTALL)
    if not fn: return None
    params = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"<parameter=(\w+)>(.*?)</parameter>", fn.group(2), re.DOTALL)
    }
    if "num_models" in params:
        params["num_models"] = int(params["num_models"])
    return {"name": fn.group(1), "params": params}

def run_agent(user_message, history=None):
    messages = history or [{"role": "system", "content": TOOL_SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": user_message})

    while True:
        out = llm.chat(
            messages,
            sampling_params=sampling_params,       # temperature=1.0, top_p=0.95
            chat_template_kwargs={"enable_thinking": True},
        )[0].outputs[0]

        messages.append({"role": "assistant", "content": out.text})

        call = parse_tool_call(out.text)
        if not call:
            return messages   # model gave final answer

        print(f"\n[→ Tool]  {call['name']}  {call['params']}")
        result = TOOL_FNS[call["name"]](**call["params"])
        print(f"[← Result] {result[:300]}")

        # ⚠️ tool role = user, wrapped in <tool_response> per model card
        messages.append({
            "role": "user",
            "content": f"<tool_response>\n{result}\n</tool_response>",
        })
```

## Verifying It Works

**Smoke test — force a Clingo call:**
```python
history = run_agent("Run this ASP and show me the answer sets: a :- not b. b :- not a.")
# Expect: model emits <tool_call> for run_clingo, then gives final answer with both models
```

**Inspect raw output before the loop:**
```python
out = llm.chat(messages, sampling_params=sampling_params,
               chat_template_kwargs={"enable_thinking": True})[0].outputs[0]
print(out.text)   # should contain <tool_call>...</tool_call> if tool was triggered
```

**Check the parser found a call:**
```python
call = parse_tool_call(out.text)
print(call)   # None → no tool call. {'name': ..., 'params': ...} → found one
```

**Verify Clingo directly:**
```python
print(run_clingo("color(1..3). :- color(X), color(Y), X != Y. #show color/1."))
# Expected: "Answer: 1\ncolor(1)\nSATISFIABLE"
```

## ⚠️ Thinking + Multi-Turn

Per the model card: in multi-turn, only the **final summary** (not the full `<think>` block) should be kept in history . Strip it before the next call to prevent context bloat and template mis-tokenization:

```python
def trim_thinking(messages):
    for m in messages:
        if m["role"] == "assistant" and m.get("content"):
            m["content"] = re.sub(
                r"<think>.*?</think>", "", m["content"], flags=re.DOTALL
            ).strip()
    return messages

# Call after each round before passing history to the next run_agent()
history = trim_thinking(history)
```

Useful references: [Reasoning Outputs docs](https://docs.vllm.ai/en/v0.9.1/features/reasoning_outputs.html) · [Tool Calling docs](https://docs.vllm.ai/en/v0.18.0/features/tool_calling/) · [offline `chat_with_tools.py` example](https://github.com/vllm-project/vllm/blob/main/examples/offline_inference/chat_with_tools.py) [docs.vllm](https://docs.vllm.ai/en/v0.9.1/features/reasoning_outputs.html)
