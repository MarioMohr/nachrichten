"""
This script serves as the gatekeeper for the incoming video queue.
It utilizes an Ollama reasoning model to check for geopolitical, market, and social relevance.
To preserve the 20 requests per day limit of the Gemini API down the line,
this script enforces a hard cap of 20 approved videos per execution batch.
It processes only videos discovered within the last 24 hours, prioritizing the newest ones.
"""

import os
import sys
import json
import sqlite3
import requests
from datetime import datetime, timedelta

# Configuration paths and environment definitions
DB_PATH = "data/video_ids.db"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama_backend:11434")
OLLAMA_MODEL = "qwen2.5:1.5b"

# Safety capping mechanism to guarantee Gemini safety bounds
DAILY_GEMINI_CAP = 20

def check_title_with_ollama(title):
    """
    Queries the Ollama local backend container to evaluate title relevance.
    Evaluates based on global impact, Germany/Malaysia relevance, 
    deaf community context, or trading market influence.
    """
    url = f"{OLLAMA_HOST}/api/generate"
    
    prompt = f"""
    Analyze the following video title carefully: "{title}"
    
    Determine if this content is highly relevant or impactful based on these strict guidelines:
    1. Global impact: Does it cover major geopolitical shifts or breaking global changes?
    2. Regional relevance: Is it directly relevant to Germany or Malaysia?
    3. Community interest: Is it highly important to the deaf community or accessibility developments?
    4. Market influence: Does it significantly influence the trading market, stocks, or major economic sectors?
    5. Actionable changes: Does it discuss active changes, ongoing scenarios, or near future predictions rather than unimpactful historical reviews?
    
    You must respond ONLY with a valid JSON object matching this schema exactly:
    {{
        "worth_processing": boolean,
        "reason": "Clear English explanation detailing your decision matching the criteria"
    }}
    """
    
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False
    }
    
    try:
        response = requests.post(url, json=payload, timeout=300)
        if response.status_code == 200:
            result = response.json()
            response_text = result.get("response", "{}")
            parsed_data = json.loads(response_text)
            return parsed_data.get("worth_processing", False)
        else:
            print(f"Ollama returned an unexpected HTTP status code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as error:
        print(f"Network error trying to contact Ollama backend service: {error}")
        return False
    except json.JSONDecodeError as error:
        print(f"Failed to parse structured JSON response from Ollama: {error}")
        return False

def run_gatekeeper():
    """Main pipeline execution loop for filtering and status distribution via Ollama."""
    
    if not os.path.exists(DB_PATH):
        print(f"Error: Target database file not found at {DB_PATH}")
        sys.exit(1)
        
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()
    
    # Fetch all video tracking references currently marked as new ordered by newest first
    try:
        cursor.execute("SELECT video_id, title, discovered_at FROM video_queue WHERE ai_status = 'new' ORDER BY discovered_at DESC;")
        incoming_videos = cursor.fetchall()
    except sqlite3.Error as error:
        print(f"Database error while querying queue entries: {error}")
        connection.close()
        sys.exit(1)
        
    if not incoming_videos:
        print("No new videos discovered in the queue processing pipe.")
        connection.close()
        return

    approved_count = 0
    print(f"Found {len(incoming_videos)} new video files to evaluate.")
    
    # SQLite CURRENT_TIMESTAMP uses UTC
    now_utc = datetime.utcnow()

    for video_id, title, discovered_at_str in incoming_videos:
        
        # Parse the timestamp and check if it is older than 24 hours
        try:
            discovered_at = datetime.strptime(discovered_at_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            discovered_at = now_utc
            
        if now_utc - discovered_at > timedelta(hours=24):
            print(f"Video {video_id} is older than 24 hours. Setting state to ignored.")
            cursor.execute("UPDATE video_queue SET ai_status = 'ignored' WHERE video_id = ?;", (video_id,))
            connection.commit()
            continue

        # Check if the allowed daily allotment has already been fully reached in this execution loop
        if approved_count >= DAILY_GEMINI_CAP:
            print(f"Daily safe threshold allocation limit of {DAILY_GEMINI_CAP} videos achieved. Postponing remaining files.")
            break

        # Reasoning logic step via local LLM infrastructure
        print(f"Evaluating relevance matrix via Ollama for title: {title}")
        is_relevant = check_title_with_ollama(title)
        
        if is_relevant:
            print(f"Video {video_id} accepted and promoted to pending status.")
            cursor.execute("UPDATE video_queue SET ai_status = 'pending' WHERE video_id = ?;", (video_id,))
            approved_count += 1
        else:
            print(f"Video {video_id} rejected by criteria check. Setting state to ignored.")
            cursor.execute("UPDATE video_queue SET ai_status = 'ignored' WHERE video_id = ?;", (video_id,))
            
        connection.commit()

    print(f"Gatekeeper loop completed. Approved {approved_count} files for subsequent step steps.")
    connection.close()

if __name__ == "__main__":
    run_gatekeeper()

