from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from inforp.schemas import RawItem, SourceProfile


@dataclass(frozen=True)
class CollectionContext:
    started_at: datetime
    report_kind: str


class Collector(Protocol):
    def collect(self, source: SourceProfile, context: CollectionContext) -> list[RawItem]:
        ...

