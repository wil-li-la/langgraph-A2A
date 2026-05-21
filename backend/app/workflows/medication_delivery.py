"""LangGraph-based medication delivery agent for HelloRobot Stretch."""

import operator
import time
import logging
from pathlib import Path
import rerun as rr
from datetime import datetime
from typing import Annotated, Generator, List, Tuple, TypedDict

from langgraph.graph import StateGraph, END

# Drop-in replacements for cure's navigate/handover/speak/listen — these now
# talk to the stretch3-zmq driver directly. Only grasp_skill still comes from
# cure (heavy ArUco + IK + RGBD pipeline); when grasp is replaced (VLA or
# step-breakdown) the cure dep can be removed entirely.
from cure.skills.grasp import grasp_skill
from cure.config import update_config, Config
from app.tools.stretch_tools import (
    navigate_skill,
    get_workflow_location,
    LocationNotTaughtError,
    handover_skill,
    speak_skill,
    wait_for_speech_completion,
    listen_skill,
)

# Identifier the dashboard and runtime location store use to scope this
# workflow's pose registry. Must match the entry in
# backend/app/api/workflow.py's _WORKFLOW_REGISTRY.
WORKFLOW_ID = "medication_delivery"

# Names this workflow looks up from the runtime location store. The
# dashboard's Locations panel reads this list to show "required" markers
# next to each name.
REQUIRED_LOCATIONS: tuple[str, ...] = ("medicine", "patient", "origin")

from app.skills.browser_input import browser_input_skill
from app.mock_data import MockDatabase

# cure.skills.grasp still reads from cure.config — so we keep loading the
# yaml into cure's global config too. stretch_tools loads the same file via
# its own (smaller) loader.
_CURE_CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "cure" / "config.yaml"
if not _CURE_CONFIG_FILE.is_file():
    _CURE_CONFIG_FILE = Path("./cure/config.yaml")
update_config(Config.from_yaml(str(_CURE_CONFIG_FILE)))

logger = logging.getLogger(__name__)

# All .rrd files are saved here automatically after each run
RERUN_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "rerun"
RERUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Set to True when rr.init() has already been called
_rr_initialized = False

# In-memory store for paused workflow sessions.
_paused_sessions: dict[str, dict] = {}

# Session IDs that have requested a graceful stop. Consumed by stream_execute
# after each node_end — when present, the workflow pauses via the existing
# _paused_sessions mechanism instead of continuing.
_stop_requests: set[str] = set()


class AgentState(TypedDict):
    """State definition for medication delivery workflow."""
    patient_name: str
    medication_name: str
    current_location: str
    task_status: str
    target_detected: bool
    identity_verified: bool
    identity_check_retries: int
    mode: str  # "manual" (dashboard) or "auto" (A2A from external agent)
    session_id: str  # SSE session id; used by browser_input_skill in manual mode
    errors: Annotated[List[str], operator.add]
    history: Annotated[List[str], operator.add]
    executed_nodes: Annotated[List[str], operator.add]
    resume_from: str  # node ID to resume from (empty string = normal start)


# --- Helper ---

def _setup_cure_loggers():
    """Ensure all skill loggers also write to rerun 'workflow/log'.

    cure.skills.grasp's setup_skill_logger() may have already added handlers
    (its own rr.LoggingHandler("logs")), so we cannot rely on `if not handlers`.
    A sentinel attribute avoids double-adding our handler.

    Name kept for backwards compatibility; it now patches both cure.skills.grasp
    (still in use) and app.tools.stretch_tools (the new direct-ZMQ skills).
    """
    skills_to_patch = [
        "cure.skills.grasp",
        "app.tools.stretch_tools",
    ]
    for skill in skills_to_patch:
        skill_logger = logging.getLogger(skill)

        # Already patched by us — skip
        if getattr(skill_logger, "_app_rr_handler_added", False):
            continue

        # Add StreamHandler only if nothing at all is present yet
        if not skill_logger.handlers:
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
            skill_logger.addHandler(sh)

        # Always add our workflow/log rerun handler
        skill_logger.addHandler(rr.LoggingHandler("workflow/log"))
        skill_logger.setLevel(logging.DEBUG)
        skill_logger._app_rr_handler_added = True  # type: ignore[attr-defined]


def _log(icon: str, msg: str):
    """Unified log — goes to console AND rerun."""
    msg_full = f"{icon} {msg}"
    logger.info(msg_full)
    rr.log("workflow/log", rr.TextLog(msg_full, level="INFO"))


def _fail(node: str, status: str, error: str, history_msg: str | None = None) -> dict:
    """Build a failure return dict and log to rerun."""
    logger.error(f"Node {node} failed: {error}")
    rr.log("workflow/log", rr.TextLog(f"Node {node} failed: {error}", level="ERROR"))
    rr.log(f"workflow/{node}/error", rr.TextDocument(error))
    return {
        "task_status": status,
        "errors": [error],
        "history": [history_msg or f"✗ {error}"],
        "executed_nodes": [node],
    }


def _section(icon: str, title: str):
    msg_full = f"{icon} {title}"
    logger.info(f"\n{'='*60}\n{msg_full}\n{'='*60}")
    rr.log("workflow/log", rr.TextLog(msg_full, level="INFO"))


def _log_node_entry(node_name: str, state: AgentState):
    """Log node entry to rerun for visual timeline debugging."""
    step = len(state.get("executed_nodes", []))
    rr.set_time("node_step", sequence=step)
    rr.log(
        f"workflow/{node_name}/status",
        rr.TextDocument(f"status: {state.get('task_status', 'unknown')}"),
    )


def _speak_and_wait(text: str, timeout_s: float = 30.0):
    """Speak text and block until speech completes."""
    job_id = speak_skill(text)
    wait_for_speech_completion(job_id, timeout_s=timeout_s)


# --- Node Functions ---

def confirm_task_node(state: AgentState) -> dict:
    """Pre-check validation: confirm patient and medication exist.

    In auto mode (A2A), the upstream agent already validated the instruction,
    so we skip the local mock DB check and trust the parsed input.
    """
    _log_node_entry("confirm_task", state)
    patient_name = state['patient_name']
    medication_name = state['medication_name']
    mode = state.get('mode', 'manual')

    _section("🔍", f"確認任務: 給予 {patient_name} {medication_name} (mode={mode})")

    if mode == "auto":
        # A2A path — external agent (e.g. Google ADK) already resolved patient/medication.
        # Just validate that required fields are present.
        if not patient_name or not medication_name:
            return _fail("confirm_task", "missing_fields",
                         "A2A 指令缺少病患名稱或藥物名稱")
        _log("✓", f"A2A 任務確認成功: {patient_name} / {medication_name}")
        return {
            "task_status": "task_confirmed",
            "history": [f"✓ A2A 確認給藥任務: {patient_name} — {medication_name}"],
            "executed_nodes": ["confirm_task"],
        }

    # Manual path — validate against local mock database
    patient_info = MockDatabase.get_patient(patient_name)
    if not patient_info:
        _log("✗", f"病患資料庫中無此人: {patient_name}")
        return _fail("confirm_task", "patient_not_found", f"無效病患: {patient_name}")

    _log("✓", "任務確認成功")
    return {
        "task_status": "task_confirmed",
        "history": [f"✓ 確認給藥任務: {patient_name}"],
        "executed_nodes": ["confirm_task"],
    }


def navigate_to_pharmacy_node(state: AgentState) -> dict:
    """Navigate robot to pharmacy to pick up medication."""
    _log_node_entry("nav_to_pharmacy", state)
    _section("🚶", f"導航中: 前往藥局領取 {state['medication_name']}")

    try:
        tx, ty, ttheta = get_workflow_location(WORKFLOW_ID, "medicine")
    except LocationNotTaughtError as e:
        _log("✗", f"藥局位置尚未設定: {e}")
        return _fail("nav_to_pharmacy", "location_not_taught", str(e),
                     "✗ 藥局位置尚未在儀表板教學")
    _log("📍", f"藥局座標: x={tx:.3f}, y={ty:.3f}, θ={ttheta:.3f}")
    navigate_skill(tx, ty, ttheta)
    # 目前無確認導航是否失敗

    _log("✓", "已到達藥局")
    return {
        "current_location": "pharmacy",
        "task_status": "at_pharmacy",
        "history": ["✓ 導航至藥局"],
        "executed_nodes": ["nav_to_pharmacy"],
    }


def pickup_medication_node(state: AgentState) -> dict:
    """Call grasp_skill() to detect and pick up the medication.

    grasp() has built-in medication detection, so a separate detect step
    is unnecessary. Returns bool indicating success.
    """
    _log_node_entry("pickup_med", state)
    _section("🤖", f"機械臂操作中: 抓取 {state['medication_name']}")

    try:
        success = grasp_skill("medicine")
    except TypeError as e:
        # IK solver returns None when it can't reach the target pose
        _log("✗", "偵測到標記但 IK 無法到達目標位置")
        return _fail("pickup_med", "pickup_failed",
                      f"偵測到藥物標記但機械臂無法到達 (IK failed): {e}",
                      f"✗ 偵測到標記但 IK 無法到達: {state['medication_name']}")

    if not success:
        _log("✗", "藥物抓取失敗")
        return _fail("pickup_med", "pickup_failed", f"藥物抓取失敗: {state['medication_name']}",
                      f"✗ 藥物抓取失敗: {state['medication_name']}")

    _log("✓", f"藥物已抓取: {state['medication_name']}")
    return {
        "target_detected": True,
        "task_status": "med_picked",
        "history": [f"✓ 藥物已抓取: {state['medication_name']}"],
        "executed_nodes": ["pickup_med"],
    }

def navigate_to_patient_node(state: AgentState) -> dict:
    """Navigate robot to patient room."""
    _log_node_entry("nav_to_patient", state)
    patient_name = state['patient_name']

    #無需確認病人資料庫
    _section("🚶", f"導航中: 前往 {patient_name} 的病房")
    try:
        tx, ty, ttheta = get_workflow_location(WORKFLOW_ID, "patient")
    except LocationNotTaughtError as e:
        _log("✗", f"病房位置尚未設定: {e}")
        return _fail("nav_to_patient", "location_not_taught", str(e),
                     "✗ 病房位置尚未在儀表板教學")
    _log("📍", f"病房座標: x={tx:.3f}, y={ty:.3f}, θ={ttheta:.3f}")
    navigate_skill(tx, ty, ttheta)

    #目前無需確認是否導航失敗
    _log("✓", "已到達病房")
    return {
        "current_location": "patient_room",
        "task_status": "at_patient_room",
        "history": ["✓ 導航至病房"],
        "executed_nodes": ["nav_to_patient"],
    }


def deliver_to_patient_node(state: AgentState) -> dict:
    """Announce arrival and initiate delivery interaction."""
    _log_node_entry("delivery", state)
    return {
        "task_status": "ready_for_identity_check",
        "history": ["✓ 已播報到達通知"],
        "executed_nodes": ["delivery"],
    }


def check_identity_node(state: AgentState) -> dict:
    """Verify patient identity via voice with retries."""
    _log_node_entry("check_identity", state)
    patient_name = state['patient_name']
    retries = state.get('identity_check_retries', 0)

    _section("🔍", f"確認病患身份: {patient_name} (第 {retries + 1} 次嘗試)")

    # Voice confirmation — identity
    q1 = "您好，我是給藥機器人，請問你叫什麼名字"
    _log("🔊", f"語音播放: 「{q1}」")
    _speak_and_wait(q1)

    # Manual mode (dashboard) uses browser-side voice/text input; auto mode
    # (A2A) uses the on-robot ASR via cure.skills.listen.
    mode = state.get("mode", "auto")
    session_id = state.get("session_id", "")
    if mode == "manual" and session_id:
        resp1 = browser_input_skill(session_id, q1)
    else:
        resp1 = listen_skill() or ""
    _log("🗣️", f"病患回覆: 「{resp1}」")
    rr.log("workflow/identity_check/voice_q1",
           rr.TextDocument(f"Q: {q1}\nA: {resp1}"))

    # Match full name in response
    if not resp1 or patient_name not in resp1:
        _log("✗", "病患否認身份或回覆不符")
        return {
            "task_status": "identity_failed",
            "identity_check_retries": retries + 1,
            "history": [f"✗ 身份確認失敗 (第 {retries + 1} 次): 病患回覆「{resp1}」"],
            "executed_nodes": ["check_identity"],
        }

    _log("✓", f"身份確認通過 — 病患: {patient_name}")
    return {
        "identity_verified": True,
        "task_status": "identity_verified",
        "history": ["✓ 語音身份確認通過"],
        "executed_nodes": ["check_identity"],
    }


def hand_medicine_node(state: AgentState) -> dict:
    """Announce medication and hand over to patient."""
    _log_node_entry("hand_medicine", state)
    patient_name = state['patient_name']
    medication_name = state['medication_name']

    _section("🤝", f"遞交藥物: {medication_name} → {patient_name}")

    # Announce medication time
    now = datetime.now()
    q2 = f"現在是{now.hour}點{now.minute}分，病患{patient_name}需要服用{medication_name}"
    _log("🔊", f"語音播放: 「{q2}」")
    _speak_and_wait(q2)

    # Hand off medication
    msg_handoff = "給藥確認完成，謝謝您，請拿取藥物。"
    _log("🔊", f"語音播放: 「{msg_handoff}」")
    _speak_and_wait(msg_handoff)

    _log("🤝", "遞交藥物中...")
    handover_skill()
    _log("✓", "藥物已成功遞交")

    confirmation = f"給藥任務已全數完成，{patient_name} 的 {medication_name} 已安全送達。祝您早日康復！"
    _log("🔊", f"語音播放: 「{confirmation}」")
    _speak_and_wait(confirmation)

    return {
        "task_status": "delivered",
        "history": [
            "✓ 用藥認知確認通過",
            f"✓ 藥物已遞交給 {patient_name}",
        ],
        "executed_nodes": ["hand_medicine"],
    }


def error_handler_node(state: AgentState) -> dict:
    """Handle errors and request human intervention."""
    _log_node_entry("handle_error", state)
    _section("⚠️", "錯誤處理: 任務中斷")
    for error in state.get('errors', []):
        _log("  -", error)
    _log("🆘", "請求人工協助...")

    return {
        "task_status": "failed",
        "history": ["✗ 任務失敗，已請求人工協助"],
        "executed_nodes": ["handle_error"],
    }


def return_to_origin_node(state: AgentState) -> dict:
    """Navigate robot back to the origin (charging dock) after task completion or failure."""
    _log_node_entry("return_to_origin", state)
    current_loc = state.get("current_location", "charging_dock")

    if current_loc == "charging_dock":
        _log("✓", "機器人已在原點，無需移動")
        return {
            "history": ["✓ 機器人已在原點"],
            "executed_nodes": ["return_to_origin"],
        }

    _section("🏠", f"返回原點: 從 {current_loc} 直接導航回充電座")
    try:
        tx, ty, ttheta = get_workflow_location(WORKFLOW_ID, "origin")
    except LocationNotTaughtError as e:
        _log("✗", f"原點位置尚未設定: {e}")
        return _fail("return_to_origin", "location_not_taught", str(e),
                     "✗ 原點位置尚未在儀表板教學")
    _log("📍", f"原點座標: x={tx:.3f}, y={ty:.3f}, θ={ttheta:.3f}")
    navigate_skill(tx, ty, ttheta)

    _log("✓", "已返回原點")
    return {
        "current_location": "charging_dock",
        "history": [f"✓ 從 {current_loc} 返回原點"],
        "executed_nodes": ["return_to_origin"],
    }


def resume_router_node(state: AgentState) -> dict:
    """Entry point router — passes through state unchanged."""
    _log_node_entry("resume_router", state)
    return {"executed_nodes": ["resume_router"]}


# --- Conditional Edge Functions ---

def _route_or_error(state: AgentState, ok_status: str, next_node: str) -> str:
    """Generic router: go to next_node if status matches, else error."""
    return next_node if state.get('task_status') == ok_status else "handle_error"


def should_continue_after_confirm(state: AgentState) -> str:
    return _route_or_error(state, "task_confirmed", "nav_to_pharmacy")


def should_continue_after_pickup(state: AgentState) -> str:
    if state.get('target_detected') and state.get('task_status') == 'med_picked':
        return "nav_to_patient"
    return "handle_error"


def should_continue_after_nav_to_patient(state: AgentState) -> str:
    return _route_or_error(state, "at_patient_room", "delivery")


def should_continue_after_delivery(state: AgentState) -> str:
    return _route_or_error(state, "ready_for_identity_check", "check_identity")


def should_continue_after_identity(state: AgentState) -> str:
    status = state.get('task_status')
    if status == 'identity_verified':
        return "hand_medicine"
    return "handle_error"


def should_continue_after_handover(state: AgentState) -> str:
    return _route_or_error(state, "delivered", "return_to_origin")


_VALID_RESUME_NODES = {
    "confirm_task", "nav_to_pharmacy", "pickup_med",
    "nav_to_patient", "delivery", "check_identity",
    "hand_medicine", "return_to_origin",
}


def route_from_resume_router(state: AgentState) -> str:
    target = state.get("resume_from", "")
    if target and target in _VALID_RESUME_NODES:
        return target
    return "confirm_task"


# Map each node to the router function that decides its next step.
_NODE_ROUTERS = {
    "confirm_task": should_continue_after_confirm,
    "pickup_med": should_continue_after_pickup,
    "nav_to_patient": should_continue_after_nav_to_patient,
    "delivery": should_continue_after_delivery,
    "check_identity": should_continue_after_identity,
    "hand_medicine": should_continue_after_handover,
}


def _should_pause(node_id: str, state: dict) -> bool:
    """Check if a node's output would route to handle_error."""
    router = _NODE_ROUTERS.get(node_id)
    if router is None:
        return False
    return router(state) == "handle_error"


# --- Workflow Construction ---

def create_medication_delivery_workflow() -> StateGraph:
    """Create and compile the medication delivery workflow graph."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("resume_router", resume_router_node)
    workflow.add_node("confirm_task", confirm_task_node)
    workflow.add_node("nav_to_pharmacy", navigate_to_pharmacy_node)
    workflow.add_node("pickup_med", pickup_medication_node)
    workflow.add_node("nav_to_patient", navigate_to_patient_node)
    workflow.add_node("delivery", deliver_to_patient_node)
    workflow.add_node("check_identity", check_identity_node)
    workflow.add_node("hand_medicine", hand_medicine_node)
    workflow.add_node("handle_error", error_handler_node)
    workflow.add_node("return_to_origin", return_to_origin_node)

    # Entry
    workflow.set_entry_point("resume_router")

    # Edges
    workflow.add_conditional_edges("resume_router", route_from_resume_router, {
        "confirm_task": "confirm_task",
        "nav_to_pharmacy": "nav_to_pharmacy",
        "pickup_med": "pickup_med",
        "nav_to_patient": "nav_to_patient",
        "delivery": "delivery",
        "check_identity": "check_identity",
        "hand_medicine": "hand_medicine",
        "return_to_origin": "return_to_origin",
    })
    workflow.add_conditional_edges("confirm_task", should_continue_after_confirm,
                                   {"nav_to_pharmacy": "nav_to_pharmacy", "handle_error": "handle_error"})
    workflow.add_edge("nav_to_pharmacy", "pickup_med")
    workflow.add_conditional_edges("pickup_med", should_continue_after_pickup,
                                   {"nav_to_patient": "nav_to_patient", "handle_error": "handle_error"})
    workflow.add_conditional_edges("nav_to_patient", should_continue_after_nav_to_patient,
                                   {"delivery": "delivery", "handle_error": "handle_error"})
    workflow.add_conditional_edges("delivery", should_continue_after_delivery,
                                   {"check_identity": "check_identity", "handle_error": "handle_error"})
    workflow.add_conditional_edges("check_identity", should_continue_after_identity,
                                   {"hand_medicine": "hand_medicine",
                                    "handle_error": "handle_error"})
    workflow.add_conditional_edges("hand_medicine", should_continue_after_handover,
                                   {"return_to_origin": "return_to_origin", "handle_error": "handle_error"})
    workflow.add_edge("handle_error", "return_to_origin")
    workflow.add_edge("return_to_origin", END)

    return workflow.compile()


class MedicationDeliveryAgent:
    """Agent for executing medication delivery tasks."""

    def __init__(self):
        self.app = create_medication_delivery_workflow()

    def _build_initial_state(
        self, patient_name: str, medication_name: str, *, mode: str = "auto"
    ) -> AgentState:
        """Build the initial AgentState dict."""
        return {
            "patient_name": patient_name,
            "medication_name": medication_name,
            "current_location": "charging_dock",
            "task_status": "initialized",
            "target_detected": False,
            "identity_verified": False,
            "identity_check_retries": 0,
            "mode": mode,
            "session_id": "",
            "errors": [],
            "history": [],
            "executed_nodes": [],
            "resume_from": "",
        }

    def _print_summary(self, final_state: dict, execution_time: float):
        """Print execution summary to console and rerun."""
        logger.info(f"\n{'#'*60}\n# 任務執行摘要\n{'#'*60}")
        logger.info(f"最終狀態: {final_state['task_status']}")
        logger.info(f"執行時間: {execution_time:.2f} 秒")
        logger.info("執行歷程:")
        for entry in final_state.get('history', []):
            logger.info(f"  {entry}")
        if final_state.get('errors'):
            logger.warning("錯誤記錄:")
            for error in final_state['errors']:
                logger.warning(f"  ✗ {error}")
        status_icon = "✅" if final_state['task_status'] == 'delivered' else "❌"
        status_text = "給藥任務完成！" if final_state['task_status'] == 'delivered' else "給藥任務失敗"
        logger.info(f"{status_icon} {status_text}")

        # Log summary to rerun
        summary_lines = final_state.get('history', [])
        rr.log("workflow/summary", rr.TextDocument(
            f"status: {final_state['task_status']}\n"
            f"time: {execution_time:.2f}s\n"
            + "\n".join(summary_lines)
        ))

    def execute(self, patient_name: str, medication_name: str, *, mode: str = "auto") -> dict:
        """Execute a medication delivery task (blocking)."""
        global _rr_initialized
        if not _rr_initialized:
            rr.init("medication_delivery", spawn=False)
            _rr_initialized = True
            # Log a small heartbeat to ENSURE the recording has data
            rr.log("workflow/heartbeat", rr.TextLog("Execution started", level="DEBUG"))

        _setup_cure_loggers()

        start_time = time.time()
        logger.info(f"\n{'#'*60}\n# 給藥任務開始 (mode={mode})\n{'#'*60}")

        initial_state = self._build_initial_state(patient_name, medication_name, mode=mode)
        try:
            final_state = self.app.invoke(initial_state)
        except KeyboardInterrupt:
            logger.warning("任務被使用者手動中斷 (KeyboardInterrupt)")
            final_state = initial_state # use initial state or intermediate if possible, but invoke doesn't give us intermediate easily without using stream
            final_state["task_status"] = "interrupted"
            final_state["history"].append("⚠️ 任務被手動中斷")
            
        self._print_summary(final_state, time.time() - start_time)

        # Ensure all data is flushed to the buffer before saving
        rrd_path = RERUN_LOG_DIR / f"medication_delivery_{patient_name}_{int(time.time())}.rrd"
        rr.save(str(rrd_path))
        logger.info(f"Rerun log saved to {rrd_path}")
        
        return final_state

    def stream_execute(
        self, patient_name: str, medication_name: str, *, mode: str = "auto",
        session_id: str = "", resume_state: dict | None = None,
        start_from: str = "",
    ) -> Generator[Tuple[str, str, dict], None, None]:
        """Execute workflow with per-node streaming.

        `start_from` (optional): when non-empty and `resume_state` is None,
        seeds the initial state's `resume_from` field so the workflow begins
        at the named node instead of confirm_task. Must be a valid node id
        (see _VALID_RESUME_NODES) — unknown values silently fall back to
        confirm_task via route_from_resume_router.

        Yields (event_type, node_id, data) tuples:
          - ("node_start", node_id, {executed_nodes so far})
          - ("node_end",   node_id, {executed_nodes, history, task_status})
          - ("paused",     node_id, {session_id, reason, task_status, executed_nodes})
          - ("done",        "",      {full final state})
        """
        if not _rr_initialized:
            rr.init("medication_delivery", spawn=False)
        _setup_cure_loggers()

        start_time = time.time()
        logger.info(f"\n{'#'*60}\n# 給藥任務開始 (streaming, mode={mode})\n{'#'*60}")

        if resume_state is not None:
            initial_state = resume_state
        else:
            initial_state = self._build_initial_state(patient_name, medication_name, mode=mode)
            if start_from:
                initial_state["resume_from"] = start_from
        initial_state["session_id"] = session_id

        executed_nodes: list[str] = list(initial_state.get("executed_nodes", []))
        final_state = dict(initial_state)

        paused = False
        try:
            try:
                for chunk in self.app.stream(initial_state):
                    for node_id, state_update in chunk.items():
                        # Skip the resume_router node in events
                        if node_id == "resume_router":
                            final_state.update(state_update)
                            new_nodes = state_update.get("executed_nodes", [])
                            executed_nodes.extend(new_nodes)
                            final_state["executed_nodes"] = list(executed_nodes)
                            continue

                        yield ("node_start", node_id, {
                            "executed_nodes": list(executed_nodes),
                            "session_id": session_id,
                        })

                        final_state.update(state_update)
                        new_nodes = state_update.get("executed_nodes", [])
                        executed_nodes.extend(new_nodes)
                        final_state["executed_nodes"] = list(executed_nodes)

                        yield ("node_end", node_id, {
                            "executed_nodes": list(executed_nodes),
                            "history": state_update.get("history", []),
                            "task_status": state_update.get("task_status", ""),
                            "session_id": session_id,
                        })

                        # In manual mode, check if this node failed OR user requested stop
                        user_stop = mode == "manual" and session_id in _stop_requests
                        node_failed = mode == "manual" and _should_pause(node_id, final_state)
                        if user_stop or node_failed:
                            if node_failed:
                                errors = final_state.get("errors", [])
                                reason = errors[-1] if errors else final_state.get("task_status", "unknown error")
                            else:
                                reason = "Stopped by user"
                            if user_stop:
                                _stop_requests.discard(session_id)
                            _paused_sessions[session_id] = dict(final_state)
                            yield ("paused", node_id, {
                                "session_id": session_id,
                                "reason": reason,
                                "task_status": final_state.get("task_status", ""),
                                "executed_nodes": list(executed_nodes),
                            })
                            paused = True
                            break
                    if paused:
                        break

            except KeyboardInterrupt:
                logger.warning("任務被使用者手動中斷 (KeyboardInterrupt)")
                final_state["task_status"] = "interrupted"
                final_state["history"].append("⚠️ 任務被手動中斷")

            if not paused:
                self._print_summary(final_state, time.time() - start_time)
                rrd_path = RERUN_LOG_DIR / f"medication_delivery_{patient_name}_{int(time.time())}.rrd"
                rr.save(str(rrd_path))
                logger.info(f"Rerun log saved to {rrd_path}")
                _paused_sessions.pop(session_id, None)
                yield ("done", "", final_state)
        finally:
            # Always clear stop flag for this session — prevents leaks on exceptions
            _stop_requests.discard(session_id)


# --- CLI for Testing ---

if __name__ == "__main__":
    import sys
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    dry_run = "--dry-run" in sys.argv
    argv_args = [a for a in sys.argv[1:] if a != "--dry-run"]

    # Initialize Rerun (skip spawn — hangs when viewer is not installed)
    rr.init("medication_delivery", spawn=False)
    _rr_initialized = True

    examples = [
        ("張小明", "阿斯匹靈"),
        ("李美華", "普拿疼"),
        ("王大同", "維他命C"),
    ]

    if len(argv_args) >= 2:
        p_name, m_name = argv_args[0], argv_args[1]
    else:
        logger.info("使用範例指令，您也可以用: python -m app.workflows.medication_delivery <病患> <藥物>")
        p_name, m_name = examples[0]

    if dry_run:
        # Dry-run: patch module globals directly so node functions see the stubs.
        # (import-as would create a second module copy when __name__ == "__main__")
        _noop = lambda *a, **kw: True
        navigate_skill = _noop          # noqa: F811
        navigate_avoidance = _noop      # noqa: F811
        # Return a sentinel pose so nodes can log it without needing a real config.
        get_workflow_location = lambda wf, name: (0.0, 0.0, 0.0)  # noqa: F811
        grasp_skill = _noop             # noqa: F811
        speak_skill = lambda *a, **kw: "stub"  # noqa: F811
        wait_for_speech_completion = _noop      # noqa: F811
        listen_skill = lambda *a, **kw: p_name  # noqa: F811  # echo patient name to pass identity check
        handover_skill = _noop          # noqa: F811
        logger.info("🧪 Dry-run mode: robot skills stubbed")

    agent = MedicationDeliveryAgent()
    result = agent.execute(p_name, m_name)

    logger.info("可用的測試指令範例:")
    for i, (p, m) in enumerate(examples, 1):
        logger.info(f"  {i}. {p} — {m}")
