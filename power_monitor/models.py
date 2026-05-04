from dataclasses import dataclass
from datetime import datetime
from typing import Optional

_MAX_AFFECTED = 100_000   # sanity ceiling on customer count from external APIs
_MAX_STR = 500            # max length for free-text fields from external APIs


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

    def __post_init__(self) -> None:
        # Clamp values that come directly from external APIs
        self.num_affected = max(0, min(int(self.num_affected or 0), _MAX_AFFECTED))
        if self.customer_message:
            self.customer_message = self.customer_message[:_MAX_STR]
        if self.municipality:
            self.municipality = self.municipality[:100]
