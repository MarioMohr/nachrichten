"""
This script connects to a local database and processes one video at a time.
It requests JSON from the Transcript API, extracts the language code, and 
combines all transcript segments into one single text block.
The final structured data is saved as a custom JSON file.
"""

import sqlite3
import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("TRANSCRIPT_API_KEY")
DB_QUEUE = "data/video_ids.db"
TEMP_DIR = "data/temp_transcripts"
API_URL = "https://transcriptapi.com/api/v2/youtube/transcript"

def setup_environment():
    os.makedirs(TEMP_DIR, exist_ok=True)

def get_next_video():
    conn = sqlite3.connect(DB_QUEUE)
    cursor = conn.cursor()
    cursor.execute('SELECT video_id FROM video_queue WHERE ai_status = "pending" LIMIT 1;')
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def update_status(video_id, status):
    conn = sqlite3.connect(DB_QUEUE)
    conn.execute('UPDATE video_queue SET ai_status = ? WHERE video_id = ?;', (status, video_id))
    conn.commit()
    conn.close()

def process_video():
    setup_environment()
    
    if not API_KEY:
        print("Error: TRANSCRIPT_API_KEY is not set in the .env file")
        return

    video_id = get_next_video()
    
    if not video_id:
        print("No pending videos found in the database")
        return

    print(f"Processing video: {video_id}")
    
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {"video_url": video_id, "format": "json"}
    
    try:
        response = requests.get(API_URL, headers=headers, params=params, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            
            video_id_val = data.get("video_id", video_id)
            language_val = data.get("language", "unknown")
            transcript_segments = data.get("transcript", [])
            
            # Combine all text segments into one single continuous string
            text_chunks = [segment.get("text", "").strip() for segment in transcript_segments if segment.get("text", "").strip()]
            formatted_transcript = " ".join(text_chunks)
            
            output_data = {
                "video_id": video_id_val,
                "language": language_val,
                "transcript": formatted_transcript
            }
            
            file_path = os.path.join(TEMP_DIR, f"{video_id}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=4)
                
            update_status(video_id, "extracted")
            print(f"Transcript successfully saved to {file_path}")
            
        else:
            print(f"Failed to fetch transcript. Unexpected Status Code: {response.status_code}")
            update_status(video_id, "error")
            
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        update_status(video_id, "error")

if __name__ == "__main__":
    process_video()

