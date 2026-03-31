"""
Test script for nvidia/Nemotron-Cascade-2-30B-A3B (NVFP4 quantized).

Runs both thinking and non-thinking modes and prints outputs with timing.
* Thinking mode: model generates <think>...</think> before the answer.
* Non-thinking mode: chat template prepends <think></think> to bypass reasoning.
"""

import time

from vllm import LLM, SamplingParams

MODEL_ID = "chankhavu/Nemotron-Cascade-2-30B-A3B-NVFP4"

# mamba_ssm_cache_dtype="float32" is required to prevent gibberish from Mamba blocks
llm = LLM(
    model=MODEL_ID,
    trust_remote_code=True,
    mamba_ssm_cache_dtype="float32",
    kv_cache_dtype="fp8",
    max_model_len=8192,
    tensor_parallel_size=1,
)

sampling_params = SamplingParams(temperature=1.0, top_p=0.95, top_k=20, max_tokens=8192)

messages = [
    {"role": "system", "content": "You are a helpful AI assistant."},
    {"role": "user", "content": "Explain Answer Set Programming."},
]

# --- Thinking mode ---
print("=" * 60)
print("MODE: Thinking")
print("=" * 60)
t0 = time.perf_counter()
outputs = llm.chat(messages=messages, sampling_params=sampling_params)
elapsed = time.perf_counter() - t0

for output in outputs:
    text = output.outputs[0].text
    n_tokens = len(output.outputs[0].token_ids)
    print(text)
    print(f"\n[{n_tokens} tokens in {elapsed:.2f}s — {n_tokens / elapsed:.1f} tok/s]")

# --- Non-thinking mode ---
print("\n" + "=" * 60)
print("MODE: Non-thinking")
print("=" * 60)
t0 = time.perf_counter()
outputs = llm.chat(
    messages=messages,
    sampling_params=sampling_params,
    chat_template_kwargs={"enable_thinking": False},
)
elapsed = time.perf_counter() - t0

for output in outputs:
    text = output.outputs[0].text
    n_tokens = len(output.outputs[0].token_ids)
    print(text)
    print(f"\n[{n_tokens} tokens in {elapsed:.2f}s — {n_tokens / elapsed:.1f} tok/s]")
