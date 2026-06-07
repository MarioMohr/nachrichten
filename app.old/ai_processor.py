import os
import time
import sqlite3
import requests
from google import genai
from google.genai.errors import APIError
from youtube_transcript_api import YouTubeTranscriptApi

DB_QUEUE_PATH = "data/video_ids.db"
DB_TICKER_PATH = "data/news_ticker.db"

def init_ticker_database():
    """Initializes the news ticker database if it does not exist."""
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
        
    conn = sqlite3.connect(DB_TICKER_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE,
            channel_handle TEXT,
            ticker_text TEXT,
            bot_status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    print("[PROTOCOL] Database initialized.")

def get_video_transcript(video_id):
    """Fetches video transcript from YouTube using the updated library syntax."""
    try:
        print(f"[PROTOCOL] Attempting to fetch transcript for video: {video_id}")
        yt_api = YouTubeTranscriptApi()
        transcript_list = yt_api.list(video_id)
        transcript = transcript_list.find_transcript(['en', 'de'])
        transcript_data = transcript.fetch()
        return " ".join([entry['text'] for entry in transcript_data])
    except Exception as e:
        print(f"[PROTOCOL] Could not fetch transcript (using title fallback): {e}")
        return None

def translate_to_german(text):
    """Translates text to German via DeepL only if necessary."""
    api_key = os.environ.get("DEEPL_API_KEY")
    if not api_key:
        print("[!] Warning: DEEPL_API_KEY is missing. Skipping translation.")
        return text

    url = "https://api-free.deepl.com/v2/translate"
    payload = {"text": [text], "target_lang": "DE"}
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            print("[PROTOCOL] Translation successful.")
            return response.json()["translations"][0]["text"]
        else:
            print(f"[!] DeepL API Error: {response.status_code} {response.text}")
    except Exception as e:
        print(f"[!] DeepL connection issue: {e}")
        
    return text

def call_gemini_stage1(client, video_id, title):
    """Generates a comprehensive summary and identifies the source language."""
    transcript = get_video_transcript(video_id)
    content = transcript if transcript else title
    
    print("[PROTOCOL] Sending content to Gemini for summarization.")
    prompt = (
        f"Analyze this content. Provide the language code (DE or EN) followed by a pipe character, "
        f"then a short, comprehensive news ticker summary based on this content. "
        f"Do not use markdown formatting. Keep it informative.\n\n"
        f"Content: {content[:15000]}"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        if response.text and "|" in response.text:
            parts = response.text.split("|", 1)
            print(f"[PROTOCOL] Summary generated. Language: {parts[0].strip()}")
            return True, parts[0].strip(), parts[1].strip()
        return False, None, None
    except APIError as e:
        if e.code == 429:
            print("[!] Gemini Rate Limit hit during Stage 1.")
            return False, None, None
        return True, "EN", title
    except Exception as e:
        print(f"[!] Stage 1 Error: {e}")
        return False, None, None

def call_gemini_stage2(client, new_ticker, existing_tickers):
    """Validates and deduplicates headlines against existing database entries."""
    if not existing_tickers:
        return True, "UNIQUE", None, new_ticker

    history = "\n".join([f"ID: {row[0]} | {row[1]}" for row in existing_tickers])
    
    prompt = (
        f"You are a validation engine sorting news summaries in German.\n"
        f"Recent Logged Summaries:\n{history}\n\n"
        f"New Candidate Summary: {new_ticker}\n\n"
        f"Select one phrase: DUPLICATE, UNIQUE, or FUSED || <ID> || <New Text>.\n"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        output = response.text.strip() if response.text else "UNIQUE"
        print(f"[PROTOCOL] Validation outcome: {output}")

        if "DUPLICATE" in output:
            return True, "DUPLICATE", None, None
        elif "FUSED" in output and "||" in output:
            parts = [p.strip() for p in output.split("||")]
            if len(parts) >= 3:
                return True, "FUSED", parts[1], parts[2]
        
        return True, "UNIQUE", None, new_ticker
            
    except Exception as e:
        print(f"[!] Stage 2 Error: {e}")
        return True, "UNIQUE", None, new_ticker

def run_processor():
    """Orchestrates the processing pipeline."""
    init_ticker_database()
    
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if api_key else None
    
    if not client:
        print("[-] Error: GEMINI_API_KEY is not configured.")
        return

    while True:
        print("\n[~] AI Processor cycle initiated.")
        
        q_conn = sqlite3.connect(DB_QUEUE_PATH)
        q_cursor = q_conn.cursor()
        
        try:
            q_cursor.execute("SELECT video_id, channel_handle, title, discovered_at FROM video_queue WHERE ai_status = 'pending' ORDER BY discovered_at ASC;")
            pending_videos = q_cursor.fetchall()
        except sqlite3.OperationalError:
            print("[-] Queue table not ready.")
            q_conn.close()
            time.sleep(120)
            continue
            
        if not pending_videos:
            print("[~] No pending videos. Sleeping.")
            q_conn.close()
            time.sleep(120)
            continue

        t_conn = sqlite3.connect(DB_TICKER_PATH)
        t_cursor = t_conn.cursor()
        
        for vid_id, handle, title, timestamp in pending_videos:
            print(f"[>] Processing: {handle} | {title}")
            
            time.sleep(5)
            
            ai_ok, lang, raw_ticker = call_gemini_stage1(client, vid_id, title)
            if not ai_ok:
                print("[!] Stopping batch due to AI failure or rate limit.")
                break
                
            final_text = translate_to_german(raw_ticker) if lang == "EN" else raw_ticker
            
            t_cursor.execute("SELECT id, ticker_text FROM tickers ORDER BY id DESC LIMIT 20;")
            history = t_cursor.fetchall()
            
            ai_ok, status, replace_id, final_text = call_gemini_stage2(client, final_text, history)
                
            if status == "UNIQUE":
                t_cursor.execute("INSERT INTO tickers (video_id, channel_handle, ticker_text, created_at) VALUES (?, ?, ?, ?);",
                                (vid_id, handle, final_text, timestamp))
            elif status == "FUSED":
                t_cursor.execute("UPDATE tickers SET ticker_text = ?, bot_status = 'pending', created_at = ? WHERE id = ?;",
                                (final_text, timestamp, replace_id))
            
            q_cursor.execute("UPDATE video_queue SET ai_status = 'completed' WHERE video_id = ?;", (vid_id,))
            
            q_conn.commit()
            t_conn.commit()

        q_conn.close()
        t_conn.close()
        print("[~] Cycle complete. Sleeping.")
        time.sleep(120)

if __name__ == "__main__":
    run_processor()

