#!/bin/bash

# Configuration
DB_PATH="data/video_ids.db"
TEMP_DIR="data/temp_transcripts"

# Ensure the temporary folder exists
mkdir -p "$TEMP_DIR"

# 1. Fetch the oldest pending video
# Adjust the table/column names if they differ from 'video_queue' and 'video_id'
video_id=$(sqlite3 "$DB_PATH" "SELECT video_id FROM video_queue WHERE ai_status = 'pending' ORDER BY discovered_at ASC LIMIT 1;")

# If no pending videos are found, exit
if [ -z "$video_id" ]; then
    echo "No pending videos found."
    exit 0
fi

echo "Processing video: $video_id"

# 2. Extract transcript using yt-dlp
# --write-auto-sub: fetches auto-generated captions
# --skip-download: does not download the video file
# --sub-lang: sets language preference
# --output: sets the location and filename prefix
yt-dlp --skip-download --write-auto-sub --sub-lang en,de --output "$TEMP_DIR/$video_id" "https://www.youtube.com/watch?v=$video_id"

# 3. Update the database status
# Marking as 'extracted' so it is not picked up by this script again
sqlite3 "$DB_PATH" "UPDATE video_queue SET ai_status = 'extracted' WHERE video_id = '$video_id';"

echo "Transcript saved for $video_id and database updated."

