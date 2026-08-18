"""Minimal post-deployment smoke test. Does not create or change data."""

import json
import os
import urllib.request


def read(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return json.load(response)


def main() -> None:
    base = os.environ.get("FAZTRACK_SMOKE_API_URL", "http://localhost:8000").rstrip("/")
    live = read(f"{base}/health/live")
    ready = read(f"{base}/health/ready")
    if live.get("status") != "ok" or ready.get("status") != "ready":
        raise RuntimeError({"live": live, "ready": ready})
    print("PASS: API hidup dan database siap")


if __name__ == "__main__":
    main()
