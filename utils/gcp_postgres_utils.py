"""
Data access module for the Oasis Form application.
Provides functions to fetch and update the `oasis_form` table in Cloud SQL.
"""
import os
import json
from typing import Optional, Dict, List

from google.cloud.sql.connector import Connector, IPTypes
from google.oauth2 import service_account

# Choose your DB driver; pg8000 is a pure-Python driver supported by the connector
DB_DRIVER = "pg8000"

# ─── Cloud SQL / Service Account setup ────────────────────────────────────────
SA_KEY_PATH = os.getenv("GCP_SA_KEY_PATH")

if SA_KEY_PATH:
    creds = service_account.Credentials.from_service_account_file(
        SA_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
else:
    creds = None  # uses Application Default Credentials

CLOUDSQL_INSTANCE = os.getenv("CLOUDSQL_INSTANCE")  # e.g. "project:region:instance"

DB_CONFIG = {
    "dbname": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
    "host": os.getenv("POSTGRES_HOST"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "db_region": os.getenv("CLOUDSQL_REGION", "us-central1")
}

connector = Connector(credentials=creds)

def get_connection():
    """
    Establishes a new database connection using the Cloud SQL Python Connector.
    """
    conn = connector.connect(
        CLOUDSQL_INSTANCE,
        DB_DRIVER,
        user=DB_CONFIG.user,
        password=DB_CONFIG.password,
        db=DB_CONFIG.dbname,
        port=DB_CONFIG.port,
        ip_type=IPTypes.PUBLIC,
    )
    return conn

# ─── CRUD Operations ──────────────────────────────────────────────────────────

def fetch_oasis_form(db_id: int) -> Optional[Dict]:
    """
    Fetch a single row by primary key `id` from the oasis_form table.
    Returns a dict of column -> value, or None if not found.
    """
    sql = """
        SELECT
        id, patientid, providerid, rendered_form_html, recording_path,
        section_name, recorded_date, createddate, updateddate,
        document_creationdate, certify, orgid
        FROM public.oasis_form
        WHERE id = %s and certify = false
        LIMIT 1;
        """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (db_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [col.name for col in cur.description]
            record = dict(zip(cols, row))
            # ensure JSON field is a Python dict
            if isinstance(record["rendered_form_html"], (str, bytes)):
                record["rendered_form_html"] = json.loads(record["rendered_form_html"])
            return record
    finally:
        conn.close()


def update_rendered_form_html(form_id: int, rendered: Dict) -> bool:
    """
    Update the rendered_form_html column for the given form `id`.
    Returns True if a row was updated, False otherwise.
    """
    sql = """
UPDATE public.oasis_form
SET rendered_form_html = %s,
    updateddate = CURRENT_TIMESTAMP
WHERE id = %s;
"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (json.dumps(rendered), form_id))
            updated = cur.rowcount > 0
            conn.commit()
            return updated
    finally:
        conn.close()

# ─── oasis_audio Operations ─────────────────────────────────────────────────────

def fetch_oasis_audio_list(form_id: Optional[int] = None, patient_id: Optional[int] = None) -> List[Dict]:
    """
    Retrieve a list of audio records from oasis_audio.
    Optionally filter by form_id and/or patient_id.
    Returns a list of dicts per row.
    """
    base_sql = """
SELECT
  id, patientid, orgid, formid, gcs_audio_path, sectionfor,
  createddate, updateddate, createdby, updatedby, recordeddate
FROM public.oasis_audio
"""
    filters = []
    params = []
    if form_id is not None:
        filters.append("formid = %s")
        params.append(form_id)
    if patient_id is not None:
        filters.append("patientid = %s")
        params.append(patient_id)
    if filters:
        base_sql += "WHERE " + " AND ".join(filters)
    base_sql += ";"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(base_sql, tuple(params))
            rows = cur.fetchall()
            cols = [col.name for col in cur.description]
            results = [dict(zip(cols, r)) for r in rows]
            return results
    finally:
        conn.close()
