#!/bin/bash

DB_PATH="data/Video_IDs.db"

if [ ! -f "$DB_PATH" ]; then
    echo "[-] Error: Database $DB_PATH does not exist. Run create_database.py first."
    exit 1
fi

echo "[~] Script started: Checking tracked channels for new content..."

# Fetch all active channel rows from our mapping table
channels_data=$(sqlite3 "$DB_PATH" "SELECT channel_id, handle FROM channel_map;")

if [ -z "$channels_data" ]; then
    echo "[~] No active channels found in the database."
    exit 0
fi

# Keep a global counter for new videos discovered during this run
total_new_videos=0
ai_rate_limited=0

# Loop through each channel line by line
echo "$channels_data" | while IFS='|' read -r channel_id handle; do
    # If a previous iteration hit a rate limit, stop processing further channels entirely
    if [ "$ai_rate_limited" -eq 1 ]; then
        break
    fi

    if [ -z "$channel_id" ]; then
        continue
    fi

    rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=$channel_id"
    xml_content=$(curl -sL -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" "$rss_url")

    if [ -z "$xml_content" ]; then
        echo "[!] Failed to fetch feed for $handle"
        continue
    fi

    # Extract all unique Video IDs and raw Titles into separate arrays
    mapfile -t video_ids < <(echo "$xml_content" | grep -oP '<yt:videoId>\K[^<]+')
    mapfile -t all_titles < <(echo "$xml_content" | grep -oP '<title>\K[^<]+')

    # Remove the first title element since it always represents the main Channel Name
    video_titles=("${all_titles[@]:1}")

    # Track if we need to print a header block for updates on this channel
    header_printed=0

    # Iterate through the extracted feed arrays
    for i in "${!video_ids[@]}"; do
        vid_id="${video_ids[i]}"
        title="${video_titles[i]}"

        if [ -z "$vid_id" ]; then
            continue
        fi

        # Check if this unique video ID already exists inside this channel's column
        check_exists=$(sqlite3 "$DB_PATH" "SELECT 1 FROM processed_videos WHERE \"$channel_id\" = '$vid_id' LIMIT 1;")

        if [ -z "$check_exists" ]; then
            if [ "$header_printed" -eq 0 ]; then
                echo "--- New Updates for $handle ---"
                header_printed=1
            fi

            # --- START OVER STOP OVER LOGIC ---
            echo "[AI Simulation] Sending to AI -> $handle: $title"
            
            # Simulate an AI execution status. 
            # In the future, this will be your actual AI command line tool or python call.
            # Change this to 1 manually if you want to test how the script reacts to an AI failure.
            ai_exit_status=0 
            # --- END STOP OVER LOGIC ---

            if [ "$ai_exit_status" -ne 0 ]; then
                echo "[!] AI Error or Rate Limit reached. Stopping data processing immediately."
                ai_rate_limited=1
                break
            fi

            # Log the processed ID under its dedicated channel column ONLY if the AI succeeded
            sqlite3 "$DB_PATH" "INSERT INTO processed_videos (\"$channel_id\") VALUES ('$vid_id');"
            
            # Increment our runtime counters
            total_new_videos=$((total_new_videos + 1))
        fi
    done
done

# Print final run summary statistics for log collection
if [ "$total_new_videos" -gt 0 ]; then
    echo "[+] Check finished: Found and processed $total_new_videos new videos."
else
    echo "[~] Check finished: No new content processed across any channels."
fi

