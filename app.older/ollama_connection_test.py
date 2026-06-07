import json
import urllib.request

def test_shared_ai_node():
    # Use the container name directly as the network address
    url = "http://ollama_backend:11434/api/generate"
    
    payload = {
        "model": "phi4-mini",
        "prompt": "Respond with only one word: Connected.",
        "stream": False
    }
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            print(f"[+] Status: Shared AI Engine responded cleanly -> {result.get('response').strip()}")
    except Exception as e:
        print(f"[-] Connection failed across the shared network: {e}")

if __name__ == "__main__":
    test_shared_ai_node()

