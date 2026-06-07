"""
This script scrapes YouTube video RSS feeds for specific channels and synchronizes them with a local database.
It resolves channel handles to underlying channel IDs and maintains a SQLite queue.
The script parses the actual YouTube publication timestamp directly from the XML feed.
It applies a word blacklist and cross references duplicate titles to prevent redundant processing.
Relevant and newly discovered videos are inserted with the status "new" so the downstream AI pipeline can evaluate them.
Irrelevant or duplicate videos are instantly marked as "ignored".
The scraper runs in a continuous hourly loop.
"""

import os
import re
import time
import sqlite3
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

DB_PATH = "data/video_ids.db"
CHANNELS_FILE = "data/channels.txt"
BLACKLIST_PATH = "data/blacklist.txt"

def initialize_environment():
    if not os.path.exists(os.path.dirname(BLACKLIST_PATH)):
        os.makedirs(os.path.dirname(BLACKLIST_PATH), exist_ok=True)
    if not os.path.exists(BLACKLIST_PATH):
        with open(BLACKLIST_PATH, "w", encoding="utf-8") as file:
            file.write("# Add blacklisted words or phrases below\n")

def load_blacklist():
    blacklist = set()
    if os.path.exists(BLACKLIST_PATH):
        with open(BLACKLIST_PATH, "r", encoding="utf-8") as file:
            for line in file:
                cleaned_line = line.strip().lower()
                if cleaned_line and not cleaned_line.startswith("#"):
                    blacklist.add(cleaned_line)
    return blacklist

def is_blacklisted(title, blacklist):
    title_lower = title.lower()
    for word in blacklist:
        if word in title_lower:
            return True
    return False

def init_tables_and_get_columns():
    if not os.path.exists("data"):
        os.makedirs("data", exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("CREATE TABLE IF NOT EXISTS processed_videos (id INTEGER PRIMARY KEY AUTOINCREMENT);")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_map (
            channel_id TEXT PRIMARY KEY,
            handle TEXT NOT NULL
        );
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_queue (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT,
            channel_handle TEXT,
            title TEXT,
            ai_status TEXT DEFAULT "new",
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    
    conn.commit()
    
    cursor.execute("PRAGMA table_info(processed_videos);")
    existing_cols = [col[1] for col in cursor.fetchall() if col[1] != "id"]
    
    conn.close()
    return existing_cols

def resolve_single_id(handle):
    clean_handle = handle.strip().lstrip("@")
    if not clean_handle or handle.startswith("#"):
        return None, None, None
        
    url = f"https://www.youtube.com/@{clean_handle}"
    try:
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            html = response.read().decode("utf-8")
            
        match = re.search(r"youtube\.com/channel/([A-Za-z0-9_-]+)", html)
        if match:
            return handle, match.group(1), None
        else:
            return handle, None, "rubbish text"
            
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return handle, None, "channel missing"
        return handle, None, f"HTTP Error {e.code}"
    except Exception:
        return handle, None, "connection error"

def sync_channels_from_file(existing_ids):
    if not os.path.exists(CHANNELS_FILE):
        print(f"Error: {CHANNELS_FILE} not found.")
        return

    with open(CHANNELS_FILE, "r") as f:
        handles = [line.strip() for line in f if line.strip()]

    id_to_handle_map = {}
    active_ids = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(resolve_single_id, handles)
        
    for handle, cid, error_msg in results:
        if error_msg:
            print(f"Error processing {handle}: {error_msg}")
        elif cid:
            id_to_handle_map[cid] = handle
            active_ids.append(cid)
            
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    channels_to_add = [cid for cid in active_ids if cid not in existing_ids]
    channels_to_remove = [cid for cid in existing_ids if cid not in active_ids]
    
    for cid in channels_to_add:
        handle_name = id_to_handle_map.get(cid, "Unknown")
        print(f"Adding new channel: {handle_name}")
        cursor.execute("INSERT OR REPLACE INTO channel_map (channel_id, handle) VALUES (?, ?);", (cid, handle_name))
        cursor.execute(f'ALTER TABLE processed_videos ADD COLUMN "{cid}" TEXT;')
        
    for cid in channels_to_remove:
        cursor.execute("SELECT handle FROM channel_map WHERE channel_id = ?;", (cid,))
        row = cursor.fetchone()
        handle_name = row[0] if row else f"Unknown ({cid})"
        
        print(f"Removing missing channel: {handle_name}")
        cursor.execute(f'ALTER TABLE processed_videos DROP COLUMN "{cid}";')
        cursor.execute("DELETE FROM channel_map WHERE channel_id = ?;", (cid,))
        
    if not channels_to_add and not channels_to_remove:
        print("Database schema is already up to date.")
        
    conn.commit()
    conn.close()

def fetch_feed(channel_id):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

def parse_feed(xml_content):
    entries = re.findall(r"<entry>(.*?)</entry>", xml_content, re.DOTALL)
    parsed_entries = []
    
    for entry in entries:
        v_match = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", entry)
        t_match = re.search(r"<title>([^<]+)</title>", entry)
        p_match = re.search(r"<published>([^<]+)</published>", entry)
        
        if v_match and t_match and p_match:
            clean_timestamp = p_match.group(1).replace("T", " ").split("+")[0].split("Z")[0].strip()
            parsed_entries.append((v_match.group(1), t_match.group(1), clean_timestamp))
            
    return parsed_entries

def run_scraper():
    interval_seconds = 3600

    while True:
        print("\nScraper cycle initiated. Synchronizing tracking maps...")
        
        initialize_environment()
        blacklist = load_blacklist()
        
        existing_ids = init_tables_and_get_columns()
        sync_channels_from_file(existing_ids)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT channel_id, handle FROM channel_map;")
            channels = cursor.fetchall()
        except sqlite3.OperationalError:
            print("Error The channel mapping table is missing.")
            conn.close()
            time.sleep(interval_seconds)
            continue

        new_count = 0

        for channel_id, handle in channels:
            xml = fetch_feed(channel_id)
            if not xml:
                continue

            entries = parse_feed(xml)
            
            for vid_id, title, published_time in entries:
                cursor.execute("SELECT 1 FROM video_queue WHERE video_id = ?;", (vid_id,))
                if not cursor.fetchone():
                    
                    cursor.execute("SELECT 1 FROM video_queue WHERE title = ?;", (title,))
                    is_duplicate = cursor.fetchone() is not None
                    
                    if is_blacklisted(title, blacklist):
                        print(f"Ignored due to blacklist: {title}")
                        ai_status = "ignored"
                    elif is_duplicate:
                        print(f"Ignored due to duplicate title: {title}")
                        ai_status = "ignored"
                    else:
                        print(f"Logged new pending video for {handle}: {title}")
                        ai_status = "new"
                        
                    cursor.execute(
                        "INSERT INTO video_queue (video_id, channel_id, channel_handle, title, ai_status, discovered_at) VALUES (?, ?, ?, ?, ?, ?);",
                        (vid_id, channel_id, handle, title, ai_status, published_time)
                    )
                    
                    if ai_status == "new":
                        new_count += 1
                        
                    conn.commit()

        conn.close()
        print(f"Scraper cycle complete. Added {new_count} items to the tracking queue.")
        
        time.sleep(interval_seconds)

if __name__ == "__main__":
    run_scraper()

