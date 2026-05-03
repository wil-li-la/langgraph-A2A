"""Mock data generators for medication delivery simulation."""

import json
import logging
import re
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class MockDatabase:
    """Mock database for patients and medications."""
    
    # Mock patient database
    PATIENTS = {
        "張小明": {
            "room": "301",
            "face_id": "face_001",
            "age": 65,
            "allergies": []
        },
        "李美華": {
            "room": "302",
            "face_id": "face_002",
            "age": 58,
            "allergies": ["penicillin"]
        },
        "王大同": {
            "room": "303",
            "face_id": "face_003",
            "age": 72,
            "allergies": []
        },
        "John Smith": {
            "room": "304",
            "face_id": "face_004",
            "age": 45,
            "allergies": []
        },
        "Mary Johnson": {
            "room": "305",
            "face_id": "face_005",
            "age": 53,
            "allergies": ["aspirin"]
        }
    }
    
    # Mock medication inventory
    MEDICATIONS = {
        "阿斯匹靈": {
            "english_name": "Aspirin",
            "location": "shelf_A1",
            "stock": 50,
            "description": "白色圓形藥片"
        },
        "普拿疼": {
            "english_name": "Paracetamol",
            "location": "shelf_A2",
            "stock": 30,
            "description": "白色橢圓形藥片"
        },
        "維他命C": {
            "english_name": "Vitamin C",
            "location": "shelf_B1",
            "stock": 100,
            "description": "橙色圓形藥片"
        },
        "Aspirin": {
            "chinese_name": "阿斯匹靈",
            "location": "shelf_A1",
            "stock": 50,
            "description": "White round tablet"
        },
        "Paracetamol": {
            "chinese_name": "普拿疼",
            "location": "shelf_A2",
            "stock": 30,
            "description": "White oval tablet"
        },
        "Vitamin C": {
            "chinese_name": "維他命C",
            "location": "shelf_B1",
            "stock": 100,
            "description": "Orange round tablet"
        }
    }
    
    @classmethod
    def get_patient(cls, name: str) -> Optional[Dict]:
        """Get patient information by name."""
        return cls.PATIENTS.get(name)


class MockNLU:
    """Natural Language Understanding for medication delivery instructions.

    Tries the configured LLM (via app.llm.get_llm) first. Falls back to
    keyword matching when LLM is disabled, unavailable, or produces an
    invalid/hallucinated response. Output contract is unchanged regardless
    of which path runs.
    """

    @staticmethod
    def parse_instruction(instruction: str) -> Dict:
        """Parse a medication delivery instruction. LLM first, regex fallback."""
        llm_result = MockNLU._parse_with_llm(instruction)
        if llm_result is not None:
            return llm_result
        return MockNLU._parse_with_keywords(instruction)

    # -- LLM path --------------------------------------------------------

    @staticmethod
    def _parse_with_llm(instruction: str) -> Optional[Dict]:
        try:
            from app.llm import get_llm
        except Exception:
            return None

        llm = get_llm()
        if llm is None:
            return None

        patients = list(MockDatabase.PATIENTS.keys())
        meds = list(MockDatabase.MEDICATIONS.keys())

        prompt = (
            "You extract two fields from a hospital medication-delivery instruction.\n"
            "Return ONLY a JSON object on a single line, with no prose, no markdown.\n"
            "Schema: {\"patient_name\": <string or null>, \"medication_name\": <string or null>}\n\n"
            f"Allowed patient_name values (use EXACTLY one or null): {patients}\n"
            f"Allowed medication_name values (use EXACTLY one or null): {meds}\n\n"
            "Rules:\n"
            "- If the instruction does not clearly name an allowed patient, return null for patient_name.\n"
            "- If the instruction does not clearly name an allowed medication, return null for medication_name.\n"
            "- Never invent a value. Never translate. Use the exact spelling from the allowed lists.\n\n"
            f"Instruction: {instruction}\n"
            "JSON:"
        )

        try:
            response = llm.invoke(prompt)
            text = getattr(response, "content", None) or str(response)
        except Exception as e:
            logger.warning("NLU LLM call failed (%s); falling back to keywords", e)
            return None

        parsed = MockNLU._extract_json(text)
        if parsed is None:
            logger.warning("NLU LLM returned non-JSON output; falling back to keywords")
            return None

        patient_name = parsed.get("patient_name")
        medication_name = parsed.get("medication_name")

        # Reject hallucinated values not in the known dictionaries.
        if patient_name is not None and patient_name not in MockDatabase.PATIENTS:
            patient_name = None
        if medication_name is not None and medication_name not in MockDatabase.MEDICATIONS:
            medication_name = None

        success = patient_name is not None and medication_name is not None
        return {
            "success": success,
            "patient_name": patient_name,
            "medication_name": medication_name,
            "confidence": 0.95 if success else 0.3,
            "original_instruction": instruction,
            "message": "指令解析成功" if success else "無法解析指令，請提供病患姓名和藥物名稱",
        }

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        """Pull a JSON object out of the LLM response, tolerating fences and prose."""
        if not text:
            return None
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{[^{}]*\}", stripped, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    # -- Keyword fallback (original regex behavior) ----------------------

    @staticmethod
    def _parse_with_keywords(instruction: str) -> Dict:
        time.sleep(0.2)  # Preserved: simulated processing latency from the demo path
        instruction_lower = instruction.lower()

        patient_name = None
        for name in MockDatabase.PATIENTS.keys():
            if name.lower() in instruction_lower or name in instruction:
                patient_name = name
                break

        medication_name = None
        for med in MockDatabase.MEDICATIONS.keys():
            if med.lower() in instruction_lower or med in instruction:
                medication_name = med
                break

        success = patient_name is not None and medication_name is not None
        return {
            "success": success,
            "patient_name": patient_name,
            "medication_name": medication_name,
            "confidence": 0.95 if success else 0.3,
            "original_instruction": instruction,
            "message": "指令解析成功" if success else "無法解析指令，請提供病患姓名和藥物名稱",
        }
