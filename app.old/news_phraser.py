import os
import re
import time
import sqlite3
import urllib.request
from google import genai
from google.genai import types
from google.genai.errors import APIError

DB_VIDEO_PATH = "data/Video_IDs.db"
DB_TICKER_PATH = "data/News_Ticker.db"

# Initialize the Gemini Client using the environment key injected by Docker
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

def init_databases():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_TICKER_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker_text TEXT UNIQUE NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def fetch_rss_feed(channel_id):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.read().decode('utf-8')
    except Exception:
        return None

def parse_videos_from_xml(xml_content):
    video_ids = re.findall(r'<yt:videoId>([^<]+)</yt:videoId>', xml_content)
    titles = re.findall(r'<title>([^<]+)</title>', xml_content)
    actual_titles = titles[1:] if len(titles) > 1 else []
    return list(zip(video_ids, actual_titles))

def is_video_processed(cursor, channel_id, video_id):
    try:
        query = f'SELECT 1 FROM processed_videos WHERE "{channel_id}" = ? LIMIT 1;'
        cursor.execute(query, (video_id,))
        return cursor.fetchone() is not None
    except sqlite3.OperationalError:
        return False

def call_gemini_stage1(title):
    """
    Stage 1: Sends the raw YouTube video title to Gemini Flash.
    Returns a short, punchy, compact news ticker line.
    """
    if not client:
        print("[!] Error: GEMINI_API_KEY is not set in environment.")
        return False, f"NEWS: {title} (Fallback)"

    prompt = (
        f"Transform this raw video title into a short, punchy, ultra-compact news ticker headline. "
        f"Do not use quotes, markdown formatting, prefixes like NEWS:, or introductory text. "
        f"Keep it as a single crisp statement.\n\nTitle: {title}"
    )

    try:
        # Utilizing gemini-2.5-flash as the optimal speed-to-quota efficiency model
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        if response.text:
            return True, response.text.strip()
        return False, None
    except APIError as e:
        # Check explicitly for resource exhaustion (HTTP Status Code 429)
        if e.code == 429:
            return False, None
        print(f"[!] Gemini API Non-429 Exception encountered: {e}")
        return True, f"NEWS: {title} (Parsing Issue)"
    except Exception as e:
        print(f"[!] System connection issue: {e}")
        return False, None

def call_gemini_stage2(new_ticker, existing_tickers):
    """
    Stage 2: Compares the brand new news ticker statement against a historical 
    list of recently logged headlines. It fuses them if additional value is 
    found, or identifies it as a duplicate.
    """
    if not client:
        return True, new_ticker, None

    # Construct the comparative analytical instruction list
    formatted_history = "\n".join([f"- {text}" for text in existing_tickers])
    
    prompt = (
        f"You are a validation engine protecting a news ticker feed from double postings and redundant info.\n"
        f"Current Recent Headlines:\n{formatted_history}\n\n"
        f"New Candidate Headline: {new_ticker}\n\n"
        f"Analyze the relationship. You have exactly three pathways to choose from:\n"
        f"1. If the candidate is a complete duplicate or adds no real value, reply with precisely: DUPLICATE\n"
        f"2. If the candidate covers a topic listed above but contains significant additions, numbers, or facts, "
        f"rewrite a comprehensive headline fusing both sources cleanly. Reply with precisely: FUSED || Your_New_Fused_Text\n"
        f"3. If the candidate is entirely new content, reply with precisely: UNIQUE\n"
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        output = response.text.strip() if response.text else "UNIQUE"

        if output == "DUPLICATE":
            return True, None, None
        elif output.startswith("FUSED"):
            # Split out the newly combined phrasing string
            parts = output.split("||")
            fused_text = parts[1].strip() if len(parts) > 1 else new_ticker
            # For now, we append it cleanly. Later we can pass IDs to overwrite specific entries.
            return True, fused_text, None
        else:
            return True, new_ticker, None
            
    except APIError as e:
        if e.code == 429:
            return False, None, None
        return True, new_ticker, None
    except Exception:
        return True, new_ticker, None

def process_single_video(video_id, title, channel_id, handle, v_cursor, t_cursor):
    # Stage 1: Send raw content to Gemini to parse down to headline ticker format
    ai_ok, ticker_line = call_gemini_stage1(title)
    if not ai_ok:
        return "RATE_LIMIT"

    # Stage 2: Cross-reference across historical ticker text data
    t_cursor.execute("SELECT id, ticker_text FROM ticker_entries ORDER BY id DESC LIMIT 20;")
    existing_entries = t_cursor.fetchall()
    
    if existing_entries:
        existing_texts = [row[1] for row in existing_entries]
        ai_ok, final_ticker, replace_id = call_gemini_stage2(ticker_line, existing_texts)
        if not ai_ok:
            return "RATE_LIMIT"
    else:
        final_ticker = ticker_line
        replace_id = None

    if final_ticker:
        print(f"[AI Output] Live Headline: {final_ticker}")
        try:
            t_cursor.execute("INSERT INTO ticker_entries (ticker_text) VALUES (?);", (final_ticker,))
        except sqlite3.IntegrityError:
            pass 

    # Mark the source link as tracked regardless of duplicate outcome
    insert_query = f'INSERT INTO processed_videos ("{channel_id}") VALUES (?);'
    v_cursor.execute(insert_query, (video_id,))
    return "SUCCESS"

def main_loop():
    init_databases()
    current_delay = 600 

    while True:
        print(f"\n[~] Script started: Checking tracked channels for new content... (Interval: {current_delay // 60}m)")
        
        v_conn = sqlite3.connect(DB_VIDEO_PATH)
        t_conn = sqlite3.connect(DB_TICKER_PATH)
        v_cursor = v_conn.cursor()
        t_cursor = t_conn.cursor()

        try:
            v_cursor.execute("SELECT channel_id, handle FROM channel_map;")
            channels = v_cursor.fetchall()
        except sqlite3.OperationalError:
            print("[-] Error: channel_map table missing. Run create_database.py first.")
            v_conn.close()
            t_conn.close()
            time.sleep(600)
            continue

        rate_limit_hit = False
        new_videos_processed = 0

        for channel_id, handle in channels:
            if rate_limit_hit:
                break

            xml_content = fetch_rss_feed(channel_id)
            if not xml_content:
                continue

            feed_entries = parse_videos_from_xml(xml_content)
            
            for vid_id, title in feed_entries:
                if not is_video_processed(v_cursor, channel_id, vid_id):
                    print(f"[New Content] Found candidate video on {handle}: {title}")
                    
                    status = process_single_video(vid_id, title, channel_id, handle, v_cursor, t_cursor)
                    
                    if status == "RATE_LIMIT":
                        print("[!] AI Rate limit detected. Aborting cycle to preserve state.")
                        rate_limit_hit = True
                        break
                    
                    new_videos_processed += 1
                    v_conn.commit()
                    t_conn.commit()

        v_conn.close()
        t_conn.close()

        if rate_limit_hit:
            current_delay = 1200 
            print(f"[~] Cool-down triggered. Sleeping for 20 minutes before retry.")
        else:
            current_delay = 600 
            if new_videos_processed > 0:
                print(f"[+] Check finished: Processed {new_videos_processed} new entries.")
            else:
                print("[~] Check finished: No new content found across any channels.")

        time.sleep(current_delay)

if __name__ == "__main__":
    main_loop()

