# main.py

from __future__ import annotations

import logging
import os
from dotenv import load_dotenv

# Load variables from .env file before anything else
load_dotenv()

from flask import Flask, request, jsonify
from flask_cors import CORS
from flasgger import Swagger, swag_from
from routes import routes  # import the Blueprint

# -------------------------------------------------------------
# Logging setup
# -------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("oasis-transcription")

# -------------------------------------------------------------
# Flask app & CORS
# -------------------------------------------------------------
app = Flask(__name__)
# Allow only localhost (dev) and Cloud Run domain (set via ENV)
allowed = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:8080,https://oasis-transcription-m5h6w2scva-uc.a.run.app,http://oasis-transcription-m5h6w2scva-uc.a.run.app"
).split(",")
CORS(app, origins=allowed)

Swagger(app)

# Register routes Blueprint
app.register_blueprint(routes)

# -------------------------------------------------------------
# Entrypoint
# -------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Flask application …")
    port = int(os.getenv("PORT", 8082))
    app.run(host="0.0.0.0", port=port)
