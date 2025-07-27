"""
whisper_utils.py

Handles Whisper model transcription using chunked audio processing,
GPU acceleration, fault-tolerant logging, model auto-selection,
and future speaker diarization.
"""
import whisper
import tempfile
import torchaudio
import os
import math
import logging
import torch
import psutil
import filetype
import platform

logger = logging.getLogger("oasis-transcription.whisper_utils")
logger.info("Available Whisper models: %s", whisper.available_models())

# Platform-aware backend selection
system_platform = platform.system()

if system_platform == "Windows":
    try:
        torchaudio.set_audio_backend("soundfile")
        logger.info("Using 'soundfile' backend for torchaudio on Windows.")
    except Exception as e:
        logger.error("Failed to set 'soundfile' backend on Windows: %s", str(e))
else:
    try:
        torchaudio.set_audio_backend("soundfile")
        logger.info("Using 'soundfile' backend for torchaudio on Linux/macOS.")
    except Exception as e:
        logger.warning("Failed to set 'soundfile' backend: %s", str(e))
        try:
            torchaudio.set_audio_backend("ffmpeg")
            logger.info("Fallback to 'ffmpeg' backend for torchaudio.")
        except Exception as e2:
            logger.error("Failed to set 'ffmpeg' backend as fallback: %s", str(e2))

def auto_select_model():
    mem_gb = psutil.virtual_memory().total / 1e9
    logger.info("Detected RAM: %.2f GB", mem_gb)
    if mem_gb >= 24:
        return "large"
    elif mem_gb >= 16:
        return "medium"
    elif mem_gb >= 8:
        return "small"
    return "base"

def transcribe_audio(file_path: str, model_size: str = "base") -> str:
    logger.info("Start transcription for: %s with model=%s", file_path, model_size)
    model = whisper.load_model(model_size)
    result = model.transcribe(file_path)
    transcript = result.get("text", "")
    base, _ = os.path.splitext(file_path)
    transcript_file = f"{base}.txt"
    try:
        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write(transcript)
        logger.info("Transcript saved to: %s", transcript_file)
    except Exception as exc:
        logger.error("Failed to write transcript file: %s", exc)
    return transcript

def transcribe_audio_in_chunks(file_path: str, model_size=None, chunk_seconds=30, session_id=None, recording_name=None) -> str:
    from utils.gcs_utils import upload_transcript_chunk_to_gcs, merge_transcript_chunks
    from utils.pubsub_processing_utils import publish_to_pubsub

    model_size = model_size or auto_select_model()
    logger.info("Chunked transcription started: %s [%s]", file_path, model_size)
    use_gpu = torch.cuda.is_available()
    device = "cuda" if use_gpu else "cpu"
    model = whisper.load_model(model_size, device=device)

    kind = filetype.guess(file_path)
    if kind:
        print("Detected MIME:", kind.mime)
    else:
        print("Could not detect MIME type")

    logger.info("Attempting to load file: %s", file_path)

    waveform, sr = torchaudio.load(file_path)
    total_duration = waveform.size(1) / sr
    transcript_file_path = f"{file_path}.transcript.txt"

    logger.info("Audio duration: %.2f seconds", total_duration)
    logger.info("Using device: %s", device)
    logger.info("Writing transcript to: %s", transcript_file_path)

    full_transcript = ""
    with open(transcript_file_path, "w", encoding="utf-8") as output_file:
        for i in range(0, math.ceil(total_duration), chunk_seconds):
            start = i
            end = min(i + chunk_seconds, total_duration)
            chunk_waveform = waveform[:, int(start * sr):int(end * sr)]

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp_chunk:
                torchaudio.save(tmp_chunk.name, chunk_waveform, sr)
                try:
                    result = model.transcribe(tmp_chunk.name)
                    chunk_text = result.get("text", "").strip()
                    logger.info("Chunk %d–%d sec: %s", start, end, chunk_text[:60])
                    output_file.write(chunk_text + " ")
                    full_transcript += chunk_text + " "
                    if session_id:
                        upload_transcript_chunk_to_gcs(session_id, chunk_text, i // chunk_seconds)
                except Exception as e:
                    logger.warning("Failed to transcribe chunk %d–%d sec: %s", start, end, str(e))

    if session_id:
        try:
            merge_result = merge_transcript_chunks(session_id, file_path, recording_name)
            publish_to_pubsub(form_id=session_id, recording_name=merge_result['url'])
            merged_text = merge_result['text']
            logger.info("Auto-merged all transcript chunks for session_id: %s", session_id)
        except Exception as merge_exc:
            logger.error("Failed to auto-merge transcript for session %s: %s", session_id, str(merge_exc))

    return full_transcript.strip()
