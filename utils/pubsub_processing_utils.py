import uuid
import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from google.cloud import pubsub_v1


executor = ThreadPoolExecutor(max_workers=4)
logger = logging.getLogger("oasis-transcription.pubsub_processing_utils")

# Pub/Sub setup
PUBSUB_TOPIC = os.getenv("PUBSUB_TOPIC")  # e.g. "projects/your-project/topics/transcripts"
publisher = pubsub_v1.PublisherClient()

def publish_to_pubsub(db_id: int, transcript_name: str) -> None:
    """
    Publish a JSON message to Pub/Sub with the form ID and transcript filename.
    """
    if not PUBSUB_TOPIC:
        logger.warning("PUBSUB_TOPIC not set; skipping publish")
        return

    message = {
        "form_id": db_id,
        "transcript": transcript_name
    }
    data = json.dumps(message).encode("utf-8")
    future = publisher.publish(PUBSUB_TOPIC, data)
    future.add_done_callback(
        lambda f: logger.info("Published message to %s: %s", PUBSUB_TOPIC, f.result())
    )