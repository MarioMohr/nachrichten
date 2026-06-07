from youtube_transcript_api import YouTubeTranscriptApi

def get_transcript_fixed(video_id):
    # This creates the object required by the latest version
    ytt_api = YouTubeTranscriptApi()
    try:
        # Use fetch() to get the transcript
        transcript = ytt_api.fetch(video_id)
        return transcript
    except Exception as e:
        print(f"Error: {e}")
        return None

if __name__ == "__main__":
    # This line calls the function and prints the result
    result = get_transcript_fixed("dQw4w9WgXcQ")
    print(result)

