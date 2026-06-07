import sqlite3
import time
from google import genai
from google.genai import types
import json

DB_QUEUE = "data/video_ids.db"
DB_TICKER = "data/news_ticker.db"

def init_dbs():
    conn = sqlite3.connect(DB_TICKER)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickers (
            video_id TEXT PRIMARY KEY,
            problem TEXT,
            solution TEXT,
            consequences TEXT,
            lang TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def get_next_video():
    conn = sqlite3.connect(DB_QUEUE)
    cursor = conn.cursor()
    # Wir prüfen hier, ob überhaupt ein Video mit 'pending' existiert
    cursor.execute("SELECT video_id FROM video_queue WHERE ai_status = 'pending' LIMIT 1;")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def process_video():
    print("Skript gestartet. Suche nach pending Videos...")
    init_dbs()
    client = genai.Client()

    video_id = get_next_video()
    
    if not video_id:
        print("Kein Video mit Status 'pending' in der Datenbank gefunden.")
        return

    print(f"Verarbeite Video ID: {video_id}")
    
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Test-Prompt um zu sehen, ob die API antwortet
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                types.Part.from_uri(file_uri=url, mime_type='video/mp4'),
                "Erstelle einen Ticker für Gehörlose mit: Problem, Lösung, Konsequenzen."
            ]
        )
        
        print("Antwort erhalten. Speichere in Datenbank...")
        # (Hier fügen wir dann wieder die saubere Speicherung ein)
        print(f"Rohdaten: {response.text}")
        
    except Exception as e:
        print(f"Fehler bei der Verarbeitung: {e}")

if __name__ == "__main__":
    process_video()

