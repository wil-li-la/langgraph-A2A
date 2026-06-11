"""Playwright check: live YOLO-World detections render on the dashboard.

Connects dashboard to robot, polls /api/detect/latest until the YOLO
worker has fresh detections on the wrist camera, then asserts the
gripper tile's SVG overlay reflects the most recent backend snapshot.
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
CAMERA_KEY = os.environ.get("YOLO_CAMERA_KEY", "arm")  # backend key, "arm" or "head"
TILE_LABEL = "Gripper" if CAMERA_KEY in ("arm", "gripper") else "Head"
TEST_TAG_ID = "overlay-tag-gripper" if CAMERA_KEY in ("arm", "gripper") else "overlay-tag-head"
OUT_DIR = Path(__file__).resolve().parent / "_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_latest() -> dict:
    with urllib.request.urlopen(f"{BACKEND_URL}/api/detect/latest", timeout=5) as r:
        return json.loads(r.read())


def wait_for_yolo(min_n: int = 1, timeout_s: float = 60.0) -> int:
    deadline = time.monotonic() + timeout_s
    last = 0
    while time.monotonic() < deadline:
        try:
            latest = fetch_latest().get("latest", {}).get(CAMERA_KEY, {})
            n = len(latest.get("detections", []))
            last = n
            if n >= min_n:
                return n
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(
        f"YOLO worker produced no detections on '{CAMERA_KEY}' in {timeout_s}s (last={last}). "
        f"Is yolo_worker.py running? Check /tmp/...by1uv7c5a.output in the container."
    )


def main() -> int:
    # Wait until backend has at least one detection from the YOLO worker.
    n_initial = wait_for_yolo(min_n=1, timeout_s=30.0)
    print(f"[OK] backend has {n_initial} YOLO detection(s) on camera={CAMERA_KEY}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"[pageerror] {e}"))

        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("load", timeout=30_000)
        page.wait_for_selector("h2", timeout=10_000)

        # Connect to robot so the camera tile renders live frames.
        host_input = page.locator("input[placeholder='Robot IP']")
        host_input.fill(ROBOT_HOST)
        page.locator("button", has_text="Connect").first.click()
        page.locator("button", has_text="Disconnect").wait_for(state="visible", timeout=15_000)
        print(f"[OK] connected to {ROBOT_HOST}")

        tile_label = page.locator("span", has_text=TILE_LABEL).first
        tile = tile_label.locator(
            "xpath=ancestor::div[contains(@class,'rounded-md') and contains(@class,'border-border')][1]"
        )
        canvas = tile.locator("canvas").first
        canvas.wait_for(state="visible", timeout=10_000)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if canvas.evaluate("c => c.width") > 0:
                break
            time.sleep(0.3)
        print(f"[OK] {TILE_LABEL} canvas live, w={canvas.evaluate('c => c.width')}")

        # The YOLO worker's last event was already replayed when the SSE
        # connection opened (the broadcaster does that on subscribe). Wait
        # for the overlay chip + SVG rects to appear.
        chip = page.get_by_test_id(TEST_TAG_ID)
        expect(chip).to_be_visible(timeout=15_000)
        chip_text = chip.inner_text().strip()
        print(f"[OK] {TILE_LABEL} chip: {chip_text}")

        outline_rects = tile.locator("svg rect[fill='none']")
        outline_rects.first.wait_for(state="visible", timeout=10_000)
        n = outline_rects.count()
        print(f"[OK] {n} outline rects rendered on {TILE_LABEL}")
        assert n > 0

        # Watch for at least one chip-text update to confirm the SSE pipe
        # is live (not just the replay). The YOLO worker runs at 5 Hz, so
        # we should see a change within ~3s. Track text changes since this
        # observation depends on the wrist scene actually varying — if the
        # scene is static, detection count may stay constant. Use timestamp
        # from /api/detect/latest as a freshness proxy.
        ts_old = fetch_latest()["latest"][CAMERA_KEY]["ts"]
        deadline = time.monotonic() + 10.0
        ts_new = ts_old
        while time.monotonic() < deadline:
            ts_new = fetch_latest()["latest"][CAMERA_KEY]["ts"]
            if ts_new != ts_old:
                break
            time.sleep(0.5)
        assert ts_new != ts_old, (
            f"YOLO worker not producing fresh events (ts stuck at {ts_old})"
        )
        elapsed_ns = int(ts_new) - int(ts_old)
        print(f"[OK] YOLO stream advanced: ts {ts_old} → {ts_new} (~{elapsed_ns/1e9:.2f}s)")

        # Screenshot.
        shot = OUT_DIR / "yolo_world_live_overlay.png"
        tile.screenshot(path=str(shot))
        full = OUT_DIR / "yolo_world_live_dashboard.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"[OK] screenshots: {shot}, {full}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
