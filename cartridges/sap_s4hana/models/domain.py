
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class SAPDomainModel(BaseModel):
    id: str
    name: Optional[str] = None
    last_modified: Optional[str] = None
    raw_data: Dict[str, Any] = Field(default_factory=dict)
