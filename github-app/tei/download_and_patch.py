"""Bakes sentence-transformers/all-MiniLM-L6-v2 into the TEI image at build
time.

Replaced nomic-embed-text-v1.5 after it repeatedly OOM-killed on the real
production host: dmesg showed every kill landing at anon-rss ~4184-4185MB,
right at the 4GB cgroup boundary, to within 1MB every single time - the
process was still climbing when killed, so no cgroup limit we tried was
actually measuring its peak, only censoring it. MiniLM is a 22M-param,
6-layer, 384-dim model (~90MB ONNX weights) built for exactly this
resource-constrained use case; this component is a similarity CACHE for
evidence packets, not the core relevance signal (see embedding_client.py -
it already degrades to `None`/cache-miss on any failure), so the much
smaller embedding space is a good trade for actually fitting on this host.

Unlike nomic's NomicBert config, MiniLM's config.json is a plain
single-named BERT config (hidden_size, num_attention_heads, etc, no GPT-2
alias fields) - the LEGACY_ALIAS_KEYS removal below is a no-op for this
model. Left in as a harmless safety net rather than deleted, in case we
swap models again.

Downloading and patching at build time, not at container startup, means a
fresh deploy never depends on HuggingFace's availability.
"""

import json
import urllib.request
from pathlib import Path

REPO = "sentence-transformers/all-MiniLM-L6-v2"
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
