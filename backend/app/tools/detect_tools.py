"""VLM-driven open-vocabulary detection + JSON scene memory.

Captures a frame from the robot's head/arm camera, asks a vision-language
model (default: Qwen2.5-VL via Ollama) to find arbitrary objects by name,
and persists detections to a JSON file keyed by the robot's current
location. Recall tools read that JSON without moving the robot or burning
another VLM call, so the LLM agent can answer "where did I see the
aspirin?" later in the same task.

The detector LLM is intentionally separate from `app.llm.factory.get_llm()`
because the agent's chat model may be text-only (e.g. qwen3:4b) while the
detector needs vision. Configure independently:

  DETECT_PROVIDER       ollama (default) | openai | google | anthropic | none
  DETECT_MODEL          model id; default `qwen2.5vl:7b` for ollama
  DETECT_HOST           ollama host (default http://localhost:11434)
  DETECT_TIMEOUT        request timeout seconds (default 60)
  SCENE_MEMORY_FILE     JSON memory path (default backend/memory/scene_state.json)
  SCENE_MEMORY_MAX_SCENES        cap on scene records (default 200)
  SCENE_MEMORY_MAX_PER_OBJECT    cap per-object sightings (default 20)
  SCENE_MEMORY_RECALL_LIMIT      recall N most recent (default 5)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Optional

import cv2
import numpy as np
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from app.safety.guard import get_guard
from app.tools.stretch_tools import (
    _capture_arm_frame,
    _capture_head_frame,
    _dry_run,
    _rr_log,
)

logger = logging.getLogger(__name__)


# ---------- Detector LLM (separate from the agent's chat LLM) ------------

_det_lock = Lock()
_det_llm: Optional[object] = None
_det_sig: Optional[tuple] = None


def _det_signature() -> tuple:
    return (
        os.getenv("DETECT_PROVIDER", "ollama").lower(),
        os.getenv("DETECT_MODEL", "qwen2.5vl:7b"),
        os.getenv("DETECT_HOST", "http://localhost:11434"),
        os.getenv("DETECT_TIMEOUT", "60"),
    )


def _get_detector():
    """Return a cached vision-capable LangChain chat model, or None."""
    global _det_llm, _det_sig
    sig = _det_signature()
    with _det_lock:
        if _det_sig == sig:
            return _det_llm
        provider, model, host, timeout_s = sig
        try:
            timeout = float(timeout_s)
        except ValueError:
            timeout = 60.0
        llm = None
        try:
            if provider in ("", "none", "off", "disabled"):
                llm = None
            elif provider == "ollama":
                from langchain_ollama import ChatOllama
                llm = ChatOllama(
                    model=model,
                    base_url=host,
                    temperature=0,
                    timeout=timeout,
                    # ollama-native JSON mode — the model is told to emit
                    # valid JSON and the runtime constrains decoding.
                    format="json",
                )
            elif provider == "openai":
                from langchain_openai import ChatOpenAI
                llm = ChatOpenAI(
                    model=model,
                    temperature=0,
                    timeout=timeout,
                    model_kwargs={"response_format": {"type": "json_object"}},
                )
            elif provider == "google":
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(
                    model=model, temperature=0, timeout=timeout,
                )
            elif provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
                llm = ChatAnthropic(
                    model=model, temperature=0, timeout=timeout,
                )
            else:
                logger.warning("unknown DETECT_PROVIDER=%r; detector disabled", provider)
        except ImportError as e:
            logger.warning(
                "DETECT_PROVIDER=%s but its package isn't installed (%s); detector disabled",
                provider, e,
            )
            llm = None
        except Exception as e:
            logger.warning("failed to init detector LLM (%s); detector disabled", e)
            llm = None
        if llm is not None:
            logger.info("Detector enabled: provider=%s model=%s", provider, model)
        _det_llm = llm
        _det_sig = sig
        return llm


# ---------- Scene memory (JSON-backed, two indexes) ----------------------

_mem_lock = Lock()


def _memory_path() -> Path:
    p = os.getenv("SCENE_MEMORY_FILE")
    if p:
        return Path(p)
    # backend/app/tools/detect_tools.py  ->  backend/memory/scene_state.json
    return Path(__file__).resolve().parent.parent.parent / "memory" / "scene_state.json"


def _empty_memory() -> dict[str, Any]:
    return {"scenes": [], "objects": {}}


def _load_memory() -> dict[str, Any]:
    path = _memory_path()
    if not path.is_file():
        return _empty_memory()
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "scenes" not in data or "objects" not in data:
            return _empty_memory()
        return data
    except Exception as e:
        logger.warning("scene memory at %s corrupt (%s); starting fresh", path, e)
        return _empty_memory()


def _save_memory(data: dict[str, Any]) -> None:
    path = _memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _current_location() -> str:
    g = get_guard()
    if g is None:
        return "unknown"
    snap = g.snapshot()
    return str(snap.get("location") or "unknown")


def _persist_detection(
    query: str,
    camera: str,
    location: str,
    detections: list[dict],
    image_path: str,
) -> dict:
    """Append a scene record and index each detection by lowercased label."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "ts": ts,
        "location": location,
        "camera": camera,
        "query": query,
        "image_path": image_path,
        "detections": detections,
    }
    with _mem_lock:
        data = _load_memory()
        data["scenes"].append(record)
        max_scenes = int(os.getenv("SCENE_MEMORY_MAX_SCENES", "200"))
        if len(data["scenes"]) > max_scenes:
            data["scenes"] = data["scenes"][-max_scenes:]

        max_per_obj = int(os.getenv("SCENE_MEMORY_MAX_PER_OBJECT", "20"))
        for d in detections:
            label = (d.get("label") or "").strip().lower()
            if not label:
                continue
            entries = data["objects"].setdefault(label, [])
            entries.append({
                "ts": ts,
                "location": location,
                "camera": camera,
                "bbox_2d": d.get("bbox_2d"),
                "confidence": d.get("confidence"),
                "description": d.get("description"),
                "image_path": image_path,
            })
            if len(entries) > max_per_obj:
                data["objects"][label] = entries[-max_per_obj:]
        _save_memory(data)
    return record


# ---------- Frame capture + JPEG ----------------------------------------

def _capture(camera: str) -> tuple[np.ndarray, int]:
    if camera == "head":
        return _capture_head_frame(timeout_s=5.0)
    if camera in ("arm", "gripper", "wrist"):
        return _capture_arm_frame(timeout_s=5.0)
    raise ValueError(f"unknown camera={camera!r}; use 'head' or 'arm'")


def _save_jpeg(frame: np.ndarray, prefix: str, ts_ns: int) -> Path:
    out_dir = Path(os.getenv("ROBOT_SCREENSHOT_DIR", "/tmp/robot_screenshots"))
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{prefix}_{ts_ns}.jpg"
    cv2.imwrite(str(p), frame)
    return p


def _frame_to_data_url(frame: np.ndarray, jpeg_quality: int = 85) -> str:
    ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode("ascii")


# ---------- VLM prompt + parse ------------------------------------------

# Qwen2.5-VL returns bboxes in the input image's native pixel space when
# the prompt states the dimensions. Other VLMs (Gemini, GPT-4o) follow
# the same convention given the same instruction.
_PROMPT_TEMPLATE = """Find every instance of: {query}

The image is {w} x {h} pixels (top-left origin, pixel coordinates).

Return STRICT JSON ONLY — no prose, no markdown fences:
{{
  "detections": [
    {{
      "label": "<short noun phrase>",
      "bbox_2d": [x1, y1, x2, y2],
      "confidence": <float 0.0..1.0>,
      "description": "<one sentence about this instance>"
    }}
  ]
}}

If you see nothing matching, return exactly: {{"detections": []}}
"""


def _extract_json(text: str) -> Optional[dict]:
    """Find the first JSON object in `text` and parse it.

    Handles fenced ```json blocks and stray prose. Returns None if nothing
    parseable was found.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _normalize_detections(raw: dict, w: int, h: int) -> list[dict]:
    dets = raw.get("detections") if isinstance(raw, dict) else None
    if not isinstance(dets, list):
        return []
    out: list[dict] = []
    for d in dets:
        if not isinstance(d, dict):
            continue
        bbox = d.get("bbox_2d") or d.get("bbox") or d.get("box")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in bbox]
        except Exception:
            continue
        x1, x2 = sorted((max(0.0, x1), min(float(w), x2)))
        y1, y2 = sorted((max(0.0, y1), min(float(h), y2)))
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        try:
            conf = float(d.get("confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        out.append({
            "label": str(d.get("label", "")).strip(),
            "bbox_2d": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "confidence": conf,
            "description": str(d.get("description", "")).strip(),
        })
    return out


# ---------- @tool surface ------------------------------------------------


def _check_budget() -> Optional[str]:
    g = get_guard()
    if g is None:
        return None
    ok, reason = g.tick()
    if not ok:
        return f"BLOCKED: {reason}"
    return None


def _vlm_text(resp) -> str:
    """Extract the text payload from a LangChain chat response.

    OpenAI/Anthropic return content as a list of blocks when the model
    used multimodal input; Ollama returns a plain string. Unify here.
    """
    content = getattr(resp, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def detect_impl(
    query: str,
    camera: str = "head",
    location_override: Optional[str] = None,
    check_budget: bool = True,
) -> tuple[str, Optional[dict], list[dict]]:
    """Core detect logic, callable from agent tools or scripted workflows.

    Args:
        query, camera: see `detect`.
        location_override: pin scene memory under this location string
            instead of the RobotGuard's snapshot. Useful for scripted
            workflows that know their state without a guard installed.
        check_budget: skip the per-task budget check (False from scripted
            workflows that have no agent budget).

    Returns:
        (text_summary, record_dict_or_None, detections_list). On any
        failure, text starts with "FAILED:" / "BLOCKED:" and the other
        fields are (None, []).
    """
    if check_budget:
        if (b := _check_budget()):
            return b, None, []

    cam = (camera or "head").strip().lower()
    if cam not in ("head", "arm", "gripper", "wrist"):
        return f"FAILED: camera={camera!r} not understood. Use 'head' or 'arm'.", None, []

    if not query or not query.strip():
        return "FAILED: query required (e.g. 'aspirin bottle').", None, []

    if _dry_run():
        msg = (
            f"[DRY_RUN] would capture {cam} frame and ask VLM: 'Find {query}'. "
            f"VALIDATION: detector LLM (DETECT_PROVIDER="
            f"{os.getenv('DETECT_PROVIDER','ollama')} "
            f"DETECT_MODEL={os.getenv('DETECT_MODEL','qwen2.5vl:7b')}) "
            f"must be reachable and return JSON detections."
        )
        logger.info(msg)
        _rr_log("agent/tool/detect", msg)
        return f"OK [DRY_RUN]: would detect {query!r}. {msg}", None, []

    detector = _get_detector()
    if detector is None:
        return (
            "FAILED: no detector LLM configured. Set DETECT_PROVIDER and "
            "DETECT_MODEL (e.g. DETECT_PROVIDER=ollama DETECT_MODEL=qwen2.5vl:7b).",
            None, [],
        )

    try:
        frame, ts_ns = _capture(cam)
    except Exception as e:
        _rr_log("agent/tool/detect", f"capture FAILED: {e}", level="ERROR")
        return f"FAILED: could not capture {cam} camera: {e}", None, []

    h, w = frame.shape[:2]
    image_path = _save_jpeg(frame, prefix=f"detect_{cam}", ts_ns=ts_ns)

    try:
        data_url = _frame_to_data_url(frame)
    except Exception as e:
        return f"FAILED: image encode: {e}", None, []

    prompt = _PROMPT_TEMPLATE.format(query=query, w=w, h=h)
    msg = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ])

    _rr_log("agent/tool/detect", f"VLM query: {query!r} ({w}x{h}) on {cam}")
    t0 = time.monotonic()
    try:
        resp = detector.invoke([msg])
    except Exception as e:
        _rr_log("agent/tool/detect", f"VLM FAILED: {e}", level="ERROR")
        return f"FAILED: detector invoke raised: {e}", None, []
    dt = time.monotonic() - t0

    text = _vlm_text(resp)
    parsed = _extract_json(text)
    if parsed is None:
        _rr_log("agent/tool/detect", f"unparseable VLM output: {text!r}", level="WARN")
        return f"FAILED: VLM returned non-JSON output: {text[:300]}", None, []

    detections = _normalize_detections(parsed, w, h)
    location = (location_override or _current_location()).strip() or "unknown"
    record = _persist_detection(
        query=query, camera=cam, location=location,
        detections=detections, image_path=str(image_path),
    )

    # Broadcast to dashboard SSE subscribers. Lazy import to avoid pulling
    # Starlette into tool-only paths (e.g. CLI smoke tests).
    try:
        from app.api.detect_stream import publish_detection
        publish_detection(
            camera=cam,
            query=query,
            location=location,
            image_w=w,
            image_h=h,
            image_path=str(image_path),
            detections=detections,
            ts=record.get("ts"),
        )
    except Exception as e:
        logger.debug("detect_stream publish skipped: %s", e)

    if not detections:
        return (
            f"OK: 0 detections for {query!r} in {cam} camera at {location} "
            f"({w}x{h}, {dt:.2f}s VLM). Frame saved {image_path}."
        ), record, detections

    summary = "; ".join(
        f"{d['label']} (conf={d['confidence']:.2f}, bbox={d['bbox_2d']})"
        for d in detections
    )
    return (
        f"OK: {len(detections)} detection(s) for {query!r} at {location} "
        f"(camera={cam}, {dt:.2f}s VLM): {summary}. "
        f"Persisted to scene memory; recall via "
        f"recall_object('{detections[0]['label']}') or recall_scene('{location}'). "
        f"Raw: {json.dumps(record, ensure_ascii=False)}"
    ), record, detections


@tool
def ask_vlm(query: str, camera: str = "head") -> str:
    """Reason about the current camera view with a vision-language model.

    Use this when you need an answer the streaming YOLO-World detector
    can't give: ambiguous scenes, novel objects, scene description,
    "does this person look like a patient", or one-shot localization
    of something not in the streaming vocabulary. SLOW (1-8s on RTX
    4080) — call sparingly, not in a loop.

    For continuous presence/position of known classes, use the
    background YOLO-World stream instead (its detections automatically
    overlay on the dashboard; query them via recall_object / the
    /api/detect/latest endpoint). Don't call ask_vlm in a polling loop.

    Args:
        query: Natural language. Open-vocabulary. Examples:
               "aspirin bottle", "person wearing blue scrubs",
               "describe what is on the desk".
        camera: "head" (d435if scene) or "arm" (d405 wrist).

    Returns:
        Text summary of what the VLM saw. Starts with "OK:" /
        "FAILED:" / "BLOCKED:". Detections are persisted to scene
        memory and broadcast to the dashboard overlay automatically.

    Worked example — confirm an unusual scene before grasping:
        navigate_to("pharmacy")
        ask_vlm("is the medicine bottle upright and not blocked?")
        # If the VLM warns of a tipped bottle, change strategy.
        pick_up("medicine")
    """
    text, _record, _dets = detect_impl(query=query, camera=camera)
    return text


# Back-compat alias. Older agent prompts may still call `detect(...)`;
# the @tool decorator captures the function name in the schema, so we
# expose both names. Hidden from the default tool list — use ask_vlm.
@tool
def detect(query: str, camera: str = "head") -> str:
    """Deprecated alias for ask_vlm. Use ask_vlm() instead.

    Kept so older agent prompts and the scripted medication_delivery
    pre-grasp check don't break. New code should call ask_vlm() to make
    the slow-VLM intent explicit (vs the always-on YOLO-World stream).
    """
    text, _record, _dets = detect_impl(query=query, camera=camera)
    return text


@tool
def recall_object(object_name: str) -> str:
    """Look up what the robot has previously seen of a given object.

    Reads scene memory only — no camera, no LLM, no budget cost. Returns
    the most recent N sightings: timestamp, location, camera, bbox,
    confidence. Useful when the agent has already explored and wants to
    revisit a known object location.

    Args:
        object_name: Label to look up. Case-insensitive substring match
                     against labels the VLM produced. Examples: "aspirin",
                     "bottle", "person".

    Returns:
        Text summary of sightings, or "no memory" if nothing matches.

    Worked example — agent was asked to bring pills it already saw:
        recall_object("aspirin")
        # → "1 recent sighting: aspirin bottle @ pharmacy ts=...
        navigate_to("pharmacy")
        pick_up("medicine")
    """
    needle = (object_name or "").strip().lower()
    if not needle:
        return "FAILED: object_name required."
    data = _load_memory()
    hits: list[dict] = []
    for label, entries in data.get("objects", {}).items():
        if needle in label:
            for e in entries:
                hits.append({**e, "label": label})
    if not hits:
        return f"no memory: no prior detection matching {object_name!r}."
    hits.sort(key=lambda e: e.get("ts") or "", reverse=True)
    limit = int(os.getenv("SCENE_MEMORY_RECALL_LIMIT", "5"))
    hits = hits[:limit]
    lines = [
        f"  - {h['label']} @ {h['location']} ({h['camera']}) "
        f"ts={h['ts']} conf={(h.get('confidence') or 0.0):.2f} "
        f"bbox={h.get('bbox_2d')}"
        for h in hits
    ]
    return (
        f"recall_object({object_name!r}): {len(hits)} recent sighting(s)\n"
        + "\n".join(lines)
    )


@tool
def recall_scene(location: str = "") -> str:
    """List recent VLM observations, optionally filtered by location.

    Args:
        location: Optional location name (e.g. "pharmacy"). Empty = all.

    Returns:
        Text summary of recent scene records (ts, location, query, det count).
    """
    data = _load_memory()
    scenes = data.get("scenes", [])
    if location:
        loc = location.strip().lower()
        scenes = [s for s in scenes if (s.get("location") or "").lower() == loc]
    if not scenes:
        return f"no memory: no scenes recorded{' for ' + location if location else ''}."
    limit = int(os.getenv("SCENE_MEMORY_RECALL_LIMIT", "5"))
    scenes = scenes[-limit:]
    lines = [
        f"  - {s['ts']} @ {s['location']} ({s['camera']}) "
        f"query={s['query']!r} dets={len(s.get('detections', []))}"
        for s in scenes
    ]
    return (
        f"recall_scene({location or 'all'}): {len(scenes)} recent record(s)\n"
        + "\n".join(lines)
    )


@tool
def clear_scene_memory() -> str:
    """Erase all scene memory. Use between tasks or on operator request.

    Irreversible.
    """
    with _mem_lock:
        _save_memory(_empty_memory())
    _rr_log("agent/tool/clear_scene_memory", "memory cleared")
    return "OK: scene memory cleared."


# ---------- Public exports -----------------------------------------------


def get_detect_tools() -> list:
    """Tools to merge into the agent's tool list.

    `ask_vlm` is the canonical slow-VLM tool. `detect` is kept as a
    back-compat alias for older prompts/code paths; the streaming
    YOLO-World detections do not need a tool — they auto-publish to the
    dashboard overlay and to scene memory.
    """
    return [ask_vlm, detect, recall_object, recall_scene, clear_scene_memory]
