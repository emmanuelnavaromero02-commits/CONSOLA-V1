
from typing import Dict, Any

class ValidationService:
    @staticmethod
    def validate_config(env_vars: Dict[str, str]) -> Dict[str, Any]:
        missing = [k for k, v in env_vars.items() if not v]

        if missing:
            return {
                "valid": False,
                "errors": [f"Missing required environment variables: {', '.join(missing)}"]
            }

        return {"valid": True, "errors": []}

    @staticmethod
    def validate_mapping(record: Dict[str, Any], entity_type: str) -> bool:
        # Standard validation for models
        if not record:
            return False
        return True
