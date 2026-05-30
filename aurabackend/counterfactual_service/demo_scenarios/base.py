"""Demo-scenario interface + registry.

A scenario is a deterministic, self-contained compliance audit fixture:
a synthetic dataset (with a planted, known ground-truth effect), the
CounterfactualQuery that audits it, and a plain-English narrative for
the PDF/UI. Scenarios are independent — shipping a subset still yields
a working demo.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

import pandas as pd

from ..schemas import CounterfactualQuery


class DemoScenario(ABC):
    id: str
    title: str
    vertical: str
    description: str
    instrument: Optional[str] = None

    @abstractmethod
    def build_dataset(self) -> pd.DataFrame: ...

    @abstractmethod
    def query(self) -> CounterfactualQuery: ...

    def narrative(self, artifact: dict) -> str:
        """Plain-English conclusion for the PDF/UI. Override per scenario."""
        return self.description


_REGISTRY: Dict[str, DemoScenario] = {}


def register(scenario: DemoScenario) -> DemoScenario:
    _REGISTRY[scenario.id] = scenario
    return scenario


def get_scenario(scenario_id: str) -> DemoScenario:
    if scenario_id not in _REGISTRY:
        raise KeyError(scenario_id)
    return _REGISTRY[scenario_id]


def list_scenarios() -> list[dict]:
    return [
        {"id": s.id, "title": s.title, "vertical": s.vertical, "description": s.description}
        for s in _REGISTRY.values()
    ]
