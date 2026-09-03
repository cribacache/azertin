import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def main():
    base = os.getenv("BUK_API_BASE", "").rstrip("/")
    url = f"{base}/employees/active" if base else ""
    api_key = os.getenv("BUK_API_KEY", "")

    if not url or not api_key:
        raise SystemExit("Faltan BUK_API_BASE o BUK_API_KEY en .env")

    variants = {
        "auth_token": {"auth_token": api_key, "Content-Type": "application/json"},
        "authorization_bearer": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        "x_api_key": {"X-API-Key": api_key, "Content-Type": "application/json"},
    }

    print(f"Endpoint: {url}")
    for name, headers in variants.items():
        try:
            response = requests.get(url, headers=headers, timeout=10)
            print(f"{name}: HTTP {response.status_code} | {response.text[:180].replace(chr(10), ' ')}")
        except requests.RequestException as error:
            print(f"{name}: ERROR | {error}")


if __name__ == "__main__":
    main()
