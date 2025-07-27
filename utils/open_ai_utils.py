import openai
import os
import json
from flask import Flask, request, jsonify

openai.api_key = os.getenv("OPENAI_API_KEY")

def fill_json_with_llm(template: dict, transcript: str) -> dict:
    """Use GPT to fill the form template."""
    prompt = f"""
        You are given a JSON form template (OASIS form JSON version) and a transcript.
        Fill out the answers in the value field of the template. Answer or value field available part of the transcript.
        Do not change the JSON format of the template. Only find the answers and add it in the respective value field of the question.
        If answers are not found in the transcript leave it as is unanswered.
        Template:
        {json.dumps(template, indent=2)}
        Transcript:
        \"\"\"{transcript}\"\"\"
    """
    resp = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = resp.choices[0].message["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # attempt JSON extraction
        start = content.find("{")
        end = content.rfind("}") + 1
        return json.loads(content[start:end])