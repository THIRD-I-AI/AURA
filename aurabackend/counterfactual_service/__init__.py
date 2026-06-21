# DoWhy's refuters (e.g. add_unobserved_common_cause) call matplotlib, which
# opens a blocking GUI window on a machine with a display. Force the headless
# Agg backend before any matplotlib/dowhy import so the service, scripts, and
# tests never spawn a window. setdefault lets a dev override it explicitly.
import os

os.environ.setdefault("MPLBACKEND", "Agg")
