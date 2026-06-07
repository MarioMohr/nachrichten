# nachrichten

## Database Structure: video_ids.db

The local SQLite database data/video_ids.db is the central tracking hub for the entire pipeline. It manages channel synchronizations, legacy processed states, and the active queue for the AI gatekeeper and processing stages.

It consists of three main tables.

### 1. video_queue
This is the core table where the scraper drops new videos and where the AI pipeline tracks progress.

    Schema:
    * video_id (TEXT, Primary Key): The 11 character YouTube video ID.
    * channel_id (TEXT): The unique YouTube channel ID.
    * channel_handle (TEXT): The human readable channel handle.
    * title (TEXT): The title of the video.
    * ai_status (TEXT): The current state of the video in the pipeline. Default is new.
    * discovered_at (TIMESTAMP): When the scraper first found the video.

    Example Data:
    video_id    | channel_id | channel_handle | title                          | ai_status | discovered_at
    ----------- | ---------- | -------------- | ------------------------------ | --------- | -------------------
    dQw4w9WgXcQ | UC_x5XG... | @rickastley    | Never Gonna Give You Up        | new       | 2026-06-07 14:00:00
    MZb2vG9r3_g | UCQv...    | @some_news     | Market crashes by 10 percent   | pending   | 2026-06-07 14:05:00
    z1234567890 | UCxyz...   | @gaming_chan   | Let Us Play Elden Ring Part 12 | ignored   | 2026-06-07 14:10:00

### 2. channel_map
This table resolves human readable handles from your channels.txt to internal YouTube channel IDs.

    Schema:
    * channel_id (TEXT, Primary Key): The internal YouTube channel ID.
    * handle (TEXT, Not Null): The @ handle of the channel.

    Example Data:
    channel_id               | handle
    ------------------------ | --------------
    UC_x5XG1OV2P6uZZ5FSM9Ttw | @rickastley
    UCQvMZb2vG9r3_g          | @some_news

### 3. processed_videos
This is a legacy tracking table where each column represents a channel ID to track which videos have already been processed for that specific channel.

    Schema:
    * id (INTEGER, Primary Key, Autoincrement)
    * {channel_id} (TEXT): Dynamic columns added or removed automatically by the scraper.


