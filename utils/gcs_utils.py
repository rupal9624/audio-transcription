"""
gcs_utils.py

Utility module for interacting with Google Cloud Storage:
- Upload/download audio and transcript files
- Handle chunked and merged transcript files
"""
import os
import tempfile
import logging
from urllib.parse import urlparse
from typing import Optional
from google.cloud import storage
from google.oauth2 import service_account

logger = logging.getLogger("gcs_utils")

# Environment configuration
GCP_BUCKET_NAME = os.getenv("GCP_BUCKET_NAME")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
SA_KEY_PATH = os.getenv("GCP_SA_KEY_PATH")
TRANSCRIPT_BUCKET_PATH = os.getenv("TRANSCRIPT_BUCKET_PATH", "transcripts")
RECORDING_BUCKET_PATH = os.getenv("RECORDING_BUCKET_PATH", "recordings")

# Initialize GCS client
if SA_KEY_PATH:
    creds = service_account.Credentials.from_service_account_file(
        SA_KEY_PATH, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    storage_client = storage.Client(credentials=creds, project=GCP_PROJECT_ID)
else:
    storage_client = storage.Client()


def parse_blob_path(recording_url_or_path: str) -> str:
    """Extract blob path from GCS URL or return as-is."""
    if recording_url_or_path.startswith("https://storage.googleapis.com/"):
        parts = urlparse(recording_url_or_path)
        return parts.path.lstrip("/").split("/", 1)[1]
    elif recording_url_or_path.startswith("gs://"):
        return recording_url_or_path.split("/", 3)[-1]
    return recording_url_or_path

def extract_filename(recording_name: str) -> str:
    """Extracts clean filename from a URL or GCS path."""
    if recording_name.startswith("http"):
        return os.path.basename(urlparse(recording_name).path)
    return os.path.basename(recording_name)

class GCSFileNotFoundError(Exception):
    def __init__(self, blob_path, bucket):
        self.message = f"GCS file '{blob_path}' not found in bucket '{bucket}'"
        super().__init__(self.message)


def download_audio(recording_name: str) -> str:
    """Download an audio blob and return the local file path."""
    blob_path = parse_blob_path(recording_name)
    logger.info("Downloading from: %s", blob_path)
    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    blob = bucket.blob(blob_path)
    if not blob.exists():
        raise GCSFileNotFoundError(blob_path, GCP_BUCKET_NAME)
    suffix = os.path.splitext(blob_path)[1] or ".dat"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    blob.download_to_filename(tmp.name)
    return tmp.name

def upload_transcript_chunk_to_gcs(session_id: str, chunk_text: str, chunk_index: int):
    """Upload an individual chunk of transcript text to GCS."""
    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    blob_name = f"{TRANSCRIPT_BUCKET_PATH}/{session_id}_part{chunk_index}.txt"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(chunk_text, content_type="text/plain")
    logger.info("Uploaded transcript chunk to GCS: %s", blob_name)


def upload_transcript(local_audio: str, transcript_text: str, recording_name: str) -> str:
    """Upload final transcript text using original audio name."""
    clean_name = extract_filename(recording_name)
    base_name = os.path.splitext(clean_name)[0]
    txt_blob = f"{base_name}.txt"

    with open(local_audio + ".txt", "w", encoding="utf-8") as f:
        f.write(transcript_text)

    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    blob_path = f"{TRANSCRIPT_BUCKET_PATH}/{txt_blob}"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(transcript_text, content_type="text/plain")

    logger.info("Uploaded full transcript to GCS: %s", blob_path)
    return blob_path


def merge_transcript_chunks(session_id: str, local_audio: str = None, recording_name: str = None) -> dict:
    """Merge all transcript parts from GCS into a full transcript and upload."""
    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    prefix = f"{TRANSCRIPT_BUCKET_PATH}/{session_id}_part"
    blobs = sorted(bucket.list_blobs(prefix=prefix), key=lambda b: b.name)

    merged_text = ""
    for blob in blobs:
        chunk = blob.download_as_text()
        merged_text += chunk.strip() + " "
        logger.info("Merged: %s (%d chars)", blob.name, len(chunk))

    # Upload merged result as final transcript
    if local_audio and recording_name:
        upload_transcript(local_audio, merged_text.strip(), recording_name)

    # Cleanup: delete all chunk blobs
    for blob in blobs:
        try:
            blob.delete()
            logger.info("Deleted chunk blob: %s", blob.name)
        except Exception as e:
            logger.warning("Failed to delete chunk blob %s: %s", blob.name, str(e))

    # Build and return public URL
    clean_name = extract_filename(recording_name)
    base_name = os.path.splitext(clean_name)[0]
    final_blob = f"{TRANSCRIPT_BUCKET_PATH}/{base_name}.txt"
    public_url = f"https://storage.googleapis.com/{GCP_BUCKET_NAME}/{final_blob}"

    return {
        "text": merged_text.strip(),
        "blob": final_blob,
        "url": public_url,
    }


def transcript_exists(recording_name: str) -> bool:
    """Check if final transcript already exists in GCS."""
    clean_name = extract_filename(recording_name)
    base = os.path.splitext(clean_name)[0]
    blob_path = f"{TRANSCRIPT_BUCKET_PATH}/{base}.txt"
    return exists_in_bucket(blob_path)


def exists_in_bucket(blob_path: str) -> bool:
    """Check if blob exists in GCS bucket."""
    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    blob = bucket.blob(blob_path)
    return blob.exists()


def get_transcript_content(recording_name: str) -> str:
    """Retrieve final transcript text from GCS."""
    clean_name = extract_filename(recording_name)
    base = os.path.splitext(clean_name)[0]
    blob_path = f"{TRANSCRIPT_BUCKET_PATH}/{base}.txt"

    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    blob = bucket.blob(blob_path)
    if not blob.exists():
        logger.warning("Transcript not found: gs://%s/%s", GCP_BUCKET_NAME, blob_path)
        raise FileNotFoundError(f"Transcript '{blob_path}' not found in bucket '{GCP_BUCKET_NAME}'")

    return blob.download_as_text(encoding="utf-8")