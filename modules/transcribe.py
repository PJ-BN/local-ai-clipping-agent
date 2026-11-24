"""
modules/transcribe.py - Word-level Transcription using faster-whisper.
Extracts both sentence-level segments for AI viral hook analysis and
fine-grained word timestamps for subtitle generation.
Includes offline-first loading, auto-mirror support, and automatic CUDA-to-CPU fallback.
"""

import json
import os
from pathlib import Path
from typing import Optional

import ctranslate2


def ensure_whisper_model(model_size: str = "base") -> str:
    """
    Ensures that all required model files for faster-whisper are downloaded locally.
    Uses hf-mirror if direct connection has connectivity issues.
    """
    repo_id = f"Systran/faster-whisper-{model_size}"
    required_files = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]

    # First attempt: let faster-whisper handle it directly if already cached
    from faster_whisper.utils import download_model

    try:
        model_path = download_model(model_size, local_files_only=True)
        return model_path
    except Exception:
        pass

    # If not cached locally, download with mirror fallback
    from huggingface_hub import hf_hub_download

    endpoints = [None, "https://hf-mirror.com"]
    for endpoint in endpoints:
        try:
            model_dir = None
            for fname in required_files:
                kwargs = {"endpoint": endpoint} if endpoint else {}
                downloaded_file = hf_hub_download(repo_id, fname, **kwargs)
                model_dir = str(Path(downloaded_file).parent)
            if model_dir:
                return model_dir
        except Exception as e:
            if endpoint == endpoints[-1]:
                raise e
            continue

    return model_size


def detect_optimal_device() -> tuple[str, str]:
    """
    Detects if CUDA is available and returns (device, compute_type).
    Falls back gracefully to CPU with int8 quantization.
    """
    try:
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def create_whisper_instance(model_path: str, device: str, compute_type: str):
    """
    Instantiates WhisperModel with graceful fallback from CUDA to CPU if CUDA libraries are missing.
    """
    from faster_whisper import WhisperModel

    try:
        print(f"[*] Initializing WhisperModel on {device} ({compute_type})...")
        return WhisperModel(model_path, device=device, compute_type=compute_type), device, compute_type
    except Exception as e:
        if device == "cuda":
            print(f"[!] Warning: CUDA initialization failed ({e}). Falling back to CPU (int8)...")
            return WhisperModel(model_path, device="cpu", compute_type="int8"), "cpu", "int8"
        raise e


def transcribe(
    audio_path: str,
    workspace_dir: str = "workspace",
    model_size: str = "base",
    device: Optional[str] = None,
    compute_type: Optional[str] = None,
) -> tuple[str, str]:
    """
    Transcribes audio using faster-whisper with word-level timestamps.

    Args:
        audio_path: Path to extracted 16kHz WAV audio.
        workspace_dir: Directory where JSON outputs will be written.
        model_size: Whisper model size ("tiny", "base", "small", "medium", "large-v3").
        device: "cuda" or "cpu" (auto-detected if None).
        compute_type: "float16", "int8", etc. (auto-detected if None).

    Returns:
        tuple (transcript_segments_path, word_timestamps_path)
    """
    auto_dev, auto_comp = detect_optimal_device()
    chosen_device = device or auto_dev
    chosen_compute = compute_type or auto_comp

    print(f"[*] Ensuring faster-whisper model '{model_size}' is ready...")
    resolved_model = ensure_whisper_model(model_size)

    model, actual_device, actual_compute = create_whisper_instance(
        resolved_model, chosen_device, chosen_compute
    )

    print(f"[*] Starting transcription with word timestamps: {audio_path}")
    try:
        segments, info = model.transcribe(
            audio_path,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        # Materialize segments list so runtime errors (e.g. cublas) are caught immediately
        segments_list = list(segments)
    except Exception as e:
        if "cublas" in str(e).lower() or "cuda" in str(e).lower():
            print(f"[!] Warning: Runtime error during CUDA transcription ({e}). Switching to CPU (int8)...")
            from faster_whisper import WhisperModel
            model = WhisperModel(resolved_model, device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                audio_path,
                beam_size=5,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
            segments_list = list(segments)
        else:
            raise e

    detected_lang = getattr(info, "language", "en")
    lang_prob = getattr(info, "language_probability", 1.0)
    print(f"[+] Detected language: {detected_lang} (confidence: {lang_prob:.2%})")

    transcript_segments = []
    word_timestamps = []

    for seg in segments_list:
        text = seg.text.strip()
        if not text:
            continue

        transcript_segments.append({
            "start": round(float(seg.start), 2),
            "end": round(float(seg.end), 2),
            "text": text,
        })

        if seg.words:
            for w in seg.words:
                cleaned_word = w.word.strip()
                if cleaned_word:
                    word_timestamps.append({
                        "word": cleaned_word,
                        "start": round(float(w.start), 2),
                        "end": round(float(w.end), 2),
                        "probability": round(float(w.probability), 2),
                    })

    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)

    segments_path = ws / "transcript_segments.json"
    words_path = ws / "word_timestamps.json"

    with open(segments_path, "w", encoding="utf-8") as f:
        json.dump(transcript_segments, f, indent=2, ensure_ascii=False)

    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(word_timestamps, f, indent=2, ensure_ascii=False)

    print(f"[+] Generated {len(transcript_segments)} sentence segments -> {segments_path}")
    print(f"[+] Generated {len(word_timestamps)} word timestamps -> {words_path}")

    return str(segments_path), str(words_path)
