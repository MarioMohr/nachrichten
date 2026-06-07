import requests
import json
from typing import List

def process_news_feeds(headlines: List[str]) -> str:
    """
    Cross-references a list of incoming headlines to remove redundancies
    and outputs a structured, deduplicated news ticker summary.
    """
    url = "http://ollama_backend:11434/api/generate"

    # Format the input headlines into a clear, numbered list for the LLM
    formatted_input = "\n".join([f"{i+1}. {headline}" for i, headline in enumerate(headlines)])

    system_prompt = (
        "You are an expert news editor and data deduplication module. "
        "Your task is to analyze the provided list of headlines, identify matching or highly overlapping global events, "
        "and merge redundant information into single data points. "
        "Output a clean, consolidated, bulleted news ticker list using exclusively '*' as the bullet point marker. "
        "Do not include any introductory text, pleasantries, explanations, or concluding remarks. "
        "Provide only the final, processed bulleted list."
    )

    payload = {
        "model": "phi4-mini",
        "prompt": f"Raw News Feeds:\n{formatted_input}",
        "system": system_prompt,
        "stream": False,
        "options": {
            "temperature": 0.0
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=90)
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except requests.exceptions.RequestException as e:
        return f"News Ticker Service Error: {e}"

if __name__ == "__main__":
    # Test dataset mimicking multiple scrapers hitting the same breaking news
    sample_feeds = [
        "Breaking: Space-X launches Falcon 9 rocket from Cape Canaveral early this morning.",
        "Tech Update: SpaceX successfully sends new satellite cluster into orbit via Falcon 9.",
        "Market Alert: Oil prices experience unexpected 3% drop due to supply increases.",
        "Energy News: Global crude prices fall sharply amid rising production reports.",
        "SpaceX Falcon 9 liftoff confirmed in Florida for satellite deployment."
    ]

    print("--- Incoming Scraper Feeds ---")
    for feed in sample_feeds:
        print(f"[Scraped] {feed}")

    print("\n--- Processed Cross-Referenced News Ticker ---")
    ticker_output = process_news_feeds(sample_feeds)
    print(ticker_output)

