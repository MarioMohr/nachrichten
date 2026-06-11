"""
This unified script processes the incoming video queue entirely in memory.
It filters out stale videos, uses Ollama to verify relevance, fetches the transcript,
and parses it through Gemini to generate the final news ticker format.
It runs in a continuous hourly loop and strictly manages a rolling 24 hour Gemini API quota.
"""

import os
import sys
import json
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from google import genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

DB_QUEUE = "data/video_ids.db"
DB_TICKER = "data/news_ticker.db"
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama_backend:11434")
OLLAMA_MODEL = "qwen2.5:1.5b"
TRANSCRIPT_API_KEY = os.getenv("TRANSCRIPT_API_KEY")
TRANSCRIPT_API_URL = "https://transcriptapi.com/api/v2/youtube/transcript"
DAILY_GEMINI_CAP = 20

client = genai.Client()

class NewsTickerAnalysis(BaseModel):
    problem: str = Field(description="Detaillierte Erklärung des Problems im Video.")
    solution: str = Field(description="Detaillierte Erklärung der vorgeschlagenen Lösung.")
    consequences: str = Field(description="Mögliche Konsequenzen des Problems oder der Lösung.")

def init_databases():
    if not os.path.exists(DB_QUEUE):
        print(f"Error: Target database file not found at {DB_QUEUE}")
        sys.exit(1)
        
    conn = sqlite3.connect(DB_TICKER)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tickers (
            video_id TEXT PRIMARY KEY,
            problem TEXT,
            solution TEXT,
            consequences TEXT,
            status TEXT DEFAULT "new",
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    
    # Safely upgrade the existing table schema to include the new status field
    cursor.execute("PRAGMA table_info(tickers);")
    columns = [col[1] for col in cursor.fetchall()]
    if "status" not in columns:
        cursor.execute('ALTER TABLE tickers ADD COLUMN status TEXT DEFAULT "new";')
        
    conn.commit()
    conn.close()

def get_remaining_daily_quota():
    conn = sqlite3.connect(DB_TICKER)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tickers WHERE created_at >= datetime('now', '-24 hours');")
    used_quota = cursor.fetchone()[0]
    conn.close()
    return max(0, DAILY_GEMINI_CAP - used_quota)

def check_title_with_ollama(title):
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
            try:
                parsed_data = json.loads(response_text)
                return parsed_data.get("worth_processing", False)
            except json.JSONDecodeError as e:
                print(f"Ollama JSON structure failed: {e}")
                return None
        return False
    except Exception as error:
        print(f"Ollama connection error: {error}")
        return None

def fetch_transcript(video_id):
    if not TRANSCRIPT_API_KEY:
        print("Error: TRANSCRIPT_API_KEY is not set.")
        return None, None

    headers = {"Authorization": f"Bearer {TRANSCRIPT_API_KEY}"}
    params = {"video_url": video_id, "format": "json"}
    
    try:
        response = requests.get(TRANSCRIPT_API_URL, headers=headers, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            language = data.get("language", "unknown")
            transcript_segments = data.get("transcript", [])
            text_chunks = [segment.get("text", "").strip() for segment in transcript_segments if segment.get("text", "").strip()]
            return " ".join(text_chunks), language
        else:
            print(f"Failed to fetch transcript. Status Code: {response.status_code}")
            return None, None
    except Exception as e:
        print(f"Transcript fetch error: {e}")
        return None, None

def analyze_with_gemini(transcript_text, language):
    prompt = f"""
    Du bist ein Assistent, der verständliche Zusammenfassungen für gehörlose Menschen erstellt.
    Analysiere das folgende Video Transkript.
    
    WICHTIG: Wenn das Transkript beziehungsweise die Sprache nicht Deutsch ist, musst du alle deine Erklärungen direkt während der Analyse ins Deutsche übersetzen. Das Endergebnis MUSS komplett auf Deutsch sein.

    Extrahiere die Informationen exakt nach diesen Regeln:
    - problem: Detaillierte Erklärung des Problems im Video.
    - solution: Detaillierte Erklärung der vorgeschlagenen Lösung.
    - consequences: Mögliche Konsequenzen des Problems oder der Lösung.

    Hier ist das Transkript:
    {transcript_text}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": NewsTickerAnalysis,
            }
        )
        result_json = json.loads(response.text)
        return NewsTickerAnalysis(**result_json)
    except Exception as e:
        raise e

def save_to_ticker(video_id, analysis_data):
    conn = sqlite3.connect(DB_TICKER)
    conn.execute('''
        INSERT OR REPLACE INTO tickers (video_id, problem, solution, consequences, status)
        VALUES (?, ?, ?, ?, "new");
    ''', (video_id, analysis_data.problem, analysis_data.solution, analysis_data.consequences))
    conn.commit()
    conn.close()

def update_queue_status(video_id, status):
    conn = sqlite3.connect(DB_QUEUE)
    conn.execute("UPDATE video_queue SET ai_status = ? WHERE video_id = ?;", (status, video_id))
    conn.commit()
    conn.close()

def run_pipeline_loop():
    interval_seconds = 3600
    
    while True:
        print("\n[~] AI Pipeline cycle initiated.")
        init_databases()
        
        remaining_quota = get_remaining_daily_quota()
        print(f"[i] Gemini API Quota: {remaining_quota} requests remaining for the rolling 24 hour window.")
        
        if remaining_quota <= 0:
            print("[!] Daily Gemini cap reached. Waiting for the next cycle.")
            time.sleep(interval_seconds)
            continue
            
        conn = sqlite3.connect(DB_QUEUE)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT video_id, title, discovered_at FROM video_queue WHERE ai_status = 'new' ORDER BY discovered_at DESC;")
            incoming_videos = cursor.fetchall()
        except sqlite3.Error as error:
            print(f"Database query error: {error}")
            conn.close()
            time.sleep(interval_seconds)
            continue
            
        if not incoming_videos:
            print("No new videos discovered in the queue processing pipe.")
            conn.close()
            time.sleep(interval_seconds)
            continue

        approved_count = 0
        now_utc = datetime.utcnow()

        for video_id, title, discovered_at_str in incoming_videos:
            if approved_count >= remaining_quota:
                print(f"Reached available quota limit of {remaining_quota} for this cycle. Postponing remaining files.")
                break

            try:
                discovered_at = datetime.strptime(discovered_at_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                discovered_at = now_utc
                
            if now_utc - discovered_at > timedelta(hours=24):
                print(f"Video {video_id} is older than 24 hours. Setting state to ignored.")
                update_queue_status(video_id, "ignored")
                continue

            print(f"Evaluating relevance via Ollama: {title}")
            is_relevant = check_title_with_ollama(title)
            
            if is_relevant is None:
                print(f"Ollama evaluation failed for {video_id}. Retrying next cycle.")
                time.sleep(10)
                continue
            elif not is_relevant:
                print(f"Video {video_id} rejected by Ollama. Setting state to ignored.")
                update_queue_status(video_id, "ignored")
                continue
                
            print(f"Video {video_id} approved. Fetching transcript...")
            transcript_text, language = fetch_transcript(video_id)
            
            if not transcript_text:
                print(f"Failed to get transcript for {video_id}. Marking as error.")
                update_queue_status(video_id, "error")
                continue
                
            print("Transcript acquired. Analyzing with Gemini...")
            try:
                analysis_result = analyze_with_gemini(transcript_text, language)
                save_to_ticker(video_id, analysis_result)
                update_queue_status(video_id, "completed")
                approved_count += 1
                print(f"Successfully processed and saved news ticker for {video_id}.")
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "503" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or "UNAVAILABLE" in error_msg:
                    print("Gemini rate limit or server overload reached. Reverting status to new and sleeping.")
                    update_queue_status(video_id, "new")
                    time.sleep(60)
                    continue
                else:
                    print(f"Gemini processing error: {e}")
                    update_queue_status(video_id, "error")

        print(f"Pipeline cycle complete. Processed {approved_count} videos.")
        conn.close()
        
        print(f"[~] Sleeping for {interval_seconds} seconds...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    run_pipeline_loop()

