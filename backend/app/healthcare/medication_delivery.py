"""LangGraph-based medication delivery agent for HelloRobot Stretch."""

import operator
import time
import logging
from pathlib import Path
import rerun as rr
from datetime import datetime
from typing import Annotated, Generator, List, Tuple, TypedDict

from langgraph.graph import StateGraph, END

from cure.skills.grasp import grasp_skill
from cure.config import update_config, Config
from cure.skills.listen import listen_skill
from cure.skills.speak import speak_skill, wait_for_speech_completion
from cure.skills.handover import handover_skill
from cure.skills.navigate import navigate_skill

from app.healthcare.mock_data import MockDatabase, MockRobotActions

update_config(Config.from_yaml("./cure/config.yaml"))

logger = logging.getLogger(__name__)

# All .rrd files are saved here automatically after each run
RERUN_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "rerun"
RERUN_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Set to True when rr.init() has already been called
_rr_initialized = False


class AgentState(TypedDict):
    """State definition for medication delivery workflow."""
    patient_name: str
    medication_name: str
    current_location: str
    task_status: str
    target_detected: bool
    identity_verified: bool
    identity_check_retries: int
    errors: Annotated[List[str], operator.add]
    history: Annotated[List[str], operator.add]
    executed_nodes: Annotated[List[str], operator.add]


# --- Helper ---

def _setup_cure_loggers():
    """Ensure all cure skill loggers also write to rerun 'workflow/log'.

    cure's setup_skill_logger() may have already added handlers (e.g. its own
    rr.LoggingHandler("logs")), so we cannot rely on `if not handlers`.
    Instead we use a sentinel attribute to avoid double-adding our handler.
    """
    skills_to_patch = [
        "cure.skills.grasp",
        "cure.skills.listen",
        "cure.skills.speak",
        "cure.skills.handover",
        "cure.skills.navigate",
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
    """Pre-check validation: confirm patient and medication exist."""
    _log_node_entry("confirm_task", state)
    patient_name = state['patient_name']
    
    _section("🔍", f"確認任務: 給予 {patient_name} 藥物")
    
    patient_info = MockDatabase.get_patient(patient_name)
    if not patient_info:
        _log("✗", f"病患資料庫中無此人: {patient_name}")
        return _fail("confirm_task", "patient_not_found", f"無效病患: {patient_name}")
    
    _log("✓", "任務確認成功")
    return {
        "task_status": "task_confirmed",
        "history": [f"✓ 確認給藥任務: {patient_name}"],
        "executed_nodes": ["confirm_task"]
    }


def navigate_to_pharmacy_node(state: AgentState) -> dict:
    """Navigate robot to pharmacy to pick up medication."""
    _log_node_entry("nav_to_pharmacy", state)
    _section("🚶", f"導航中: 前往藥局領取 {state['medication_name']}")

    navigate_skill("medicine")
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

    success = grasp_skill("medicine")

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
    navigate_skill("patient")

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


def check_patient_identity_node(state: AgentState) -> dict:
    """Verify patient identity and medication awareness via voice with retries."""
    _log_node_entry("check_patient_identity", state)
    patient_name = state['patient_name']
    medication_name = state['medication_name']
    retries = state.get('identity_check_retries', 0)
    node = "check_patient_identity"

    _section("🔍", f"確認病患身份與用藥認知: {patient_name} (第 {retries + 1} 次嘗試)")

    # 1. Voice confirmation — identity
    q1 = f"您好，我是給藥機器人，請問你叫什麼名字"
    _log("🔊", f"語音播放: 「{q1}」")
    _speak_and_wait(q1)

    resp1 = listen_skill() or ""
    _log("🗣️", f"病患回覆: 「{resp1}」")
    rr.log("workflow/identity_check/voice_q1",
           rr.TextDocument(f"Q: {q1}\nA: {resp1}"))

    # Updated identity check — match full name in response
    if not resp1 or patient_name not in resp1:
        _log("✗", "病患否認身份或回覆不符")
        return {
            "task_status": "identity_failed",
            "identity_check_retries": retries + 1,
            "history": [f"✗ 身份確認失敗 (第 {retries + 1} 次): 病患回覆「{resp1}」"],
        }

    # 2. Announce medication time
    now = datetime.now()
    q2 = f"現在是{now.hour}點{now.minute}分，病患{patient_name}需要服用{medication_name}"
    _log("🔊", f"語音播放: 「{q2}」")
    _speak_and_wait(q2)

    # 3. All checks passed — hand off medication
    _log("✓", f"身份與用藥認知確認完成 — 病患: {patient_name}, 藥物: {medication_name}")

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
        "identity_verified": True,
        "task_status": "delivered",
        "history": [
            "✓ 語音身份確認通過",
            "✓ 用藥認知確認通過",
            f"✓ 藥物已遞交給 {patient_name}",
        ],
        "executed_nodes": [node],
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

    _section("🏠", f"返回原點: 從 {current_loc} 導航回充電座")
    navigate_skill("origin")

    _log("✓", "已返回原點")
    return {
        "current_location": "charging_dock",
        "history": [f"✓ 從 {current_loc} 返回原點"],
        "executed_nodes": ["return_to_origin"],
    }


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
    return _route_or_error(state, "ready_for_identity_check", "check_patient_identity")


def should_continue_after_identity(state: AgentState) -> str:
    status = state.get('task_status')
    if status == 'delivered':
        return "return_to_origin"
    
    # Retry logic
    if state.get('identity_check_retries', 0) < 3:
        _log("🔁", f"身份確認失敗，準備進行第 {state.get('identity_check_retries', 0) + 1} 次重試...")
        return "check_patient_identity"
    
    return "handle_error"


# --- Workflow Construction ---

def create_medication_delivery_workflow() -> StateGraph:
    """Create and compile the medication delivery workflow graph."""
    workflow = StateGraph(AgentState)

    # Nodes
    workflow.add_node("confirm_task", confirm_task_node)
    workflow.add_node("nav_to_pharmacy", navigate_to_pharmacy_node)
    workflow.add_node("pickup_med", pickup_medication_node)
    workflow.add_node("nav_to_patient", navigate_to_patient_node)
    workflow.add_node("delivery", deliver_to_patient_node)
    workflow.add_node("check_patient_identity", check_patient_identity_node)
    workflow.add_node("handle_error", error_handler_node)
    workflow.add_node("return_to_origin", return_to_origin_node)

    # Entry
    workflow.set_entry_point("confirm_task")

    # Edges
    workflow.add_conditional_edges("confirm_task", should_continue_after_confirm,
                                   {"nav_to_pharmacy": "nav_to_pharmacy", "handle_error": "handle_error"})
    workflow.add_edge("nav_to_pharmacy", "pickup_med")
    workflow.add_conditional_edges("pickup_med", should_continue_after_pickup,
                                   {"nav_to_patient": "nav_to_patient", "handle_error": "handle_error"})
    workflow.add_conditional_edges("nav_to_patient", should_continue_after_nav_to_patient,
                                   {"delivery": "delivery", "handle_error": "handle_error"})
    workflow.add_conditional_edges("delivery", should_continue_after_delivery,
                                   {"check_patient_identity": "check_patient_identity", "handle_error": "handle_error"})
    workflow.add_conditional_edges("check_patient_identity", should_continue_after_identity,
                                   {"return_to_origin": "return_to_origin", 
                                    "check_patient_identity": "check_patient_identity",
                                    "handle_error": "handle_error"})
    workflow.add_edge("handle_error", "return_to_origin")
    workflow.add_edge("return_to_origin", END)

    return workflow.compile()


class MedicationDeliveryAgent:
    """Agent for executing medication delivery tasks."""

    def __init__(self):
        self.app = create_medication_delivery_workflow()

    def _build_initial_state(self, patient_name: str, medication_name: str) -> AgentState:
        """Build the initial AgentState dict."""
        return {
            "patient_name": patient_name,
            "medication_name": medication_name,
            "current_location": "charging_dock",
            "task_status": "initialized",
            "target_detected": False,
            "identity_verified": False,
            "identity_check_retries": 0,
            "errors": [],
            "history": [],
            "executed_nodes": [],
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

    def execute(self, patient_name: str, medication_name: str) -> dict:
        """Execute a medication delivery task (blocking)."""
        global _rr_initialized
        if not _rr_initialized:
            rr.init("medication_delivery", spawn=False)
            _rr_initialized = True
            # Log a small heartbeat to ENSURE the recording has data
            rr.log("workflow/heartbeat", rr.TextLog("Execution started", level="DEBUG"))

        _setup_cure_loggers()
        
        start_time = time.time()
        logger.info(f"\n{'#'*60}\n# 給藥任務開始\n{'#'*60}")

        initial_state = self._build_initial_state(patient_name, medication_name)
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
        self, patient_name: str, medication_name: str
    ) -> Generator[Tuple[str, str, dict], None, None]:
        """Execute workflow with per-node streaming.

        Yields (event_type, node_id, state_snapshot) tuples:
          - ("node_start", node_id, {executed_nodes so far})
          - ("node_end",   node_id, {executed_nodes so far, history, ...})
          - ("done",        "",      {full final state})
        """
        if not _rr_initialized:
            rr.init("medication_delivery", spawn=False)
        _setup_cure_loggers()

        start_time = time.time()
        logger.info(f"\n{'#'*60}\n# 給藥任務開始 (streaming)\n{'#'*60}")

        initial_state = self._build_initial_state(patient_name, medication_name)
        executed_nodes: list[str] = []
        final_state = dict(initial_state)

        try:
            for chunk in self.app.stream(initial_state):
                # chunk is {node_name: state_update_dict}
                for node_id, state_update in chunk.items():
                    # Emit node_start
                    yield ("node_start", node_id, {
                        "executed_nodes": list(executed_nodes),
                    })

                    # Merge state update
                    final_state.update(state_update)
                    # Track executed nodes from the update
                    new_nodes = state_update.get("executed_nodes", [])
                    executed_nodes.extend(new_nodes)
                    final_state["executed_nodes"] = list(executed_nodes)

                    # Emit node_end
                    yield ("node_end", node_id, {
                        "executed_nodes": list(executed_nodes),
                        "history": state_update.get("history", []),
                        "task_status": state_update.get("task_status", ""),
                    })
        except KeyboardInterrupt:
            logger.warning("任務被使用者手動中斷 (KeyboardInterrupt)")
            final_state["task_status"] = "interrupted"
            final_state["history"].append("⚠️ 任務被手動中斷")

        self._print_summary(final_state, time.time() - start_time)

        rrd_path = RERUN_LOG_DIR / f"medication_delivery_{patient_name}_{int(time.time())}.rrd"
        rr.save(str(rrd_path))
        logger.info(f"Rerun log saved to {rrd_path}")
        yield ("done", "", final_state)


# --- CLI for Testing ---

if __name__ == "__main__":
    import sys

    # Initialize Rerun here so the viewer can connect to this recording
    rr.init("medication_delivery", spawn=False)
    _rr_initialized = True

    # Spawn native viewer (better CJK font support than web viewer)
    try:
        rr.spawn()
    except Exception as e:
        logger.warning(f"Could not start Rerun viewer: {e}")

    examples = [
        ("張小明", "阿斯匹靈"),
        ("李美華", "普拿疼"),
        ("王大同", "維他命C"),
    ]

    if len(sys.argv) >= 3:
        p_name, m_name = sys.argv[1], sys.argv[2]
    else:
        logger.info("使用範例指令，您也可以用: python medication_delivery.py <病患> <藥物>")
        p_name, m_name = examples[0]

    agent = MedicationDeliveryAgent()
    result = agent.execute(p_name, m_name)

    logger.info("可用的測試指令範例:")
    for i, (p, m) in enumerate(examples, 1):
        logger.info(f"  {i}. {p} — {m}")
