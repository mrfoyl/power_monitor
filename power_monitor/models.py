from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class PowerOutage:
    provider: str
    event_id: str
    status: str          # e.g. "Pågående", "Planlagt"
    outage_type: str     # e.g. "Utkobling", "Driftsforstyrrelse"
    municipality: str
    start_time: Optional[datetime]
    num_affected: int
    customer_message: Optional[str] = None
    estimated_restoration: Optional[datetime] = None
    grid_level: Optional[str] = None
