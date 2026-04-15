#!/usr/bin/env python3
"""
NexusHV AI — Fine-tuning Pipeline
Trains a PhD-level virtualization expert LLM using QLoRA.

Base model: meta-llama/Meta-Llama-3.1-8B-Instruct
Method:     QLoRA (4-bit NF4 quantization during training)
Hardware:   1× GPU with ≥24GB VRAM (RTX 3090/4090, A10G, A100)
Time:       ~4h on RTX 4090, ~12h on RTX 3090

Install:
    pip install transformers peft trl bitsandbytes accelerate datasets
    pip install flash-attn --no-build-isolation   # optional, speeds up 2x

Run:
    accelerate launch nexushv_train.py
    # or single GPU:
    python nexushv_train.py
"""

import torch
import json
from pathlib import Path
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from peft import (
    LoraConfig,
    get_peft_model,
    prepare_model_for_kbit_training,
    TaskType,
)
from trl import SFTTrainer, DataCollatorForCompletionOnlyLM

# ─── Config ──────────────────────────────────────────────────────────────────
BASE_MODEL   = "meta-llama/Meta-Llama-3.1-8B-Instruct"
OUTPUT_DIR   = "./nexushv-ai-lora"
MERGED_DIR   = "./nexushv-ai-merged"
DATASET_FILE = "./nexushv_dataset.jsonl"
MAX_SEQ_LEN  = 4096
BATCH_SIZE   = 2       # per GPU — increase if VRAM allows
GRAD_ACCUM   = 8       # effective batch = 16
LR           = 2e-4
EPOCHS       = 3
WARMUP_RATIO = 0.05

# ─── QLoRA config ─────────────────────────────────────────────────────────────
BNBCONFIG = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",           # NormalFloat4 — best for LLM weights
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,      # 2nd quantization saves ~0.4 bits/param
)

LORA_CONFIG = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=64,                   # rank — higher = more capacity, more VRAM
    lora_alpha=128,         # scaling factor (typically 2×r)
    lora_dropout=0.05,
    bias="none",
    # Target all attention + MLP projection layers for best coverage
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
)

# ─── Prompt template (Llama 3 chat format) ────────────────────────────────────
SYSTEM = """You are NEXUS AI, the integrated administrator of the NexusHV bare-metal hypervisor platform.

You have PhD-level mastery of:
- KVM/QEMU internals (VMCS, EPT, VirtIO, ioeventfd, vhost)
- CPU virtualization (Intel VT-x VMX operations, AMD-V SVM, VPID/ASID, TSC)
- Memory virtualization (shadow page tables, EPT/NPT, balloon driver, KSM, huge pages)
- Storage virtualization (QCOW2 L1/L2 tables, thin provisioning, io_uring, cache modes)  
- Network virtualization (OVS, DPDK, SR-IOV, VirtIO-net, VXLAN, vhost-user)
- Live migration (pre-copy dirty page tracking, post-copy fault handling, RDMA)
- High availability (STONITH, split-brain, quorum, Raft consensus)
- Performance tuning (NUMA pinning, CPU isolation, huge pages, iothreads)
- Security (sVirt/SELinux, IOMMU groups, nested virt, side-channel mitigations)
- Troubleshooting (virsh, qemu-img, perf kvm, ftrace, crash dumps)

You monitor the NexusHV cluster proactively. You detect misconfigurations, predict failures, 
optimize performance, and explain every decision at a deep technical level.

When an admin asks a question, answer with expert depth. Cite specific kernel sources,
QEMU code paths, or RFC numbers when relevant. Never be vague.

When proposing an action, explain: WHAT you will do, WHY technically, and RISK level.
Always ask for approval before executing destructive operations."""

def format_prompt(sample: dict) -> str:
    """Format a training sample into Llama 3 chat template."""
    return (
        f"<|begin_of_text|>"
        f"<|start_header_id|>system<|end_header_id|>\n\n{SYSTEM}<|eot_id|>"
        f"<|start_header_id|>user<|end_header_id|>\n\n{sample['instruction']}"
        + (f"\n\nContext:\n{sample['input']}" if sample.get("input") else "")
        + f"<|eot_id|>"
        f"<|start_header_id|>assistant<|end_header_id|>\n\n{sample['output']}<|eot_id|>"
    )

# ─── Load dataset ─────────────────────────────────────────────────────────────
def load_dataset():
    samples = []
    with open(DATASET_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    print(f"Loaded {len(samples)} training samples")

    texts = [format_prompt(s) for s in samples]
    return Dataset.from_dict({"text": texts})

# ─── Main training ────────────────────────────────────────────────────────────
def train():
    print(f"Loading base model: {BASE_MODEL}")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=BNBCONFIG,
        device_map="auto",
        trust_remote_code=True,
        attn_implementation="flash_attention_2",  # remove if flash-attn not installed
    )
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LORA_CONFIG)
    model.print_trainable_parameters()
    # Expected: ~84M trainable / 8.03B total = ~1.05%

    dataset = load_dataset()

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        gradient_checkpointing=True,       # saves VRAM at cost of ~20% speed
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=WARMUP_RATIO,
        weight_decay=0.01,
        bf16=True,                         # use fp16=True if no bfloat16 support
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        evaluation_strategy="no",
        dataloader_num_workers=4,
        report_to="none",
        optim="paged_adamw_8bit",          # 8-bit Adam saves ~4GB VRAM
        group_by_length=True,              # reduces padding, speeds up ~10%
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LEN,
        args=training_args,
        packing=True,                      # pack multiple short samples per batch
    )

    print("Starting training...")
    trainer.train()
    trainer.save_model(OUTPUT_DIR)
    print(f"LoRA adapters saved to {OUTPUT_DIR}")

# ─── Merge LoRA into base model ───────────────────────────────────────────────
def merge_and_export():
    """
    Merge LoRA weights into base model for deployment.
    Output: full fp16 model ready for GGUF conversion.
    """
    from peft import PeftModel

    print("Merging LoRA adapters into base model...")
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, torch_dtype=torch.float16, device_map="auto"
    )
    model = PeftModel.from_pretrained(base, OUTPUT_DIR)
    model = model.merge_and_unload()

    model.save_pretrained(MERGED_DIR, safe_serialization=True)
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    tokenizer.save_pretrained(MERGED_DIR)
    print(f"Merged model saved to {MERGED_DIR}")
    print("Next: convert to GGUF with llama.cpp → deploy via Ollama")

# ─── Convert to GGUF for Ollama/llama.cpp ────────────────────────────────────
GGUF_INSTRUCTIONS = """
After merge_and_export(), convert to GGUF:

1. Clone llama.cpp:
   git clone https://github.com/ggerganov/llama.cpp && cd llama.cpp
   make -j$(nproc)

2. Convert to GGUF (fp16):
   python convert_hf_to_gguf.py ../nexushv-ai-merged --outtype f16 \\
     --outfile nexushv-ai-f16.gguf

3. Quantize to Q4_K_M (best quality/size trade-off, ~4.8GB):
   ./llama-quantize nexushv-ai-f16.gguf nexushv-ai-Q4_K_M.gguf Q4_K_M

4. Create Ollama model:
   ollama create nexushv-ai -f Modelfile

5. Test:
   ollama run nexushv-ai "Explain EPT violation handling in KVM"

Quantization options:
  Q2_K   = 2.7GB, lowest quality (not recommended for technical tasks)
  Q4_K_M = 4.8GB, excellent quality, fits RTX 3080 10GB  ← recommended
  Q5_K_M = 5.7GB, near-lossless
  Q8_0   = 8.5GB, essentially fp16 quality
  f16    = 15GB,  full precision (needs 24GB+ VRAM for inference)
"""

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "merge":
        merge_and_export()
        print(GGUF_INSTRUCTIONS)
    else:
        train()
