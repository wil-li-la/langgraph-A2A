"""Mock data generators for medication delivery simulation."""

import random
import time
from typing import Dict, List, Optional


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
    
    # Navigation waypoints
    LOCATIONS = {
        "charging_dock": {"x": 0, "y": 0, "description": "充電站"},
        "pharmacy": {"x": 10, "y": 5, "description": "藥局"},
        "room_301": {"x": 20, "y": 10, "description": "301病房"},
        "room_302": {"x": 20, "y": 12, "description": "302病房"},
        "room_303": {"x": 20, "y": 14, "description": "303病房"},
        "room_304": {"x": 20, "y": 16, "description": "304病房"},
        "room_305": {"x": 20, "y": 18, "description": "305病房"}
    }
    
    @classmethod
    def get_patient(cls, name: str) -> Optional[Dict]:
        """Get patient information by name."""
        return cls.PATIENTS.get(name)
    
    @classmethod
    def get_medication(cls, name: str) -> Optional[Dict]:
        """Get medication information by name."""
        return cls.MEDICATIONS.get(name)
    
    @classmethod
    def get_room_location(cls, room: str) -> Optional[Dict]:
        """Get location coordinates for a room."""
        return cls.LOCATIONS.get(f"room_{room}")


class MockRobotActions:
    """Mock robot actions with simulated delays."""
    
    @staticmethod
    def navigate(from_loc: str, to_loc: str) -> Dict:
        """Simulate robot navigation."""
        # Calculate simulated travel time (2-5 seconds)
        travel_time = random.uniform(2.0, 5.0)
        time.sleep(min(travel_time, 0.5))  # Actual sleep capped for demo
        
        return {
            "success": True,
            "from": from_loc,
            "to": to_loc,
            "duration": round(travel_time, 1),
            "path_length": round(random.uniform(5.0, 15.0), 1)
        }
    
    @staticmethod
    def detect_medication(medication_name: str) -> Dict:
        """Simulate vision-based medication detection."""
        time.sleep(0.3)  # Simulated vision processing
        
        med_info = MockDatabase.get_medication(medication_name)
        if not med_info:
            return {
                "success": False,
                "detected": False,
                "confidence": 0.0,
                "message": f"未找到藥物: {medication_name}"
            }
        
        # Simulate 95% success rate
        success = random.random() > 0.05
        confidence = random.uniform(0.85, 0.99) if success else random.uniform(0.3, 0.6)
        
        return {
            "success": success,
            "detected": success,
            "confidence": round(confidence, 2),
            "location": med_info.get("location"),
            "description": med_info.get("description"),
            "message": f"藥物已定位: {medication_name}" if success else "視覺辨識失敗"
        }
    
    @staticmethod
    def pickup_medication() -> Dict:
        """Simulate robotic arm picking up medication."""
        time.sleep(0.4)  # Simulated manipulation time
        
        # Simulate 98% success rate
        success = random.random() > 0.02
        
        return {
            "success": success,
            "gripper_closed": success,
            "force": round(random.uniform(0.5, 1.5), 2),
            "message": "藥物已抓取" if success else "抓取失敗，請重試"
        }
    
    @staticmethod
    def verify_identity(patient_name: str) -> Dict:
        """Simulate face recognition for patient identity verification."""
        time.sleep(0.3)  # Simulated face recognition processing
        
        patient_info = MockDatabase.get_patient(patient_name)
        if not patient_info:
            return {
                "success": False,
                "verified": False,
                "confidence": 0.0,
                "message": f"病患資料庫中無此人: {patient_name}"
            }
        
        # Simulate 97% success rate
        success = random.random() > 0.03
        confidence = random.uniform(0.90, 0.99) if success else random.uniform(0.4, 0.7)
        
        return {
            "success": success,
            "verified": success,
            "confidence": round(confidence, 2),
            "face_id": patient_info.get("face_id"),
            "message": f"身份驗證成功: {patient_name}" if success else "人臉辨識失敗"
        }
    
    @staticmethod
    def handoff_medication() -> Dict:
        """Simulate handing medication to patient."""
        time.sleep(0.2)
        
        return {
            "success": True,
            "gripper_opened": True,
            "message": "藥物已遞交給病患"
        }
    
    @staticmethod
    def speak(message: str) -> Dict:
        """Simulate text-to-speech."""
        time.sleep(0.1)
        
        return {
            "success": True,
            "message": message,
            "duration": len(message) * 0.1
        }


class MockNLU:
    """Mock Natural Language Understanding for instruction parsing."""
    
    @staticmethod
    def parse_instruction(instruction: str) -> Dict:
        """Parse medication delivery instruction to extract entities."""
        time.sleep(0.2)  # Simulated LLM processing
        
        # Simple pattern matching for demo (in production, use actual LLM)
        instruction_lower = instruction.lower()
        
        # Extract patient name
        patient_name = None
        for name in MockDatabase.PATIENTS.keys():
            if name.lower() in instruction_lower or name in instruction:
                patient_name = name
                break
        
        # Extract medication name
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
            "message": "指令解析成功" if success else "無法解析指令，請提供病患姓名和藥物名稱"
        }
