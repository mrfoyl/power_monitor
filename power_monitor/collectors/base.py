from abc import ABC, abstractmethod
from typing import List

from ..models import PowerOutage


class BaseCollector(ABC):
    name: str
    region: str

    @abstractmethod
    def fetch_outages(self) -> List[PowerOutage]:
        pass
