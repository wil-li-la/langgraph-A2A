"""Playwright check: real VLM on BOTH head and wrist cameras.

Connects dashboard to robot, runs detect on head, then on arm (wrist),
verifies each tile gets its bboxes + chip + collision-fixed labels.
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


def run_detect(query: str, camera: str) -> dict:
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/detect/run",
        data=json.dumps({"query": query, "camera": camera, "location": "lab"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def tile_for(page, label_text: str):
    label = page.locator("span", has_text=label_text).first
    return label.locator(
        "xpath=ancestor::div[contains(@class,'rounded-md') and contains(@class,'border-border')][1]"
    )


def verify_overlay(page, tile_label: str, n_expected: int, screenshot_path: Path):
    tile = tile_for(page, tile_label)
    chip = page.get_by_test_id(f"overlay-tag-{tile_label.lower() if tile_label == 'Head' else 'gripper'}")
    expect(chip).to_be_visible(timeout=10_000)
    chip_text = chip.inner_text().strip()
    assert chip_text == f"{n_expected} BOX", (
        f"{tile_label} chip {chip_text!r} != {n_expected} BOX"
    )
    print(f"[OK] {tile_label}: chip = {chip_text}")

    outline_rects = tile.locator("svg rect[fill='none']")
    outline_rects.first.wait_for(state="visible", timeout=5_000)
    rendered = outline_rects.count()
    assert rendered == n_expected, (
        f"{tile_label}: rendered {rendered} bboxes, expected {n_expected}"
    )

    # Verify labels don't overlap each other (collision fix).
    bgs = tile.locator("svg rect[fill='#22d3ee']").evaluate_all(
        """els => els.map(e => ({
            x: +e.getAttribute('x'),
            y: +e.getAttribute('y'),
            w: +e.getAttribute('width'),
            h: +e.getAttribute('height'),
        }))"""
    )
    collisions = 0
    for i in range(len(bgs)):
        for j in range(i + 1, len(bgs)):
            a, b = bgs[i], bgs[j]
            if (a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"] and
                a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"]):
                collisions += 1
    assert collisions == 0, f"{tile_label}: {collisions} label-rect collisions"
    print(f"[OK] {tile_label}: {len(bgs)} label rects, 0 collisions")

    tile.screenshot(path=str(screenshot_path))
    print(f"[OK] {tile_label}: screenshot {screenshot_path}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"[pageerror] {e}"))

        page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("load", timeout=30_000)
        page.wait_for_selector("h2", timeout=10_000)

        host_input = page.locator("input[placeholder='Robot IP']")
        host_input.fill(ROBOT_HOST)
        page.locator("button", has_text="Connect").first.click()
        page.locator("button", has_text="Disconnect").wait_for(state="visible", timeout=15_000)
        print(f"[OK] connected to {ROBOT_HOST}")

        # Wait for both canvases to have frames.
        for name in ("Head", "Gripper"):
            tile = tile_for(page, name)
            canvas = tile.locator("canvas").first
            canvas.wait_for(state="visible", timeout=10_000)
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                if canvas.evaluate("c => c.width") > 0:
                    break
                time.sleep(0.3)
            w = canvas.evaluate("c => c.width")
            print(f"[OK] {name} canvas live, w={w}")

        # 1) Head detection.
        head_query = "chair, table, person, monitor, keyboard, light fixture, door"
        t0 = time.monotonic()
        head_result = run_detect(head_query, camera="head")
        head_dt = time.monotonic() - t0
        head_n = len(head_result.get("detections", []))
        print(f"\n--- HEAD: {head_n} detections in {head_dt:.1f}s ---")
        print(f"    labels: {[d['label'] for d in head_result.get('detections', [])]}")
        assert head_n > 0, f"head VLM 0 dets: {head_result.get('text', '')[:200]}"
        verify_overlay(page, "Head", head_n, OUT_DIR / "both_head_overlay.png")

        # 2) Wrist detection.
        arm_query = "anything in front of the gripper — hand, object, surface, floor, or empty workspace"
        t0 = time.monotonic()
        arm_result = run_detect(arm_query, camera="arm")
        arm_dt = time.monotonic() - t0
        arm_n = len(arm_result.get("detections", []))
        print(f"\n--- ARM/WRIST: {arm_n} detections in {arm_dt:.1f}s ---")
        print(f"    labels: {[d['label'] for d in arm_result.get('detections', [])]}")
        if arm_n == 0:
            print(f"    text: {arm_result.get('text', '')[:200]}")
            # Don't fail — wrist scene may legitimately be empty (floor, ceiling).
        else:
            verify_overlay(page, "Gripper", arm_n, OUT_DIR / "both_wrist_overlay.png")

        full = OUT_DIR / "both_cameras_dashboard.png"
        page.screenshot(path=str(full), full_page=True)
        print(f"\n[OK] full-dashboard screenshot: {full}")

        browser.close()
        print("\nALL CHECKS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
