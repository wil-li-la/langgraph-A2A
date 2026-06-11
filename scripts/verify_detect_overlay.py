"""Playwright verification of the live bbox overlay.

Flow:
  1. Open the dashboard.
  2. POST a synthetic detection event to /api/detect/inject.
  3. Confirm the dashboard's VIDEO panel renders an SVG <rect> on top of
     the Head camera tile, the bbox-count chip ("1 BOX") shows, and the
     overlay's normalized geometry matches what we injected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import urllib.request
import urllib.error
import json

from playwright.sync_api import sync_playwright, expect

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:9999")
OUT_DIR = Path(__file__).resolve().parent / "_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def inject(payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/detect/inject",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def main() -> int:
    # Sanity: backend has the new routes.
    try:
        check = urllib.request.urlopen(f"{BACKEND_URL}/api/detect/latest", timeout=3)
        check.read()
        print("[OK] backend /api/detect/latest reachable")
    except urllib.error.HTTPError as e:
        print(f"[FAIL] backend /api/detect/latest returned HTTP {e.code} — "
              "is the backend running with the new detect_stream routes?")
        return 1
    except Exception as e:
        print(f"[FAIL] backend not reachable: {e}")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("load", timeout=30_000)
        page.wait_for_selector("h2", timeout=10_000)

        # Find the Head camera tile.
        head_label = page.locator("span", has_text="Head").first
        head_label.wait_for(state="visible", timeout=5_000)
        head_tile = head_label.locator(
            "xpath=ancestor::div[contains(@class,'rounded-md') and contains(@class,'border-border')][1]"
        )
        head_tile.wait_for(state="visible", timeout=5_000)

        # No overlay yet.
        assert head_tile.locator("svg rect").count() == 0, "unexpected pre-injection bbox"
        print("[OK] no bbox before injection")

        # Inject a synthetic detection on the head camera.
        payload = {
            "camera": "head",
            "query": "aspirin bottle",
            "location": "pharmacy",
            "image_w": 1280,
            "image_h": 720,
            "detections": [
                {
                    "label": "aspirin bottle",
                    "bbox_2d": [320, 180, 640, 540],
                    "confidence": 0.92,
                    "description": "white pill bottle",
                },
                {
                    "label": "water bottle",
                    "bbox_2d": [800, 100, 1100, 600],
                    "confidence": 0.71,
                    "description": "clear plastic bottle",
                },
            ],
        }
        resp = inject(payload)
        print(f"[OK] inject response: {resp}")
        assert resp.get("ok") is True

        # Wait for the SSE event to update the React tree.
        chip = page.get_by_test_id("overlay-tag-head")
        expect(chip).to_be_visible(timeout=5_000)
        chip_text = chip.inner_text().strip()
        assert chip_text == "2 BOX", f"chip text wrong: {chip_text!r}"
        print(f"[OK] overlay chip: {chip_text!r}")

        # SVG inside head tile contains 2 outline rects (label-background
        # rects are also <rect>, so check for fill='none' — those are the
        # actual bbox outlines).
        outline_rects = head_tile.locator("svg rect[fill='none']")
        outline_rects.first.wait_for(state="visible", timeout=5_000)
        n = outline_rects.count()
        assert n == 2, f"expected 2 outline rects, got {n}"
        print(f"[OK] outline rect count: {n}")

        # Sanity: first rect's coords match our payload.
        x = float(outline_rects.first.get_attribute("x"))
        y = float(outline_rects.first.get_attribute("y"))
        w = float(outline_rects.first.get_attribute("width"))
        h = float(outline_rects.first.get_attribute("height"))
        assert (x, y) == (320, 180), f"bbox origin wrong: ({x},{y})"
        assert (w, h) == (320, 360), f"bbox size wrong: ({w},{h})"
        print(f"[OK] bbox geometry: x={x} y={y} w={w} h={h}")

        # Screenshot for visual inspection.
        shot = OUT_DIR / "detect_overlay.png"
        head_tile.screenshot(path=str(shot))
        full = OUT_DIR / "dashboard_with_overlay.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"[OK] screenshots: {shot}, {full}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
