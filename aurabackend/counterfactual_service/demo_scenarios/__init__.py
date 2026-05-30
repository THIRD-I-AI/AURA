# Importing each scenario module runs its register() call.
from . import fair_lending  # noqa: E402,F401
from .base import DemoScenario, get_scenario, list_scenarios, register

__all__ = ["DemoScenario", "get_scenario", "list_scenarios", "register"]
