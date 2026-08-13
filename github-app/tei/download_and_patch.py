"""Bakes nomic-embed-text-v1.5 into the TEI image at build time, patched.

The model's config.json carries both GPT-2-style legacy field names (n_embd,
n_head, n_inner, n_layer, n_positions) and their canonical BERT-style
equivalents (hidden_size, num_attention_heads, intermediate_size,
num_hidden_layers, max_position_embeddings) - same values, dual naming, a
known pattern for NomicBert's GPT/BERT-compatible config. TEI's backend-init
code flattens both into one struct and re-serializes it internally; with
both spellings present this emits `hidden_size` twice and the ONNX loading
path throws "duplicate field `hidden_size`" and falls back to a slower,
more constrained path. n_positions=8192 also isn't a true duplicate value
of max_position_embeddings=2048, and TEI reports IT (not 2048) as
max_input_length - the model was only ever trained on 2048 tokens, so
trusting that reported ceiling would silently produce degraded embeddings
for longer input. All five legacy fields are dropped below; nothing here
was needed for correctness, only for parsing and for TEI's own self-report.

Downloading and patching at build time, not at container startup, means a
fresh deploy never depends on HuggingFace's availability and never repeats
the parse-fail-then-slow-fallback dance in prod.
"""

import json
import urllib.request
from pathlib import Path

REPO = "nomic-ai/nomic-embed-text-v1.5"
REVISION = "main"
OUT_DIR = Path("/model")

FILES = [
    "config.json",
    "1_Pooling/config.json",
    "sentence_bert_config.json",
    "config_sentence_transformers.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "modules.json",
    "onnx/model.onnx",
]

LEGACY_ALIAS_KEYS = ("n_embd", "n_head", "n_inner", "n_layer", "n_positions")


def fetch(relpath: str) -> bytes:
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{relpath}"
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def main() -> None:
    for relpath in FILES:
        dest = OUT_DIR / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = fetch(relpath)
        dest.write_bytes(data)
        print(f"downloaded {relpath} ({len(data)} bytes)")

    config_path = OUT_DIR / "config.json"
    config = json.loads(config_path.read_text())
    removed = [k for k in LEGACY_ALIAS_KEYS if config.pop(k, None) is not None]
    config_path.write_text(json.dumps(config, indent=2))
    print(f"patched config.json, removed: {removed}")


if __name__ == "__main__":
    main()
