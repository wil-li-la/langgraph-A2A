"""Playwright check: real qwen2.5vl detections render on the dashboard overlay.

Connects the dashboard to the robot, fires POST /api/detect/run with a
real query, waits for the SVG overlay to populate from the SSE stream,
and screenshots the result.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:9999")
ROBOT_HOST = os.environ.get("ROBOT_HOST", "192.168.1.38")
QUERY = os.environ.get(
    "DETECT_QUERY",
    "chair, table, person, light fixture, door, monitor, keyboard, "
    "or any furniture",
)
OUT_DIR = Path(__file__).resolve().parent / "_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run_detect(query: str, camera: str = "head") -> dict:
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/detect/run",
        data=json.dumps({"query": query, "camera": camera, "location": "lab"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"[pageerror] {e}"))

        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("load", timeout=30_000)
        page.wait_for_selector("h2", timeout=10_000)

        # Connect to the robot.
        host_input = page.locator("input[placeholder='Robot IP']")
        host_input.fill(ROBOT_HOST)
        page.locator("button", has_text="Connect").first.click()
        page.locator("button", has_text="Disconnect").wait_for(state="visible", timeout=15_000)
        print(f"[OK] connected to {ROBOT_HOST}")

        # Wait for first head MJPEG frame.
        head_label = page.locator("span", has_text="Head").first
        head_tile = head_label.locator(
            "xpath=ancestor::div[contains(@class,'rounded-md') and contains(@class,'border-border')][1]"
        )
        head_canvas = head_tile.locator("canvas").first
        head_canvas.wait_for(state="visible", timeout=10_000)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if head_canvas.evaluate("c => c.width") > 0:
                break
            time.sleep(0.3)
        print(f"[OK] head canvas live, w={head_canvas.evaluate('c => c.width')}")

        # Trigger a REAL VLM detection.
        t0 = time.monotonic()
        result = run_detect(QUERY)
        dt = time.monotonic() - t0
        n = len(result.get("detections", []))
        print(f"[OK] /api/detect/run returned {n} detections in {dt:.1f}s")
        print(f"     query: {QUERY}")
        labels = [d.get("label") for d in result.get("detections", [])]
        print(f"     labels: {labels}")
        assert n > 0, f"VLM returned 0 detections — output: {result.get('text','')[:300]}"

        # The dashboard should now show the chip + N outline rects from SSE.
        chip = page.get_by_test_id("overlay-tag-head")
        expect(chip).to_be_visible(timeout=10_000)
        chip_text = chip.inner_text().strip()
        print(f"[OK] chip text: {chip_text!r}")
        assert chip_text == f"{n} BOX", (
            f"chip text {chip_text!r} doesn't match detection count {n}"
        )

        outline_rects = head_tile.locator("svg rect[fill='none']")
        outline_rects.first.wait_for(state="visible", timeout=5_000)
        rendered = outline_rects.count()
        assert rendered == n, f"rendered {rendered} bboxes, VLM returned {n}"
        print(f"[OK] rendered {rendered} bboxes on Head overlay")

        # Screenshot.
        shot = OUT_DIR / "real_vlm_head_overlay.png"
        head_tile.screenshot(path=str(shot))
        full = OUT_DIR / "real_vlm_dashboard.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"[OK] screenshots: {shot}, {full}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
