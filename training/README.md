# Fine-Tuning Qwen3:8b for Qlik MCP Tool Calling

## Overview

This directory contains tools to generate training data and fine-tune Qwen3:8b to handle all 51 Qlik MCP tools. The approach uses **distillation** — Claude Sonnet 4 generates perfect tool-calling examples, then Qwen3:8b learns from them.

## Step 1: Generate Training Data

### Prerequisites
- Working Qlik AI Assistant with MCP connected
- AWS Bedrock API key with Claude Sonnet 4 access
- Qlik MCP OAuth access token

### Get the OAuth Token
1. Run the app: `chainlit run app.py`
2. Click the plug icon and connect to Qlik Cloud
3. The OAuth token is stored in the app session — add it to `.env`:
   ```
   QLIK_ACCESS_TOKEN=your-oauth-access-token
   ```

### Generate
```bash
# Dry run — see all questions without calling MCP
python training/generate_training_data.py --dry-run

# Test with 10 questions
python training/generate_training_data.py --limit 10

# Full run (188+ questions, ~$2-5 in Bedrock costs)
python training/generate_training_data.py
```

### Output
- `training/training_data.jsonl` — Training data in Qwen3 format (one JSON per line)
- `training/training_data.meta.json` — Full metadata with success/failure counts

## Step 2: Fine-Tune (Cloud GPU Required)

### Requirements
- GPU with 16GB+ VRAM (A100, RTX 4090, T4, etc.)
- Google Colab Pro ($10/month), AWS SageMaker, RunPod, or Lambda Labs

### Install
```bash
pip install unsloth transformers datasets bitsandbytes
```

### Training Script (Colab/Cloud)
```python
from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig
from datasets import load_dataset

# Load model with 4-bit quantization
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3-8B-bnb-4bit",
    max_seq_length=4096,
    load_in_4bit=True,
)

# Add LoRA adapters
model = FastLanguageModel.get_peft_model(
    model,
    r=64,
    lora_alpha=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                     "gate_proj", "up_proj", "down_proj"],
    lora_dropout=0.05,
)

# Load training data
dataset = load_dataset("json", data_files="training_data.jsonl", split="train")

# Train
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    args=SFTConfig(
        output_dir="qwen3-qlik-lora",
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        num_train_epochs=3,
        learning_rate=2e-4,
        warmup_ratio=0.1,
        logging_steps=10,
        save_steps=100,
    ),
)
trainer.train()

# Save
model.save_pretrained("qwen3-qlik-lora")
tokenizer.save_pretrained("qwen3-qlik-lora")
```

## Step 3: Export to Ollama

### Convert to GGUF
```bash
# Merge LoRA with base model
python -c "
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained('qwen3-qlik-lora')
model.save_pretrained_merged('qwen3-qlik-merged', tokenizer)
"

# Convert to GGUF (requires llama.cpp)
python llama.cpp/convert_hf_to_gguf.py qwen3-qlik-merged --outfile qwen3-qlik.gguf
./llama.cpp/build/bin/llama-quantize qwen3-qlik.gguf qwen3-qlik-q4km.gguf Q4_K_M
```

### Create Ollama Model
```bash
# Create Modelfile
cat > Modelfile << 'EOF'
FROM ./qwen3-qlik-q4km.gguf
SYSTEM "You are a Qlik Cloud data analyst assistant. Always call tools — never guess."
PARAMETER temperature 0.2
EOF

# Import
ollama create qwen3-qlik -f Modelfile
ollama run qwen3-qlik "What apps do I have?"
```

## Step 4: Use in the App

Update `app.py` to add the Ollama model option:
```python
# Add to BEDROCK_MODELS or create a separate Ollama integration
# using langchain-ollama package
```

## Training Data Format

Each line in `training_data.jsonl` is a JSON object:
```json
{
  "messages": [
    {"role": "system", "content": "You are a Qlik Cloud data analyst..."},
    {"role": "user", "content": "What apps do I have?"},
    {"role": "assistant", "tool_calls": [{"function": {"name": "qlik_search", "arguments": "{\"query\": \"*\", \"resourceType\": \"app\"}"}}]},
    {"role": "tool", "name": "qlik_search", "content": "[results]"},
    {"role": "assistant", "content": "You have 8 apps..."}
  ]
}
```

## Cost Estimate

| Phase | Cost |
|---|---|
| Training data generation (188 questions) | ~$2-5 (Bedrock Claude) |
| Fine-tuning (Colab Pro A100, 2-4 hours) | ~$10-15 |
| Inference (Ollama, local) | Free |
| **Total** | **~$15-20 one-time** |
