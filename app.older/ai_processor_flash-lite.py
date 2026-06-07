import os
import time
import sqlite3
import requests
from google import genai
from youtube_transcript_api import YouTubeTranscriptApi

DB_QUEUE_PATH = "data/video_ids.db"
DB_TICKER_PATH = "data/news_ticker.db"

def init_ticker_database():
    """Initializes the news ticker database and logging schema."""
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
    print("[LOG] Database initialized successfully.")

def get_video_transcript(video_id):
    """Fetches video transcript from YouTube with protocol logging."""
    try:
        print(f"[PROTOCOL] Fetching transcript for: {video_id}")
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'de'])
        return " ".join([entry['text'] for entry in transcript])
    except Exception as e:
        print(f"[PROTOCOL] No transcript available for {video_id}: {e}")
        return None

def translate_to_german(text):
    """Translates text to German via DeepL with character-saving checks."""
    api_key = os.environ.get("DEEPL_API_KEY")
    if not api_key:
        print("[LOG] No DeepL API Key found, skipping translation.")
        return text
    
    print("[PROTOCOL] Sending text to DeepL for translation.")
    url = "https://api-free.deepl.com/v2/translate"
    payload = {"text": [text], "target_lang": "DE"}
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}", "Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            print("[LOG] Translation successful.")
            return response.json()["translations"][0]["text"]
    except Exception as e:
        print(f"[LOG] Translation error, falling back to original: {e}")
    return text

def call_gemini_stage1(client, video_id, title):
    """Generates comprehensive summary using transcript data."""
    transcript = get_video_transcript(video_id)
    content = transcript if transcript else title
    
    print(f"[PROTOCOL] Sending content to Gemini for summarization.")
    prompt = (
        f"Analyze this content. Provide the language code (DE or EN) followed by a pipe character, "
        f"then a comprehensive, multi-sentence news summary. Provide a complete summary, not just a headline.\n\n"
        f"Content: {content[:15000]}"
    )
    
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        if response.text and "|" in response.text:
            parts = response.text.split("|", 1)
            print("[LOG] Gemini summary generated.")
            return True, parts[0].strip(), parts[1].strip()
    except Exception as e:
        print(f"[LOG] Gemini API error: {e}")
    return False, None, None

def run_processor():
    """Main orchestration loop with process logging."""
    init_ticker_database()
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if api_key else None
    
    if not client:
        print("[ERR] GEMINI_API_KEY missing.")
        return

    while True:
        print("\n[~] Checking for pending videos...")
        q_conn = sqlite3.connect(DB_QUEUE_PATH)
        q_cursor = q_conn.cursor()
        q_cursor.execute("SELECT video_id, channel_handle, title FROM video_queue WHERE ai_status = 'pending' LIMIT 3;")
        pending = q_cursor.fetchall()
        
        if not pending:
            print("[~] No pending videos. Sleeping 2 mins.")
            q_conn.close()
            time.sleep(120)
            continue

        t_conn = sqlite3.connect(DB_TICKER_PATH)
        t_cursor = t_conn.cursor()

        for vid_id, handle, title in pending:
            print(f"[>] Processing video: {vid_id}")
            ai_ok, lang, raw_summary = call_gemini_stage1(client, vid_id, title)
            
            if ai_ok:
                final_text = translate_to_german(raw_summary) if lang == "EN" else raw_summary
                t_cursor.execute(
                    "INSERT OR IGNORE INTO tickers (video_id, channel_handle, ticker_text) VALUES (?, ?, ?);",
                    (vid_id, handle, final_text)
                )
                q_cursor.execute("UPDATE video_queue SET ai_status = 'completed' WHERE video_id = ?;", (vid_id,))
                t_conn.commit()
                q_conn.commit()
                print(f"[+] Summary stored for {handle}.")

        q_conn.close()
        t_conn.close()
        time.sleep(60)

if __name__ == "__main__":
    run_processor()

