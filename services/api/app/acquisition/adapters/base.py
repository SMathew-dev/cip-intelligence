from __future__ import annotations

from abc import ABC, abstractmethod

from app.acquisition.models import AcquisitionCandidate, AcquisitionSource


class ReadOnlyAdapter(ABC):
    """Base contract for plant adapters.

    There is deliberately no write/command method in this interface. Adapters
    can discover and read approved source data only.
    """

    capabilities = frozenset({"discover", "read"})

    def __init__(self, source: AcquisitionSource):
        if not source.read_only:
            raise ValueError("Adapter initialization refused: source is not read-only.")
        self.source = source

    @abstractmethod
    def discover(self) -> list[AcquisitionCandidate]:
        raise NotImplementedError

    @abstractmethod
    def read(self, candidate: AcquisitionCandidate) -> bytes:
        raise NotImplementedError
