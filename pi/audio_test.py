#!/usr/bin/env python3
"""
audio_test.py — Audio hardware discovery and testing utility for Kiro Pi.

Standalone tool to identify audio devices, test recording and playback,
and determine the correct device indices for kiro_client_config.yaml.

Usage:
    python audio_test.py                              # List all devices
    python audio_test.py --record                     # Record 3s, play back on default
    python audio_test.py --record --input 1           # Record from device 1
    python audio_test.py --record --input 1 --output 3  # Specify both devices
    python audio_test.py --record --duration 5        # Record 5 seconds
    python audio_test.py --latency --input 1          # Measure input latency
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import wave
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    print("ERROR: sounddevice not installed. Run: pip install sounddevice")
    sys.exit(1)


def list_devices() -> None:
    """Print all available audio input and output devices."""
    devices = sd.query_devices()
    print("=" * 72)
    print("AUDIO DEVICES")
    print("=" * 72)

    # Separate input and output devices
    inputs = []
    outputs = []
    both = []

    for i, dev in enumerate(devices):
        has_input = dev["max_input_channels"] > 0
        has_output = dev["max_output_channels"] > 0
        if has_input and has_output:
            both.append((i, dev))
        elif has_input:
            inputs.append((i, dev))
        elif has_output:
            outputs.append((i, dev))

    default_input = sd.default.device[0]
    default_output = sd.default.device[1]

    # Input devices
    print("\n🎤 INPUT DEVICES (microphones):")
    print("-" * 72)
    for i, dev in inputs + [(j, d) for j, d in both]:
        default_marker = " ← DEFAULT" if i == default_input else ""
        print(
            f"  [{i:2d}] {dev['name']:<45} "
            f"ch={dev['max_input_channels']} "
            f"rate={int(dev['default_samplerate'])}Hz"
            f"{default_marker}"
        )

    # Output devices
    print(f"\n🔊 OUTPUT DEVICES (speakers):")
    print("-" * 72)
    for i, dev in outputs + [(j, d) for j, d in both]:
        default_marker = " ← DEFAULT" if i == default_output else ""
        print(
            f"  [{i:2d}] {dev['name']:<45} "
            f"ch={dev['max_output_channels']} "
            f"rate={int(dev['default_samplerate'])}Hz"
            f"{default_marker}"
        )

    # Duplex devices
    if both:
        print(f"\n🔄 DUPLEX DEVICES (input + output):")
        print("-" * 72)
        for i, dev in both:
            din = " ← DEFAULT IN" if i == default_input else ""
            dout = " ← DEFAULT OUT" if i == default_output else ""
            print(
                f"  [{i:2d}] {dev['name']:<45} "
                f"in={dev['max_input_channels']} out={dev['max_output_channels']} "
                f"rate={int(dev['default_samplerate'])}Hz"
                f"{din}{dout}"
            )

    print(f"\nDefault input:  [{default_input}]")
    print(f"Default output: [{default_output}]")
    print()


def resolve_device(device_spec, kind: str = "input"):
    """Resolve a device specifier (None, int, or name substring) to an index."""
    if device_spec is None:
        return None  # Use system default
    if isinstance(device_spec, int):
        return device_spec
    # String: find by substring match
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        ch_key = "max_input_channels" if kind == "input" else "max_output_channels"
        if device_spec.lower() in dev["name"].lower() and dev[ch_key] > 0:
            return i
    print(f"WARNING: No {kind} device matching '{device_spec}' found. Using default.")
    return None


def record_and_playback(
    input_device=None,
    output_device=None,
    duration: float = 3.0,
    sample_rate: int = 16000,
    save_path: str = "test_recording.wav",
) -> None:
    """Record audio, save to WAV, and play back."""
    in_idx = resolve_device(input_device, "input")
    out_idx = resolve_device(output_device, "output")

    in_name = sd.query_devices(in_idx or sd.default.device[0])["name"] if in_idx is not None else "default"
    out_name = sd.query_devices(out_idx or sd.default.device[1])["name"] if out_idx is not None else "default"

    print(f"\n📍 Recording {duration}s from: [{in_idx or 'default'}] {in_name}")
    print(f"   Sample rate: {sample_rate}Hz, Channels: 1 (mono)")
    print(f"   Speak now!", flush=True)

    t0 = time.perf_counter()
    audio = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=in_idx,
    )
    sd.wait()
    elapsed = time.perf_counter() - t0

    print(f"   ✓ Recorded {len(audio)} samples in {elapsed:.1f}s")

    # Analyze audio
    audio_float = audio.astype(np.float32).flatten() / 32768.0
    rms = float(np.sqrt(np.mean(audio_float ** 2)))
    peak = float(np.max(np.abs(audio_float)))
    print(f"   RMS level: {rms:.4f} | Peak: {peak:.4f}")

    if rms < 0.001:
        print("   ⚠️  Very low audio level — mic may not be working")
    elif rms < 0.01:
        print("   ⚠️  Low audio level — check mic gain")
    else:
        print("   ✓ Audio levels look good")

    # Save to WAV
    save_wav(audio.flatten(), sample_rate, save_path)
    print(f"\n💾 Saved to: {save_path}")

    # Playback
    print(f"\n🔊 Playing back on: [{out_idx or 'default'}] {out_name}")
    sd.play(audio, samplerate=sample_rate, device=out_idx)
    sd.wait()
    print("   ✓ Playback complete")


def save_wav(pcm_int16: np.ndarray, sample_rate: int, path: str) -> None:
    """Save int16 numpy array to a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())


def measure_latency(
    input_device=None,
    sample_rate: int = 16000,
    duration: float = 2.0,
) -> None:
    """Measure input latency by detecting time to first non-silent sample."""
    in_idx = resolve_device(input_device, "input")
    silence_threshold = 0.005  # RMS threshold for "non-silent"
    block_size = 512  # ~32ms at 16kHz

    print(f"\n⏱️  Measuring input latency...")
    print(f"   Device: [{in_idx or 'default'}]")
    print(f"   Make a sharp sound (clap, tap) after the 'GO' prompt.\n")
    time.sleep(0.5)
    print("   GO!", flush=True)

    t_start = time.perf_counter()
    first_sound_ms = None
    blocks_recorded = 0
    max_blocks = int(duration * sample_rate / block_size)

    def callback(indata, frames, time_info, status):
        nonlocal first_sound_ms, blocks_recorded
        if first_sound_ms is not None:
            return
        blocks_recorded += 1
        audio = indata.astype(np.float32).flatten() / 32768.0
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms > silence_threshold:
            first_sound_ms = (time.perf_counter() - t_start) * 1000

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_size,
        device=in_idx,
        callback=callback,
    ):
        while first_sound_ms is None and blocks_recorded < max_blocks:
            time.sleep(0.01)

    if first_sound_ms is not None:
        print(f"\n   ✓ First sound detected at: {first_sound_ms:.0f}ms")
        print(f"     (includes ~{block_size/sample_rate*1000:.0f}ms block latency)")
    else:
        print(f"\n   ⚠️  No sound detected in {duration}s — check mic")


def main():
    parser = argparse.ArgumentParser(
        description="Kiro Pi audio hardware discovery and testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python audio_test.py                         List all audio devices
  python audio_test.py --record                Record 3s and play back
  python audio_test.py --record --input 1      Record from specific device
  python audio_test.py --latency               Measure input latency
        """,
    )
    parser.add_argument("--record", action="store_true", help="Record and play back audio")
    parser.add_argument("--latency", action="store_true", help="Measure input latency")
    parser.add_argument("--input", default=None, help="Input device (index or name substring)")
    parser.add_argument("--output", default=None, help="Output device (index or name substring)")
    parser.add_argument("--duration", type=float, default=3.0, help="Recording duration in seconds")
    parser.add_argument("--rate", type=int, default=16000, help="Sample rate in Hz")
    parser.add_argument("--save", default="test_recording.wav", help="Output WAV file path")
    args = parser.parse_args()

    # Resolve device specs (could be int or string)
    in_dev = int(args.input) if args.input and args.input.isdigit() else args.input
    out_dev = int(args.output) if args.output and args.output.isdigit() else args.output

    # Always show device list
    list_devices()

    if args.record:
        record_and_playback(
            input_device=in_dev,
            output_device=out_dev,
            duration=args.duration,
            sample_rate=args.rate,
            save_path=args.save,
        )

    if args.latency:
        measure_latency(
            input_device=in_dev,
            sample_rate=args.rate,
            duration=args.duration,
        )

    if not args.record and not args.latency:
        print("💡 Tip: Use --record to test mic/speaker, --latency to measure delay")
        print("   Then update kiro_client_config.yaml with the right device indices.\n")


if __name__ == "__main__":
    main()
