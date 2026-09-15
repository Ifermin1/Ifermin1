from datetime import datetime
from typing import Optional

from pydantic import Field

from tradepilot.domain import DomainModel


class AuditEvent(DomainModel):
    id: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    event_type: str
    source_account: Optional[str] = None
    target_account: Optional[str] = None
    message: str
    details: Optional[dict] = None
