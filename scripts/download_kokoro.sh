#!/usr/bin/env bash
# scripts/download_kokoro.sh
# Downloads Kokoro-82M model weights and the voice files used by each Kiro persona.
# Files are cached in ~/.cache/huggingface/hub/ — safe to run multiple times.

set -euo pipefail

PY=/home/macklemoron/miniconda3/envs/kiro_asr/bin/python

echo "=== Kokoro model download ==="
$PY - <<'EOF'
from huggingface_hub import hf_hub_download
import os

REPO = "hexgrad/Kokoro-82M"

files = [
    "kokoro-v1_0.pth",          # 327MB — main model weights
    "voices/af_heart.pt",        # kiro  — warm American female
    "voices/am_adam.pt",         # ops   — efficient American male
    "voices/bm_george.pt",       # sage  — intellectual British male
    "voices/bm_lewis.pt",        # finley — authoritative British male
    "voices/am_michael.pt",      # coach — energetic American male
    "voices/bf_emma.pt",         # chef  — warm British female
    "voices/af_nicole.pt",       # doc   — gentle American female
]

for f in files:
    print(f"  Downloading {f} ...", flush=True)
    path = hf_hub_download(repo_id=REPO, filename=f)
    size_mb = os.path.getsize(path) / 1_048_576
    print(f"  ✓  {f}  ({size_mb:.1f} MB) → {path}", flush=True)

print("\nAll Kokoro files downloaded.")
EOF
