# Importing each scenario module runs its register() call.
from . import (
    compas_recidivism,  # noqa: E402,F401
    fair_lending,  # noqa: E402,F401
    healthcare_prior_auth,  # noqa: E402,F401
    hiring_fairness,  # noqa: E402,F401
    insurance_underwriting,  # noqa: E402,F401
)
from .base import DemoScenario, get_scenario, list_scenarios, register

__all__ = ["DemoScenario", "get_scenario", "list_scenarios", "register"]
