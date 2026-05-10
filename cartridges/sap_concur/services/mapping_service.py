
from typing import Dict, Any
from ..models.domain import SAPDomainModel

class MappingService:
    @staticmethod
    def map_to_internal(sap_record: Dict[str, Any], entity_type: str) -> Dict[str, Any]:
        # Normalize fields
        normalized = {
            "id": sap_record.get("Id") or sap_record.get("userId") or sap_record.get("ObjectID"),
            "name": sap_record.get("Name") or sap_record.get("firstName"),
            "last_modified": sap_record.get("lastModifiedDateTime") or sap_record.get("ChangedAt"),
            "raw_data": sap_record
        }
        return normalized
