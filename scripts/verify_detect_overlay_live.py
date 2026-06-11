"""Playwright verification of the bbox overlay over the *live* robot camera.

Differs from verify_detect_overlay.py: this one clicks the dashboard's
Connect button first so the robot WebSocket attaches and the Head /
Gripper camera tiles render real frames. Then it injects a synthetic
detection and confirms the SVG rect lands on top of the live video.
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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()

        # Surface browser console messages to help diagnose connect failures.
        page.on("console", lambda m: print(f"[console.{m.type}] {m.text}"))
        page.on("pageerror", lambda e: print(f"[pageerror] {e}"))

        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("load", timeout=30_000)
        page.wait_for_selector("h2", timeout=10_000)

        # --- 1. Connect to the robot. ----------------------------------------
        host_input = page.locator("input[placeholder='Robot IP']")
        host_input.wait_for(state="visible", timeout=5_000)
        host_input.fill(ROBOT_HOST)
        connect_btn = page.locator("button", has_text="Connect")
        connect_btn.first.click()
        print(f"[OK] clicked Connect for host={ROBOT_HOST}")

        # The button text changes to "Disconnect" once the WebSocket is up.
        disconnect_btn = page.locator("button", has_text="Disconnect")
        disconnect_btn.wait_for(state="visible", timeout=15_000)
        print("[OK] WebSocket connected (Connect → Disconnect)")

        # --- 2. Wait for the head camera canvas to have natural pixels. ------
        # CameraView only sets canvas.width once the first MJPEG frame loads.
        head_label = page.locator("span", has_text="Head").first
        head_tile = head_label.locator(
            "xpath=ancestor::div[contains(@class,'rounded-md') and contains(@class,'border-border')][1]"
        )
        head_canvas = head_tile.locator("canvas").first
        head_canvas.wait_for(state="visible", timeout=10_000)

        # Poll the canvas's natural width up to 15s for the first frame.
        natural_w = 0
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            natural_w = head_canvas.evaluate("c => c.width")
            if natural_w and natural_w > 0:
                break
            time.sleep(0.3)
        if not (natural_w and natural_w > 0):
            print(
                "[WARN] head camera produced no frames in 15s — overlay will "
                "still be tested but on a NO SIGNAL background."
            )
        else:
            print(f"[OK] head canvas has frames: natural width = {natural_w}px")

        # Screenshot the live-video state BEFORE injection so we can compare.
        head_tile.screenshot(path=str(OUT_DIR / "live_head_no_overlay.png"))

        # --- 3. Inject a synthetic detection. --------------------------------
        payload = {
            "camera": "head",
            "query": "aspirin bottle or water bottle",
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

        # --- 4. Confirm the overlay rendered. --------------------------------
        chip = page.get_by_test_id("overlay-tag-head")
        expect(chip).to_be_visible(timeout=5_000)
        chip_text = chip.inner_text().strip()
        assert chip_text == "2 BOX", f"chip text wrong: {chip_text!r}"
        print(f"[OK] overlay chip: {chip_text!r}")

        outline_rects = head_tile.locator("svg rect[fill='none']")
        outline_rects.first.wait_for(state="visible", timeout=5_000)
        assert outline_rects.count() == 2

        # Confirm the label text renders the full string (the bug we just
        # fixed truncated "92%" → "9"). all_inner_texts() returns None for
        # SVG <text> in headless chromium; use evaluate to get textContent.
        label_texts = head_tile.locator("svg text").evaluate_all(
            "els => els.map(e => e.textContent || '')"
        )
        print(f"[OK] label texts on overlay: {label_texts}")
        assert any("92%" in t for t in label_texts), f"missing 92%: {label_texts}"
        assert any("71%" in t for t in label_texts), f"missing 71%: {label_texts}"

        # --- 5. Screenshot for visual inspection. ---------------------------
        shot = OUT_DIR / "live_head_with_overlay.png"
        head_tile.screenshot(path=str(shot))
        full = OUT_DIR / "live_dashboard_with_overlay.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"[OK] screenshots: {shot}, {full}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
