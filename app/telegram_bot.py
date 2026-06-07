"""
This script reads the newly processed news tickers from the database and publishes them to a Telegram channel.
It runs in a continuous loop and checks for new entries.
Once successfully published, the status in the database is updated to prevent duplicate messages.
"""

import os
import time
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

DB_TICKER = "data/news_ticker.db"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID")

def publish_new_tickers():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHANNEL:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID is missing in the environment variables.")
        return

    interval_seconds = 300

    while True:
        print("\n[~] Telegram publisher cycle initiated.")
        
        if not os.path.exists(DB_TICKER):
            print(f"Waiting for database at {DB_TICKER} to be created.")
            time.sleep(interval_seconds)
            continue

        conn = sqlite3.connect(DB_TICKER)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT video_id, problem, solution, consequences FROM tickers WHERE status = 'new' ORDER BY created_at ASC;")
            pending_tickers = cursor.fetchall()
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            conn.close()
            time.sleep(interval_seconds)
            continue

        if not pending_tickers:
            print("No new tickers found for publishing.")
        else:
            for video_id, problem, solution, consequences in pending_tickers:
                message = (
                    f"⚠️ <b>Wichtiges Update</b>\n\n"
                    f"<b>Problem:</b>\n{problem}\n\n"
                    f"<b>Lösung:</b>\n{solution}\n\n"
                    f"<b>Konsequenzen:</b>\n{consequences}\n\n"
                    f"🔗 https://youtube.com/watch?v={video_id}"
                )
                
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                payload = {
                    "chat_id": TELEGRAM_CHANNEL,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False
                }
                
                try:
                    response = requests.post(url, json=payload, timeout=15)
                    if response.status_code == 200:
                        print(f"Successfully published video {video_id} to Telegram.")
                        cursor.execute("UPDATE tickers SET status = 'published' WHERE video_id = ?;", (video_id,))
                        conn.commit()
                    else:
                        print(f"Failed to publish {video_id}. API returned status {response.status_code}: {response.text}")
                except Exception as req_err:
                    print(f"Network error while publishing {video_id}: {req_err}")
                
                time.sleep(3)

        conn.close()
        print(f"Publisher cycle complete. Sleeping for {interval_seconds} seconds.")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    publish_new_tickers()

