"""
Testing the nvidia/Nemotron-Cascade-2-30B-A3B model, 4-bit quantized version.

Supports both thinking and instruct (non-thinking) modes:
* Reasoning content is enclosed within <think> and </think> tags.
* To activate the instruct (non-thinking) mode, we prepend <think></think> to the beginning of the assistant’s response.
    This is done automatically by the tokenizer when enable_thinking=False
"""

from vllm import LLM, SamplingParams

# 1. Initialize the LLM
# We map the CLI flag --mamba-ssm-cache-dtype to the kwarg mamba_ssm_cache_dtype
llm = LLM(
    model="chankhavu/Nemotron-Cascade-2-30B-A3B-AWQ-INT4",
    quantization="awq",
    trust_remote_code=True,
    mamba_ssm_cache_dtype="float32",  # Crucial to prevent gibberish output in Mamba blocks
    max_model_len=4096,  # Adjust based on your batch size 256 needs on the 40GB A100
    tensor_parallel_size=1,
)

# 2. Define Recommended Generation Parameters
# Temperature 0.6 and top_k 20 keep the reasoning grounded without infinite loops
sampling_params = SamplingParams(temperature=1.0, top_p=0.95, top_k=20, max_tokens=2048)

prompts = [
    {"role": "system", "content": "You are a helpful AI assistant."},
    {"role": "user", "content": "Explain Answer Set Programming."},
]

# --- Mode 1: Thinking Mode (Default) ---
print("Running in Thinking Mode...")
# The model will automatically generate <think>...</think> tags
outputs_thinking = llm.chat(messages=prompts, sampling_params=sampling_params)

for output in outputs_thinking:
    print(f"Thinking + Answer:\n{output.outputs[0].text}\n")


# --- Mode 2: Non-Thinking Mode (Direct Answer) ---
print("Running in Non-Thinking Mode...")
# Passing enable_thinking=False tells the chat template to bypass the reasoning phase
outputs_direct = llm.chat(
    messages=prompts,
    sampling_params=sampling_params,
    chat_template_kwargs={"enable_thinking": False},
)

for output in outputs_direct:
    print(f"Direct Answer:\n{output.outputs[0].text}\n")
