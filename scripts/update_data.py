"""
Download all prize bond result files from savings.gov.pk and save them to data/.

This script is designed to run as a GitHub Action on a schedule.
It only downloads files that aren't already present, so it's
safe to run repeatedly.
"""

import re
import sys
from pathlib import Path

import requests

DENOMINATION_PAGES = {
    "rs-100": "https://savings.gov.pk/rs-100-prize-bond-draw",
    "rs-200": "https://savings.gov.pk/rs-200-prize-bond-draw",
    "rs-750": "https://savings.gov.pk/rs-750-prize-bond-draw",
    "rs-1500": "https://savings.gov.pk/rs-1500-prize-bond-draw",
    "rs-25000-premium": "https://savings.gov.pk/premium-prize-bond-rs-25000",
    "rs-40000-premium": "https://savings.gov.pk/premium-prize-bond-rs-40000",
}

LINK_PATTERN = re.compile(
    r'<a[^>]+href="([^"]+\.(?:txt|pdf|doc|docx))"[^>]*>([^<]*)</a>',
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main():
    downloaded = 0
    skipped = 0
    errors = 0

    for slug, page_url in DENOMINATION_PAGES.items():
        dest = DATA_DIR / slug
        dest.mkdir(parents=True, exist_ok=True)

        print(f"\n=== {slug} ===")
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  FAILED to fetch listing page: {e}")
            errors += 1
            continue

        links = LINK_PATTERN.findall(resp.text)
        print(f"  Found {len(links)} result file(s)")

        for href, label in links:
            filename = href.rsplit("/", 1)[-1]
            filepath = dest / filename

            if filepath.exists():
                skipped += 1
                continue

            try:
                file_resp = requests.get(href, headers=HEADERS, timeout=60)
                file_resp.raise_for_status()
                filepath.write_bytes(file_resp.content)
                print(f"  DOWNLOADED {filename}")
                downloaded += 1
            except Exception as e:
                print(f"  FAILED {filename}: {e}")
                errors += 1

    print(f"\n--- Summary ---")
    print(f"Downloaded: {downloaded}")
    print(f"Skipped (already exist): {skipped}")
    print(f"Errors: {errors}")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
