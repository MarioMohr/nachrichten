import os
import re
import sqlite3
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

DB_PATH = "data/Video_IDs.db"
CHANNELS_FILE = "data/channels.txt"

def resolve_single_id(handle):
    clean_handle = handle.strip().lstrip('@')
    if not clean_handle or handle.startswith('#'):
        return None, None, None
        
    url = f"https://www.youtube.com/@{clean_handle}"
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            html = response.read().decode('utf-8')
            
        match = re.search(r'youtube.com/channel/([A-Za-z0-9_-]+)', html)
        if match:
            return handle, match.group(1), None
        else:
            return handle, None, "rubbish text"
            
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return handle, None, "channel doesn't exist"
        return handle, None, f"HTTP Error {e.code}"
    except Exception as e:
        return handle, None, "connection error"

def get_active_channels_parallel():
    if not os.path.exists(CHANNELS_FILE):
        print(f"[-] Error: {CHANNELS_FILE} not found.")
        return {}, []

    with open(CHANNELS_FILE, "r") as f:
        handles = [line.strip() for line in f if line.strip()]

    id_to_handle_map = {}
    active_ids = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(resolve_single_id, handles)
        
    for handle, cid, error_msg in results:
        if error_msg:
            print(f"[!] Error processing {handle}: {error_msg}")
        elif cid:
            id_to_handle_map[cid] = handle
            active_ids.append(cid)
            
    return id_to_handle_map, active_ids

def get_existing_columns(cursor):
    cursor.execute("PRAGMA table_info(processed_videos);")
    return [col[1] for col in cursor.fetchall() if col[1] != 'id']

def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Core table for video processing logs
    cursor.execute("CREATE TABLE IF NOT EXISTS processed_videos (id INTEGER PRIMARY KEY AUTOINCREMENT);")
    
    # Internal map table to link IDs back to handles for terminal logs during removals
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_map (
            channel_id TEXT PRIMARY KEY,
            handle TEXT NOT NULL
        );
    """)
    conn.commit()
    
    id_to_handle_map, active_ids = get_active_channels_parallel()
    existing_ids = get_existing_columns(cursor)
    
    channels_to_add = [cid for cid in active_ids if cid not in existing_ids]
    channels_to_remove = [cid for cid in existing_ids if cid not in active_ids]
    
    # Add new columns
    for cid in channels_to_add:
        handle_name = id_to_handle_map.get(cid, "Unknown")
        print(f"[+] Adding new channel: {handle_name}")
        
        # Save the handle mapping to the database first
        cursor.execute("INSERT OR REPLACE INTO channel_map (channel_id, handle) VALUES (?, ?);", (cid, handle_name))
        cursor.execute(f'ALTER TABLE processed_videos ADD COLUMN "{cid}" TEXT;')
        
    # Remove missing columns
    for cid in channels_to_remove:
        # Retrieve the handle name directly from the database storage mapping
        cursor.execute("SELECT handle FROM channel_map WHERE channel_id = ?;", (cid,))
        row = cursor.fetchone()
        handle_name = row[0] if row else f"Unknown ({cid})"
        
        print(f"[-] Removing missing channel: {handle_name}")
        
        cursor.execute(f'ALTER TABLE processed_videos DROP COLUMN "{cid}";')
        cursor.execute("DELETE FROM channel_map WHERE channel_id = ?;", (cid,))
        
    if not channels_to_add and not channels_to_remove:
        print("[~] Database schema is already up to date. Nothing added or removed.")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()

