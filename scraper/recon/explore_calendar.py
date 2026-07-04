"""Recon spike: map the structure of stats.britishbaseball.org.uk.

Launches a real browser against the calendar page, records every network
request/response the SPA makes, and dumps the final rendered DOM. Output is
written under scraper/recon/output/ for manual inspection — this script is
throwaway investigation tooling, not part of the production scraper.

Run with: uv run python -m scraper.recon.explore_calendar
"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "https://stats.britishbaseball.org.uk/en/calendar"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    network_log = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_request(request):
            network_log.append(
                {
                    "type": "request",
                    "method": request.method,
                    "url": request.url,
                    "resource_type": request.resource_type,
                }
            )

        def on_response(response):
            entry = {
                "type": "response",
                "status": response.status,
                "url": response.url,
                "content_type": response.headers.get("content-type", ""),
            }
            ct = entry["content_type"]
            if "json" in ct:
                try:
                    entry["body"] = response.json()
                except Exception as e:
                    entry["body_error"] = str(e)
            network_log.append(entry)

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"Navigating to {BASE_URL} ...")
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        (OUTPUT_DIR / "calendar_initial.html").write_text(
            page.content(), encoding="utf-8"
        )

        # Try to discover interactive selectors (dropdowns, tabs, links) for
        # competitions/seasons so we can log their text/attributes for manual
        # inspection without guessing selector names blind.
        clickable_summary = page.eval_on_selector_all(
            "select, [role='combobox'], [role='tab'], a[href], button",
            """els => els.slice(0, 200).map(el => ({
                tag: el.tagName,
                role: el.getAttribute('role'),
                href: el.getAttribute('href'),
                text: (el.textContent || '').trim().slice(0, 80),
                classes: el.className,
            }))""",
        )
        (OUTPUT_DIR / "clickable_elements.json").write_text(
            json.dumps(clickable_summary, indent=2), encoding="utf-8"
        )

        browser.close()

    (OUTPUT_DIR / "network_log.json").write_text(
        json.dumps(network_log, indent=2, default=str), encoding="utf-8"
    )

    json_responses = [e for e in network_log if e.get("type") == "response" and "body" in e]
    print(f"Captured {len(network_log)} network events, {len(json_responses)} JSON responses.")
    print(f"Output written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
