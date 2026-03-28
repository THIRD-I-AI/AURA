# Specialist Data Engineering Agents

from .ingestion_agent import IngestionAgent
from .schema_architect_agent import SchemaArchitectAgent
from .transform_agent import TransformAgent
from .quality_agent import QualityAgent
from .pipeline_agent import PipelineAgent
from .optimization_agent import OptimizationAgent
from .sql_generator_agent import SQLGeneratorAgent


def __getattr__(name):
    """Lazy import UASR agents to avoid circular imports."""
    if name == "DiagnosticReflectorAgent":
        from uasr.reflector_agent import DiagnosticReflectorAgent
        return DiagnosticReflectorAgent
    if name == "SynthesisActuatorAgent":
        from uasr.actuator_agent import SynthesisActuatorAgent
        return SynthesisActuatorAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "IngestionAgent",
    "SchemaArchitectAgent",
    "TransformAgent",
    "QualityAgent",
    "PipelineAgent",
    "OptimizationAgent",
    "SQLGeneratorAgent",
    # UASR
    "DiagnosticReflectorAgent",
    "SynthesisActuatorAgent",
]
