"""
UASR — Universal Agentic Semantic Recovery
============================================
Self-healing data-pipeline framework for AURA.

Modules:
  drift_detector   — Latent Drift Detection via KL-Divergence
  recovery_loop    — Controller-Reflector-Actuator orchestration
  semantic_gateway — Batch Embedding & Reference Context Matrix
  metrics          — Universal Healing Coefficient (Hᵤ) tracking
  models           — Shared Pydantic / SQLAlchemy models
  service          — FastAPI microservice (port 8009)
"""
