"""
Causal Discovery Microservice
==============================
Standalone FastAPI service that consumes anomaly alerts and returns
statistical root-cause attributions using DoWhy's graphical causal models
(gcm.attribute_anomalies). Falls back to ranked partial correlations when
DoWhy is unavailable so the API still responds in dev environments.
"""
