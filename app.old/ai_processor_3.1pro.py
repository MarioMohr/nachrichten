import os
import time
import sqlite3
import requests
from google import genai
from google.genai.errors import APIError

DB_QUEUE_PATH = "data/video_ids.db"
DB_TICKER_PATH = "data/news_ticker.db"

def init_ticker_database():
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

def translate_to_german(text):
    api_key = os.environ.get("DEEPL_API_KEY")
    if not api_key:
        print("[!] Warning: DEEPL_API_KEY is missing. Skipping translation.")
        return text

    url = "https://api-free.deepl.com/v2/translate"
    payload = {
        "text": [text],
        "target_lang": "DE"
    }
    headers = {
        "Authorization": f"DeepL-Auth-Key {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data["translations"][0]["text"]
        else:
            print(f"[!] DeepL API Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[!] DeepL connection issue: {e}")
        
    return text

def call_gemini_stage1(client, title):
    prompt = (
        f"Transform this raw video title into a short, punchy, ultra compact news ticker headline. "
        f"Do not use markdown formatting, prefixes, or introductory text. "
        f"Keep it as a single crisp statement in the language of the title.\n\nTitle: {title}"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        if response.text:
            return True, response.text.strip()
        return False, None
    except APIError as e:
        if e.code == 429:
            return False, None
        return True, title
    except Exception:
        return False, None

def call_gemini_stage2(client, new_ticker, existing_tickers):
    if not existing_tickers:
        return True, "UNIQUE", None, new_ticker

    history = "\n".join([f"ID: {row[0]} | {row[1]}" for row in existing_tickers])
    
    prompt = (
        f"You are a validation engine sorting headlines for a news ticker in German.\n"
        f"Recent Logged Headlines:\n{history}\n\n"
        f"New Candidate Headline: {new_ticker}\n\n"
        f"Analyze the context. You must select one of these three exact output phrases:\n"
        f"1. If the candidate is a duplicate topic adding no new details, answer exactly: DUPLICATE\n"
        f"2. If the candidate matches a topic above but brings new numbers or facts, fuse them into one "
        f"updated summary string. Answer exactly: FUSED || <ID of the matched headline> || Your New Fused Text\n"
        f"3. If it is completely different news, answer exactly: UNIQUE\n"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        output = response.text.strip() if response.text else "UNIQUE"

        if "DUPLICATE" in output:
            return True, "DUPLICATE", None, None
        elif "FUSED" in output and "||" in output:
            parts = [p.strip() for p in output.split("||")]
            if len(parts) >= 3:
                replace_id = parts[1]
                fused_text = parts[2]
                return True, "FUSED", replace_id, fused_text
        
        return True, "UNIQUE", None, new_ticker
            
    except APIError as e:
        if e.code == 429:
            return False, None, None, None
        return True, "UNIQUE", None, new_ticker
    except Exception:
        return True, "UNIQUE", None, new_ticker

def run_processor():
    init_ticker_database()
    
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key) if api_key else None
    
    if not client:
        print("[-] Error: GEMINI_API_KEY is not configured.")
        return

    while True:
        print("\n[~] AI Processor cycle initiated. Checking for pending videos...")
        
        q_conn = sqlite3.connect(DB_QUEUE_PATH)
        q_cursor = q_conn.cursor()
        
        try:
            q_cursor.execute("SELECT video_id, channel_handle, title, discovered_at FROM video_queue WHERE ai_status = 'pending' ORDER BY discovered_at ASC;")
            pending_videos = q_cursor.fetchall()
        except sqlite3.OperationalError:
            print("[-] Queue table not ready. Waiting for scraper.")
            q_conn.close()
            time.sleep(120)
            continue
            
        if not pending_videos:
            print("[~] No pending videos found. Sleeping for 2 minutes.")
            q_conn.close()
            time.sleep(120)
            continue

        t_conn = sqlite3.connect(DB_TICKER_PATH)
        t_cursor = t_conn.cursor()
        
        rate_limit_hit = False

        for vid_id, handle, title, timestamp in pending_videos:
            print(f"[>] Processing: {handle} | {title}")
            
            ai_ok, raw_ticker = call_gemini_stage1(client, title)
            if not ai_ok:
                print("[!] Gemini Rate Limit hit during Stage 1.")
                rate_limit_hit = True
                break
                
            german_ticker = translate_to_german(raw_ticker)
            
            t_cursor.execute("SELECT id, ticker_text FROM tickers ORDER BY id DESC LIMIT 20;")
            history = t_cursor.fetchall()
            
            ai_ok, status, replace_id, final_text = call_gemini_stage2(client, german_ticker, history)
            if not ai_ok:
                print("[!] Gemini Rate Limit hit during Stage 2.")
                rate_limit_hit = True
                break
                
            if status == "UNIQUE":
                print(f"[+] UNIQUE: {final_text}")
                t_cursor.execute(
                    "INSERT INTO tickers (video_id, channel_handle, ticker_text, created_at) VALUES (?, ?, ?, ?);",
                    (vid_id, handle, final_text, timestamp)
                )
            elif status == "FUSED":
                print(f"[+] FUSED into ID {replace_id}: {final_text}")
                t_cursor.execute(
                    "UPDATE tickers SET ticker_text = ?, bot_status = 'pending', created_at = ? WHERE id = ?;",
                    (final_text, timestamp, replace_id)
                )
            elif status == "DUPLICATE":
                print(f"[-] DUPLICATE discarded.")

            q_cursor.execute("UPDATE video_queue SET ai_status = 'completed' WHERE video_id = ?;", (vid_id,))
            
            q_conn.commit()
            t_conn.commit()

        q_conn.close()
        t_conn.close()

        if rate_limit_hit:
            print("[~] Cooldown activated. Sleeping for 20 minutes.")
            time.sleep(1200)
        else:
            print("[~] Queue cleared successfully. Sleeping for 2 minutes.")
            time.sleep(120)

if __name__ == "__main__":
    run_processor()

