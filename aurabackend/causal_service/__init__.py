"""
Causal Discovery Microservice
==============================
Standalone FastAPI service that consumes anomaly alerts and returns
statistical root-cause attributions using DoWhy's graphical causal models
(gcm.attribute_anomalies). Falls back to ranked partial correlations when
DoWhy is unavailable so the API still responds in dev environments.
"""

# DoWhy/gcm call matplotlib, which opens a blocking GUI window on a machine
# with a display. Force the headless Agg backend before any matplotlib/dowhy
# import. setdefault lets a dev override it explicitly.
import os

os.environ.setdefault("MPLBACKEND", "Agg")
