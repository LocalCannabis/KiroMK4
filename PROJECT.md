# Kiro Project Overview (handoff)

## Goal
Always-on, voice-first AI assistant (Jarvis-like) on Linux desktop. Pipeline: mic → VAD → STT → LLM → TTS → speaker, with persona-specific voices.

## Current status
- End-to-end voice loop works on CPU (arecord/aplay via PipeWire).
- VAD: Silero v5 (512-sample chunks). Falls back to RMS energy if unavailable.
- STT: faster-whisper `tiny.en`, CPU int8, ~0.3s per utterance on i9-9900K.
- LLM: OpenAI gpt-4o streaming; sentences flushed as they arrive; datetime injected.
- TTS: Kokoro-82M multi-voice primary; Piper fallback. Persona map configured. Latency ~0.5s/sentence on CPU.
- `.env` auto-loaded via python-dotenv.

## Repo layout
- [kiro.py](kiro.py): main orchestrator (Sprint 1 voice loop).
- [config.yaml](config.yaml): settings (audio, VAD, STT, LLM, TTS, routing, memory scaffold).
- [audio/tts.py](audio/tts.py): multi-engine TTS (Kokoro + Piper fallback).
- [scripts/download_kokoro.sh](scripts/download_kokoro.sh): downloads Kokoro weights + persona voices.
- data/voices/: Piper ONNX models (symlinked from KiroMKIII).

## How to run
```
conda activate kiro_asr
cd /home/macklemoron/Projects/KiroMK4
python kiro.py --once --text "Hello"     # text bypass
python kiro.py                            # live voice loop
```
List devices: `python kiro.py --list-devices`

## Audio
- Input: arecord subprocess, ALSA device `default` (PipeWire handles routing). 16kHz, mono, block_ms=32 (exactly 512 samples for Silero).
- Output: aplay subprocess, ALSA device `default`. Piper plays raw S16_LE; Kokoro plays in-memory WAV at 24kHz.

## VAD
- Silero v5 loaded via `silero_vad`; threshold 0.55; start_trigger_frames=5; end_silence_ms=700; max_utterance_s=20.

## STT
- faster-whisper settings (config.yaml):
  - model_size: tiny.en
  - device: cpu
  - compute_type: int8
  - cpu_threads: 8
  - num_workers: 1

## LLM
- OpenAI gpt-4o (streaming); temperature 0.4; max_tokens 80.
- System prompt: no markdown, 1-2 sentences, conversational, datetime injected (Vancouver).

## TTS
- Engine preference: Kokoro (primary), Piper fallback.
- Persona → Kokoro voice:
  - kiro: af_heart
  - ops: am_adam
  - sage: bm_george
  - finley: bm_lewis
  - coach: am_michael
  - chef: bf_emma
  - doc: af_nicole
- Kokoro sample rate: 24000 Hz. Piper sample rate: 22050 Hz.
- Piper binary: /home/macklemoron/Projects/KiroMKIII/bin/piper/piper

## Environment
- Conda env: kiro_asr (Python 3.12). Torch 2.7.1+cu118 present. faster-whisper 1.2.0. silero-vad 5.x. Kokoro 0.9.4 installed via pip. nvidia-cudnn-cu12 9.1.0.70 installed via pip.
- activate hook: /home/macklemoron/miniconda3/envs/kiro_asr/etc/conda/activate.d/nvidia_libs.sh sets LD_LIBRARY_PATH to pip nvidia libs.

## CUDA/cuDNN situation
- System has cuDNN 8.9.5 under /usr/local/cuda/lib64 with misleading .so.9.1.0 symlinks; they conflict with pip cuDNN 9.1.0.
- Added LD_LIBRARY_PATH and local .so.9.1.0 symlinks in pip cudnn dir. Kokoro on CUDA still fails (cudnn ops symbol missing).
- Next step: install a clean cudnn from conda-forge (any 9.15+). Prefer mamba solver:
```
conda activate base
conda install -n base conda-libmamba-solver -y
conda config --set solver libmamba
mamba install -n kiro_asr -c conda-forge cudnn
```
This should shadow /usr/local/cuda copies and fix CUDA for Kokoro and faster-whisper.

## Router (personas)
- Config has keyword_map but Kiro currently always uses default_persona (kiro). Sprint 2: implement routing to select persona → voice based on keywords/model.

## Memory scaffold
- Disabled. Config includes SQLite path and vector store (chroma) settings. Sprint 2: persist convo history + retrieval.

## Known issues / TODO
1) Fix CUDA/cuDNN so Kokoro and Whisper can run on GPU (RTF should drop to ~0.02x).
2) Implement persona routing using router.keyword_map.
3) Enable memory (SQLite + retrieval) per config scaffold.
4) Chatterbox TTS deferred (PyPI broken on Python 3.12; would need a 3.10 side-env or wait for upstream fix).

## Quick commands
- Download Kokoro weights/voices: `bash scripts/download_kokoro.sh`
- Smoke TTS (text): `python kiro.py --once --text "Hello"`
- List audio devices: `python kiro.py --list-devices`

## Contact points
- Piper models reused from KiroMKIII at /home/macklemoron/Projects/KiroMKIII/bin/piper/piper and data/voices symlinks.
- Hugging Face cache for Kokoro: ~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M
