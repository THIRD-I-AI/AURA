from .base import DemoScenario, get_scenario, list_scenarios, register

# Importing each scenario module runs its register() call.
from . import fair_lending  # noqa: E402,F401

__all__ = ["DemoScenario", "get_scenario", "list_scenarios", "register"]
