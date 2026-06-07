"""
This script reads the saved JSON transcripts and sends them to the Gemini API.
It enforces a structured JSON response schema containing problem, solution, and consequences.
If the input language is not German, Gemini translates the content automatically.
The finalized analysis is written into the local SQLite news ticker database.
"""

import os
import json
import sqlite3
import glob
import google.generativeai as genai
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# Configure the Gemini API using the environment variable
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

TEMP_DIR = "data/temp_transcripts"
DB_TICKER = "data/news_ticker.db"

# Enforce structured output via Pydantic matching the exact requirements
class NewsTickerAnalysis(BaseModel):
    problem: str = Field(description="Detaillierte Erklärung des Problems im Video.")
    solution: str = Field(description="Detaillierte Erklärung der vorgeschlagenen Lösung.")
    consequences: str = Field(description="Mögliche Konsequenzen des Problems oder der Lösung.")

def init_ticker_db():
    # Setup the ticker table if it does not exist yet
    conn = sqlite3.connect(DB_TICKER)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tickers (
            video_id TEXT PRIMARY KEY,
            problem TEXT,
            solution TEXT,
            consequences TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    conn.commit()
    conn.close()

def is_already_processed(video_id):
    # Verify if this video asset was already parsed into the database
    conn = sqlite3.connect(DB_TICKER)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM tickers WHERE video_id = ?;', (video_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def save_analysis(video_id, analysis_data):
    # Store the structured segments into the ticker database
    conn = sqlite3.connect(DB_TICKER)
    conn.execute('''
        INSERT OR REPLACE INTO tickers (video_id, problem, solution, consequences)
        VALUES (?, ?, ?, ?);
    ''', (video_id, analysis_data.problem, analysis_data.solution, analysis_data.consequences))
    conn.commit()
    conn.close()

def process_transcripts_with_gemini():
    init_ticker_db()
    
    # Locate all processed JSON files from step 1
    json_files = glob.glob(os.path.join(TEMP_DIR, "*.json"))
    
    if not json_files:
        print("No transcript JSON files found to process.")
        return

    # Utilize the Flash model as seen in your configuration environments
    model = genai.GenerativeModel("gemini-2.5-flash")

    for file_path in json_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            video_id = data.get("video_id")
            language = data.get("language", "en")
            transcript_text = data.get("transcript", "")

            if not video_id or not transcript_text:
                continue

            if is_already_processed(video_id):
                print(f"Video {video_id} is already processed. Skipping.")
                continue

            print(f"Analyzing video {video_id} with Gemini (Language context: {language})...")

            # Instruct the model to structure the details and translate if necessary
            prompt = f"""
            Du bist ein Assistent, der verständliche Zusammenfassungen für gehörlose Menschen erstellt.
            Analysiere das folgende Video Transkript.
            
            WICHTIG: Wenn das Transkript beziehungsweise die Sprache nicht Deutsch ('de') ist, musst du alle deine Erklärungen direkt während der Analyse ins Deutsche übersetzen. Das Endergebnis MUSS komplett auf Deutsch sein.

            Extrahiere die Informationen exakt nach diesen Regeln:
            - problem: Detaillierte Erklärung des Problems im Video.
            - solution: Detaillierte Erklärung der vorgeschlagenen Lösung.
            - consequences: Mögliche Konsequenzen des Problems oder der Lösung.

            Hier ist das Transkript:
            {transcript_text}
            """

            # Request structure directly via the Gemini SDK config options
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=NewsTickerAnalysis
                )
            )

            # Parse response directly into the schema object
            result_json = json.loads(response.text)
            structured_result = NewsTickerAnalysis(**result_json)

            save_analysis(video_id, structured_result)
            print(f"Successfully saved analysis for video {video_id} into ticker database.")

        except Exception as e:
            print(f"An error occurred while processing file {file_path}: {e}")

if __name__ == "__main__":
    process_transcripts_with_gemini()

