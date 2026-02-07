"""LangGraph-based medication delivery agent for HelloRobot Stretch."""

import operator
from typing import Annotated, List, TypedDict
import os
import time

from langgraph.graph import StateGraph, END
import mlflow

from app.healthcare.mock_data import MockDatabase, MockRobotActions, MockNLU

# Configure MLflow
mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"))
mlflow.set_experiment("medication_delivery_agent")


class AgentState(TypedDict):
    """State definition for medication delivery workflow."""
    
    # Original input and parsed results
    instruction: str
    patient_name: str
    medication_name: str
    
    # Execution progress and location
    current_location: str  # e.g., "pharmacy", "patient_room", "charging_dock"
    task_status: str       # e.g., "identifying_med", "navigating", "completed"
    
    # Perception results
    target_detected: bool
    identity_verified: bool
    
    # Error messages and logs
    errors: Annotated[List[str], operator.add]
    history: Annotated[List[str], operator.add]


# --- Node Functions ---

def nlu_parser_node(state: AgentState) -> dict:
    """Parse voice instruction to extract patient and medication names."""
    print(f"\n{'='*60}")
    print(f"🎤 正在解析指令: {state['instruction']}")
    print(f"{'='*60}")
    
    # Use mock NLU to parse instruction
    result = MockNLU.parse_instruction(state['instruction'])
    
    if result['success']:
        patient_name = result['patient_name']
        medication_name = result['medication_name']
        
        print(f"✓ 解析完成:")
        print(f"  - 病患: {patient_name}")
        print(f"  - 藥物: {medication_name}")
        print(f"  - 信心度: {result['confidence']}")
        
        return {
            "patient_name": patient_name,
            "medication_name": medication_name,
            "task_status": "parsed",
            "history": [f"✓ NLU解析: 病患={patient_name}, 藥物={medication_name}"]
        }
    else:
        print(f"✗ 解析失敗: {result['message']}")
        return {
            "task_status": "parse_failed",
            "errors": [result['message']],
            "history": [f"✗ NLU解析失敗: {result['message']}"]
        }


def navigate_to_pharmacy_node(state: AgentState) -> dict:
    """Navigate robot to pharmacy to pick up medication."""
    print(f"\n{'='*60}")
    print(f"🚶 導航中: 前往藥局領取 {state['medication_name']}")
    print(f"{'='*60}")
    
    # Simulate navigation
    nav_result = MockRobotActions.navigate(
        from_loc=state.get('current_location', 'charging_dock'),
        to_loc='pharmacy'
    )
    
    if nav_result['success']:
        print(f"✓ 已到達藥局")
        print(f"  - 路徑長度: {nav_result['path_length']}m")
        print(f"  - 耗時: {nav_result['duration']}秒")
        
        return {
            "current_location": "pharmacy",
            "task_status": "at_pharmacy",
            "history": [f"✓ 導航至藥局 (耗時: {nav_result['duration']}秒)"]
        }
    else:
        print(f"✗ 導航失敗")
        return {
            "task_status": "navigation_failed",
            "errors": ["導航至藥局失敗"],
            "history": ["✗ 導航至藥局失敗"]
        }


def pickup_medication_node(state: AgentState) -> dict:
    """Use vision system to locate medication and pick it up."""
    print(f"\n{'='*60}")
    print(f"👁️  視覺辨識中: 搜尋 {state['medication_name']}")
    print(f"{'='*60}")
    
    # Step 1: Detect medication using vision
    detect_result = MockRobotActions.detect_medication(state['medication_name'])
    
    if not detect_result['success']:
        print(f"✗ {detect_result['message']}")
        return {
            "target_detected": False,
            "task_status": "medication_not_found",
            "errors": [detect_result['message']],
            "history": [f"✗ 藥物辨識失敗: {state['medication_name']}"]
        }
    
    print(f"✓ 藥物已定位")
    print(f"  - 位置: {detect_result['location']}")
    print(f"  - 描述: {detect_result['description']}")
    print(f"  - 信心度: {detect_result['confidence']}")
    
    # Step 2: Pick up medication with robotic arm
    print(f"\n🤖 機械臂操作中: 抓取藥物")
    pickup_result = MockRobotActions.pickup_medication()
    
    if not pickup_result['success']:
        print(f"✗ {pickup_result['message']}")
        return {
            "target_detected": True,
            "task_status": "pickup_failed",
            "errors": [pickup_result['message']],
            "history": [
                f"✓ 藥物已定位: {state['medication_name']}",
                f"✗ 抓取失敗"
            ]
        }
    
    print(f"✓ {pickup_result['message']}")
    print(f"  - 夾爪力道: {pickup_result['force']}N")
    
    return {
        "target_detected": True,
        "task_status": "med_picked",
        "history": [
            f"✓ 藥物已定位: {state['medication_name']}",
            f"✓ 藥物已抓取 (力道: {pickup_result['force']}N)"
        ]
    }


def deliver_to_patient_node(state: AgentState) -> dict:
    """Navigate to patient room and verify identity before delivery."""
    patient_name = state['patient_name']
    medication_name = state['medication_name']
    
    # Get patient information
    patient_info = MockDatabase.get_patient(patient_name)
    if not patient_info:
        print(f"✗ 病患資料庫中無此人: {patient_name}")
        return {
            "identity_verified": False,
            "task_status": "patient_not_found",
            "errors": [f"病患資料庫中無此人: {patient_name}"],
            "history": [f"✗ 病患不存在: {patient_name}"]
        }
    
    room = patient_info['room']
    
    print(f"\n{'='*60}")
    print(f"🚶 導航中: 前往 {patient_name} 的病房 ({room}室)")
    print(f"{'='*60}")
    
    # Navigate to patient room
    nav_result = MockRobotActions.navigate(
        from_loc=state.get('current_location', 'pharmacy'),
        to_loc=f'room_{room}'
    )
    
    if not nav_result['success']:
        print(f"✗ 導航失敗")
        return {
            "identity_verified": False,
            "task_status": "navigation_failed",
            "errors": [f"導航至{room}室失敗"],
            "history": [f"✗ 導航至病房{room}失敗"]
        }
    
    print(f"✓ 已到達病房 {room}")
    print(f"  - 耗時: {nav_result['duration']}秒")
    
    # Announce arrival
    print(f"\n🔊 語音播報中...")
    greeting = f"您好{patient_name}，這是您的{medication_name}，請確認身份。"
    MockRobotActions.speak(greeting)
    print(f"   「{greeting}」")
    
    # Verify patient identity using face recognition
    print(f"\n👤 身份驗證中: 人臉辨識")
    verify_result = MockRobotActions.verify_identity(patient_name)
    
    if not verify_result['success']:
        print(f"✗ {verify_result['message']}")
        return {
            "identity_verified": False,
            "current_location": f"room_{room}",
            "task_status": "identity_verification_failed",
            "errors": [verify_result['message']],
            "history": [
                f"✓ 導航至病房{room} (耗時: {nav_result['duration']}秒)",
                f"✗ 身份驗證失敗"
            ]
        }
    
    print(f"✓ {verify_result['message']}")
    print(f"  - 信心度: {verify_result['confidence']}")
    print(f"  - Face ID: {verify_result['face_id']}")
    
    # Hand off medication
    print(f"\n🤝 遞交藥物中...")
    handoff_result = MockRobotActions.handoff_medication()
    print(f"✓ {handoff_result['message']}")
    
    # Final confirmation
    confirmation = f"給藥完成，請按時服用。祝您早日康復！"
    MockRobotActions.speak(confirmation)
    print(f"\n🔊 「{confirmation}」")
    
    return {
        "identity_verified": True,
        "current_location": f"room_{room}",
        "task_status": "delivered",
        "history": [
            f"✓ 導航至病房{room} (耗時: {nav_result['duration']}秒)",
            f"✓ 身份驗證成功: {patient_name}",
            f"✓ 藥物已遞交"
        ]
    }


def error_handler_node(state: AgentState) -> dict:
    """Handle errors and request human intervention."""
    print(f"\n{'='*60}")
    print(f"⚠️  錯誤處理: 任務中斷")
    print(f"{'='*60}")
    
    print(f"\n錯誤訊息:")
    for error in state.get('errors', []):
        print(f"  - {error}")
    
    print(f"\n🆘 請求人工協助...")
    
    return {
        "task_status": "failed",
        "history": ["✗ 任務失敗，已請求人工協助"]
    }


# --- Conditional Edge Functions ---

def should_continue_after_pickup(state: AgentState) -> str:
    """Determine next step after medication pickup attempt."""
    if state.get('target_detected', False) and state.get('task_status') == 'med_picked':
        return "delivery"
    else:
        return "handle_error"


def should_continue_after_parsing(state: AgentState) -> str:
    """Determine next step after NLU parsing."""
    if state.get('task_status') == 'parsed':
        return "nav_to_pharmacy"
    else:
        return "handle_error"


# --- Workflow Construction ---

def create_medication_delivery_workflow() -> StateGraph:
    """Create and compile the medication delivery workflow graph."""
    
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("nlu_parser", nlu_parser_node)
    workflow.add_node("nav_to_pharmacy", navigate_to_pharmacy_node)
    workflow.add_node("pickup_med", pickup_medication_node)
    workflow.add_node("delivery", deliver_to_patient_node)
    workflow.add_node("handle_error", error_handler_node)
    
    # Set entry point
    workflow.set_entry_point("nlu_parser")
    
    # Add conditional edges
    workflow.add_conditional_edges(
        "nlu_parser",
        should_continue_after_parsing,
        {
            "nav_to_pharmacy": "nav_to_pharmacy",
            "handle_error": "handle_error"
        }
    )
    
    workflow.add_edge("nav_to_pharmacy", "pickup_med")
    
    workflow.add_conditional_edges(
        "pickup_med",
        should_continue_after_pickup,
        {
            "delivery": "delivery",
            "handle_error": "handle_error"
        }
    )
    
    workflow.add_edge("delivery", END)
    workflow.add_edge("handle_error", END)
    
    return workflow.compile()



class MedicationDeliveryAgent:
    """Agent for executing medication delivery tasks."""
    
    def __init__(self):
        """Initialize the medication delivery agent."""
        self.app = create_medication_delivery_workflow()
    
    def execute(self, instruction: str) -> dict:
        """Execute a medication delivery task.
        
        Args:
            instruction: Voice command for medication delivery
            
        Returns:
            Final state after workflow execution
        """
        # Start MLflow run for tracking
        with mlflow.start_run():
            start_time = time.time()
            
            # Log input parameters
            mlflow.log_param("instruction", instruction)
            mlflow.set_tag("agent_type", "medication_delivery")
            mlflow.set_tag("robot_model", "HelloRobot_Stretch")
            
            print(f"\n{'#'*60}")
            print(f"# 給藥任務開始")
            print(f"{'#'*60}")
            
            # Initialize state
            initial_state = {
                "instruction": instruction,
                "patient_name": "",
                "medication_name": "",
                "current_location": "charging_dock",
                "task_status": "initialized",
                "target_detected": False,
                "identity_verified": False,
                "errors": [],
                "history": []
            }
            
            # Run workflow
            final_state = self.app.invoke(initial_state)
            
            # Calculate execution time
            execution_time = time.time() - start_time
            
            # Log execution details
            mlflow.log_params({
                "patient_name": final_state.get('patient_name', 'N/A'),
                "medication_name": final_state.get('medication_name', 'N/A'),
                "final_location": final_state.get('current_location', 'unknown')
            })
            
            # Log metrics
            task_success = 1 if final_state['task_status'] == 'delivered' else 0
            mlflow.log_metrics({
                "task_success": task_success,
                "execution_time_seconds": execution_time,
                "error_count": len(final_state.get('errors', [])),
                "workflow_steps": len(final_state.get('history', [])),
                "target_detected": int(final_state.get('target_detected', False)),
                "identity_verified": int(final_state.get('identity_verified', False))
            })
            
            # Log final status as tag
            mlflow.set_tag("final_status", final_state['task_status'])
            
            # Save final state as artifact
            import json
            artifact_path = "final_state.json"
            with open(artifact_path, "w") as f:
                json.dump(final_state, f, indent=2, ensure_ascii=False)
            mlflow.log_artifact(artifact_path)
            
            # Clean up temporary file
            if os.path.exists(artifact_path):
                os.remove(artifact_path)
            
            # Print summary
            print(f"\n{'#'*60}")
            print(f"# 任務執行摘要")
            print(f"{'#'*60}")
            print(f"\n最終狀態: {final_state['task_status']}")
            print(f"執行時間: {execution_time:.2f} 秒")
            print(f"\n執行歷程:")
            for entry in final_state.get('history', []):
                print(f"  {entry}")
            
            if final_state.get('errors'):
                print(f"\n錯誤記錄:")
                for error in final_state['errors']:
                    print(f"  ✗ {error}")
            
            if final_state['task_status'] == 'delivered':
                print(f"\n✅ 給藥任務完成！")
            else:
                print(f"\n❌ 給藥任務失敗")
            
            print(f"\n📊 MLflow Run ID: {mlflow.active_run().info.run_id}")
            print(f"\n{'#'*60}\n")
            
            return final_state


# --- CLI for Testing ---

if __name__ == "__main__":
    import sys
    
    # Example instructions
    examples = [
        "請將阿斯匹靈送給張小明",
        "Deliver Aspirin to John Smith",
        "請將普拿疼送給李美華",
        "請將維他命C送給王大同"
    ]
    
    if len(sys.argv) > 1:
        # Use command line argument
        instruction = " ".join(sys.argv[1:])
    else:
        # Use first example
        print("使用範例指令，您也可以用 --instruction 參數指定自訂指令\n")
        instruction = examples[0]
    
    # Create agent and execute
    agent = MedicationDeliveryAgent()
    result = agent.execute(instruction)
    
    # Print available examples
    print("\n可用的測試指令範例:")
    for i, example in enumerate(examples, 1):
        print(f"  {i}. {example}")
