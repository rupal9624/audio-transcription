# routes.py

from flask import Blueprint, request, jsonify, Response
from flasgger import swag_from
import os, logging, uuid, time
from concurrent.futures import ThreadPoolExecutor

from utils.gcp_postgres_utils import fetch_oasis_form, update_rendered_form_html
from utils.gcs_utils import (
    GCSFileNotFoundError, merge_transcript_chunks, download_audio,
    upload_transcript, transcript_exists, get_transcript_content
)
from utils.whisper_utils import transcribe_audio_in_chunks
from utils.pubsub_processing_utils import publish_to_pubsub
from utils.open_ai_utils import fill_json_with_llm

executor = ThreadPoolExecutor(max_workers=4)
logger = logging.getLogger("oasis-transcription")
routes = Blueprint('routes', __name__)

# In-memory job/session tracking
_jobs = {}       # job_id → status
_sessions = {}   # session_id → job_id
JOB_TIMEOUT = 3600  # seconds


def set_job_status(job_id, status):
    _jobs[job_id] = status
    logger.info("Job %s set to %s", job_id, status)


def get_job_status(job_id):
    status = _jobs.get(job_id)
    logger.info("Status check for %s → %s", job_id, status)
    return status


def cancel_job(job_id):
    set_job_status(job_id, "cancelled")


def _background_process(form_id, audio_id, recording_name, job_id, session_id):
    logger.info("Background started: job=%s, session=%s", job_id, session_id)
    if transcript_exists(recording_name):
        set_job_status(job_id, "done")
        publish_to_pubsub(form_id, recording_name)
        return

    set_job_status(job_id, "running")
    try:
        local_audio = download_audio(recording_name)
        if get_job_status(job_id) == "cancelled":
            return

        transcript = transcribe_audio_in_chunks(
            local_audio, model_size="medium", chunk_seconds=30,
            session_id=session_id, recording_name=recording_name
        )
        if get_job_status(job_id) == "cancelled":
            return

        upload_transcript(local_audio, transcript, recording_name)
        set_job_status(job_id, "done")
        publish_to_pubsub(form_id, f"{os.path.splitext(recording_name)[0]}.txt")
    except GCSFileNotFoundError as e:
        logger.error("Background job %s error: %s", job_id, e)
        set_job_status(job_id, "error")
    except Exception:
        logger.exception("Background job %s failed", job_id)
        set_job_status(job_id, "error")


@routes.route("/process-audio", methods=["POST"])
@swag_from({
    "summary": "Start transcription job",
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "recording_name": {"type": "string", "format": "uri"}
                    },
                    "required": ["recording_name"],
                    "example": {
                        "recording_name": "https://storage.googleapis.com/.../rec.wav"
                    }
                }
            }
        }
    },
    "responses": {
        "202": {
            "description": "Transcription started; returns streaming logs",
            "content": {"text/plain": {"schema": {"type": "string"}}}
        },
        "400": {"description": "Missing required parameters"},
        "500": {"description": "Server error"}
    }
})
def process_audio():
    payload = request.get_json(force=True)
    recording_name = payload.get("recording_name")

    if not (recording_name):
        return jsonify(error="Missing 'recording_name'"), 400

    session_id = f"session-{uuid.uuid4().hex[:12]}"
    job_id = f"{uuid.uuid4().hex[:8]}"
    set_job_status(job_id, "pending")
    _sessions[session_id] = job_id

    executor.submit(_background_process, recording_name, job_id, session_id)

    def event_stream():
        yield f"Started job: {job_id}, session: {session_id}\n"
        start = time.time()
        while True:
            status = get_job_status(job_id)
            yield f"Status: {status}\n"
            if status in ("done", "error", "cancelled") or (time.time() - start) > JOB_TIMEOUT:
                yield f"Final: {status}\n"
                break
            time.sleep(20)

    return Response(event_stream(), mimetype="text/plain"), 202


@routes.route("/merge-transcript", methods=["POST"])
@swag_from({
    "summary": "Merge transcript chunks for a session",
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"}
                    },
                    "required": ["session_id"],
                    "example": {"session_id": "session-abcdef123456"}
                }
            }
        }
    },
    "responses": {
        "200": {
            "description": "Merged successfully",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string"},
                            "length": {"type": "integer"},
                            "session_id": {"type": "string"},
                            "merged_blob": {"type": "string"},
                            "url": {"type": "string", "format": "uri"}
                        },
                        "example": {
                            "message": "Transcript merged",
                            "length": 3456,
                            "session_id": "session-abcdef123456",
                            "merged_blob": "transcripts/session-abc123_merged.txt",
                            "url": "https://storage.googleapis.com/.../session-abc123.txt"
                        }
                    }
                }
            }
        },
        "400": {"description": "Missing session_id"},
        "500": {"description": "Processing failed"}
    }
})
def merge_transcript_route():
    data = request.get_json(force=True)
    session_id = data.get("session_id")
    if not session_id:
        return jsonify(error="Missing session_id"), 400

    try:
        result = merge_transcript_chunks(session_id=session_id)
        return jsonify(
            message="Transcript merged",
            length=len(result["text"]),
            session_id=session_id,
            merged_blob=result["blob"],
            url=result["url"]
        ), 200
    except Exception:
        logger.exception("Merge failed for session %s", session_id)
        return jsonify(error="Merge failed"), 500


@routes.route("/cancel/<job_id>", methods=["POST"])
@swag_from({
    "summary": "Cancel an in-progress job",
    "responses": {
        "200": {"description": "Job cancelled"},
        "404": {"description": "Unknown job_id"}
    }
})
def cancel_job_route(job_id):
    if job_id not in _jobs:
        return jsonify(error="Unknown job_id"), 404
    cancel_job(job_id)
    return jsonify(message=f"Job {job_id} cancelled"), 200


@routes.route("/status/<job_id>", methods=["GET"])
@swag_from({
    "summary": "Check job status",
    "parameters": [
        {"name": "job_id", "in": "path", "schema": {"type": "string"}, "required": True}
    ],
    "responses": {
        "200": {
            "description": "Contains job status",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"job_id": {"type": "string"}, "status": {"type": "string"}}}}}
        },
        "404": {"description": "Unknown job_id"}
    }
})
def job_status(job_id):
    status = get_job_status(job_id)
    if status is None:
        return jsonify(error="Unknown job_id"), 404
    return jsonify(job_id=job_id, status=status), 200


@routes.route("/status/session/<session_id>", methods=["GET"])
@swag_from({
    "summary": "Check status by session ID",
    "parameters": [
        {"name": "session_id", "in": "path", "schema": {"type": "string"}, "required": True}
    ],
    "responses": {
        "200": {
            "description": "Contains session → job status",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"session_id": {"type": "string"}, "job_id": {"type": "string"}, "status": {"type": "string"}}}}}
        },
        "404": {"description": "Unknown session_id"}
    }
})
def status_by_session(session_id):
    job_id = _sessions.get(session_id)
    if not job_id:
        return jsonify(error="Unknown session_id"), 404
    status = get_job_status(job_id)
    return jsonify(session_id=session_id, job_id=job_id, status=status), 200


@routes.route("/health", methods=["GET"])
@swag_from({
    "summary": "Health check",
    "responses": {200: {"description": "OK"}}
})
def health_check():
    return jsonify(status="ok"), 200


@routes.route("/test", methods=["GET"])
@swag_from({
    "summary": "Connectivity test",
    "responses": {200: {"description": "Service is running"}}
})
def test():
    return "Flask is running!", 200
