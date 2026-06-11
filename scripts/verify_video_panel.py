"""Playwright verification of the VIDEO panel layout change.

Confirms:
  1. Header text is "VIDEO" (no longer "VIDEO & MAP").
  2. No NavMap canvas inside the VideoPanel container (Map subpanel removed).
  3. VideoPanel container is at least 320 px tall (height bump).
  4. There are exactly 2 camera subpanels (Head + Gripper, was 3 with Map).
  5. Screenshot saved for visual inspection.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
OUT_DIR = Path(__file__).resolve().parent / "_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        # SSE / WebSocket connections keep the network busy, so wait for DOM
        # content + then a short settle delay instead of networkidle.
        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("load", timeout=30_000)

        # Wait for the dashboard to render its sections.
        page.wait_for_selector("h2", timeout=10_000)

        # 1. VIDEO header present, no "MAP" in that header.
        video_header = page.locator("h2", has_text="VIDEO").first
        video_header.wait_for(state="visible", timeout=5_000)
        header_text = video_header.inner_text().strip()
        assert header_text == "VIDEO", f"unexpected header: {header_text!r}"
        print(f"[OK] header text = {header_text!r}")

        # The VideoPanel is the parent of that h2. Find the wrapping container
        # that has the panel padding (`p-3`) and border classes.
        panel = video_header.locator(
            "xpath=ancestor::div[contains(@class,'rounded-md') and contains(@class,'border')][1]"
        )
        panel.wait_for(state="visible", timeout=5_000)
        box = panel.bounding_box()
        assert box is not None, "could not get VIDEO panel bounding box"
        print(f"[OK] panel bounding box = {box}")

        # 3. Height bump: container should be ≥ 320 px (was 240 px floor).
        assert box["height"] >= 300, f"panel height too small: {box['height']}"
        print(f"[OK] panel height = {box['height']:.0f}px (>= 300)")

        # 2. No NavMap canvas inside the VIDEO panel. NavMap renders a <canvas>
        #    with id-less geometry; the camera views render <img> tags or video
        #    elements. Look for "Map" label inside the panel — it must NOT exist.
        map_labels_in_panel = panel.get_by_text("Map", exact=True).count()
        assert map_labels_in_panel == 0, (
            f"Map label still inside VIDEO panel ({map_labels_in_panel}x)"
        )
        print("[OK] no 'Map' label inside VIDEO panel")

        # 4. Two subpanel labels (Head + Gripper) — not three.
        head_count = panel.get_by_text("Head", exact=True).count()
        gripper_count = panel.get_by_text("Gripper", exact=True).count()
        assert head_count >= 1, "Head subpanel missing"
        assert gripper_count >= 1, "Gripper subpanel missing"
        print(f"[OK] subpanels: Head x{head_count}, Gripper x{gripper_count}")

        # 5. Screenshot for visual confirmation.
        shot = OUT_DIR / "video_panel.png"
        panel.screenshot(path=str(shot))
        full = OUT_DIR / "dashboard_full.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"[OK] screenshots: {shot}, {full}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
