import json
from pathlib import Path
from typing import Dict, Optional

PERSONAS_PATH = Path(__file__).resolve().parent.parent / "personas" / "personas.json"


def _load_personas() -> Dict[str, dict]:
    if not PERSONAS_PATH.exists():
        return {}
    with PERSONAS_PATH.open("r", encoding="utf-8") as handle:
        personas = json.load(handle)
    return {persona["userId"]: persona for persona in personas}


_PERSONA_REGISTRY = _load_personas()


UNKNOWN_PERSONA = {
    "userId": "unknown",
    "userName": "Unknown User",
    "avatarUrl": "",
    "bio": "",
}


def get_persona(user_id: Optional[str]) -> dict:
    if not user_id:
        return UNKNOWN_PERSONA
    return _PERSONA_REGISTRY.get(user_id, {**UNKNOWN_PERSONA, "userId": user_id})


def personas() -> Dict[str, dict]:
    return dict(_PERSONA_REGISTRY)
