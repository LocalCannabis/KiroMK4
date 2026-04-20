"""
voice/routes.py — Flask blueprint for the voice processing API.

Provides:
    POST /process   — Full pipeline: audio WAV in → audio WAV out
    GET  /health    — Voice pipeline component health
    GET  /ping      — Latency measurement

Mount at / on the unified app:
    from voice.routes import voice_bp, init_voice
    init_voice(app, pipeline_cfg)
    app.register_blueprint(voice_bp)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

import numpy as np
from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger("kiro.voice")

voice_bp = Blueprint("voice", __name__)

# The pipeline instance is set by init_voice()
_pipeline = None


def init_voice(app, pipeline_cfg: Dict[str, Any]) -> None:
    """Initialize the voice pipeline and register the blueprint."""
    global _pipeline
    from voice.pipeline import VoicePipeline

    _pipeline = VoicePipeline(pipeline_cfg)
    _pipeline.init()

    app.config["voice_pipeline"] = _pipeline
    app.register_blueprint(voice_bp)
    logger.info("Voice blueprint registered (pipeline %s)",
                "ready" if _pipeline.ready else "degraded")


def get_pipeline():
    return _pipeline


@voice_bp.route("/ping", methods=["GET"])
def ping():
    return jsonify({"pong": True, "timestamp": time.time()})


@voice_bp.route("/health", methods=["GET"])
def health():
    if _pipeline is None:
        return jsonify({"status": "not_initialized", "voice_enabled": False})
    h = _pipeline.health()
    h["timestamp"] = time.time()
    return jsonify(h)


@voice_bp.route("/process", methods=["POST"])
def process():
    """
    Full voice pipeline: audio in → audio out.

    Input:
        Body: Raw WAV bytes
        Headers:
            Content-Type: audio/wav
            X-Session-Id: <uuid>   (optional)
            X-Persona: <name>      (optional)

    Output:
        Body: WAV audio bytes
        Content-Type: audio/wav
        X-Transcript, X-Response-Text, X-Persona, X-Session-Id, X-Timing
    """
    if _pipeline is None or not _pipeline.ready:
        return jsonify({"error": "Voice pipeline not initialized"}), 503

    content_type = request.content_type or "audio/wav"
    audio_data = request.get_data()

    if not audio_data:
        return jsonify({"error": "No audio data in request body"}), 400

    # For raw PCM, convert to WAV first
    if "pcm" in content_type or "raw" in content_type:
        sr = int(request.headers.get("X-Sample-Rate", 16000))
        channels = int(request.headers.get("X-Channels", 1))
        pcm = np.frombuffer(audio_data, dtype=np.int16)
        if channels > 1:
            pcm = pcm[::channels]
        from voice.pipeline import pcm_to_wav_bytes
        audio_data = pcm_to_wav_bytes(pcm.astype(np.float32) / 32768.0, sr)
    elif "wav" not in content_type:
        return jsonify({"error": f"Unsupported Content-Type: {content_type}"}), 415

    session_id = request.headers.get("X-Session-Id", str(uuid.uuid4()))
    persona_override = request.headers.get("X-Persona")

    try:
        wav_out, meta = _pipeline.process(
            audio_data,
            session_id=session_id,
            persona_override=persona_override if persona_override else None,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        logger.error("Voice process failed: %s", e, exc_info=True)
        return jsonify({"error": f"Processing failed: {e}"}), 500

    resp = Response(wav_out, mimetype="audio/wav")
    resp.headers["X-Transcript"] = meta["transcript"]
    resp.headers["X-Response-Text"] = meta["response_text"][:500]
    resp.headers["X-Persona"] = meta["persona"]
    resp.headers["X-Session-Id"] = meta["session_id"]
    resp.headers["X-Timing"] = json.dumps(meta["timing"])
    return resp
