"""
State Manager
==============
Manages checkpoint persistence for streaming pipelines:
  - Periodic checkpoint creation (snapshot window states + source offsets)
  - Checkpoint storage (JSON files on disk, upgradeable to DB/Redis)
  - Recovery: restore from latest checkpoint on pipeline restart
  - Checkpoint rotation (keep last N checkpoints)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pipeline.streaming.models import (
    CheckpointData,
    StreamMetrics,
    WindowState,
)

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "checkpoints",
)


class StateManager:
    """
    Manages checkpoint persistence for streaming pipeline state.

    Checkpoints include:
      - All active window states (aggregation accumulators)
      - Source offsets (e.g. Kafka consumer position)
      - Current watermark position
      - Pipeline metrics snapshot
    """

    def __init__(
        self,
        pipeline_id: str,
        checkpoint_dir: Optional[str] = None,
        max_checkpoints: int = 5,
    ):
        self.pipeline_id = pipeline_id
        self.checkpoint_dir = checkpoint_dir or os.path.join(CHECKPOINT_DIR, pipeline_id)
        self.max_checkpoints = max_checkpoints
        self._last_checkpoint_time: float = 0.0

        # Ensure checkpoint directory exists
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    def create_checkpoint(
        self,
        watermark: float,
        window_states: List[WindowState],
        source_offsets: Dict[str, Any],
        metrics: Optional[StreamMetrics] = None,
    ) -> CheckpointData:
        """
        Create and persist a new checkpoint.

        Returns the CheckpointData object.
        """
        checkpoint = CheckpointData(
            pipeline_id=self.pipeline_id,
            watermark=watermark,
            window_states=window_states,
            source_offsets=source_offsets,
            metrics_snapshot=metrics,
        )

        # Write to disk atomically: write to .tmp then rename so a crash
        # mid-write never leaves a corrupt checkpoint file.
        filename = f"chk_{checkpoint.checkpoint_id}_{int(time.time())}.json"
        filepath = os.path.join(self.checkpoint_dir, filename)
        tmp_path = filepath + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(checkpoint.model_dump(), f, default=str)
        os.replace(tmp_path, filepath)  # atomic on POSIX; best-effort on Windows

        self._last_checkpoint_time = time.time()
        logger.info(
            f"[StateManager:{self.pipeline_id}] Checkpoint saved: {filename} "
            f"(watermark={watermark:.1f}, windows={len(window_states)}"
            f", offsets={source_offsets})"
        )

        # Rotate old checkpoints
        self._rotate_checkpoints()

        return checkpoint

    def load_latest_checkpoint(self) -> Optional[CheckpointData]:
        """
        Load the most recent checkpoint from disk.

        Returns None if no checkpoint exists.
        """
        checkpoints = self._list_checkpoint_files()
        if not checkpoints:
            logger.info(f"[StateManager:{self.pipeline_id}] No checkpoints found")
            return None

        latest = checkpoints[-1]  # sorted by modification time
        filepath = os.path.join(self.checkpoint_dir, latest)

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Reconstruct WindowState objects
            if "window_states" in data:
                data["window_states"] = [
                    WindowState(**ws) if isinstance(ws, dict) else ws
                    for ws in data["window_states"]
                ]
            if "metrics_snapshot" in data and isinstance(data["metrics_snapshot"], dict):
                data["metrics_snapshot"] = StreamMetrics(**data["metrics_snapshot"])

            checkpoint = CheckpointData(**data)
            logger.info(
                f"[StateManager:{self.pipeline_id}] Restored checkpoint: {latest} "
                f"(watermark={checkpoint.watermark:.1f}, windows={len(checkpoint.window_states)})"
            )
            return checkpoint

        except Exception as e:
            logger.error(f"[StateManager:{self.pipeline_id}] Failed to load checkpoint {latest}: {e}")
            return None

    def should_checkpoint(self, interval_seconds: float) -> bool:
        """Check if enough time has elapsed for another checkpoint."""
        return (time.time() - self._last_checkpoint_time) >= interval_seconds

    def clear_checkpoints(self) -> int:
        """Remove all checkpoints for this pipeline. Returns count deleted."""
        count = 0
        for fname in self._list_checkpoint_files():
            try:
                os.remove(os.path.join(self.checkpoint_dir, fname))
                count += 1
            except OSError:
                pass
        return count

    @property
    def last_checkpoint_time(self) -> float:
        return self._last_checkpoint_time

    # ── Internal ──────────────────────────────────────────────────

    def _list_checkpoint_files(self) -> List[str]:
        """List checkpoint files sorted by modification time (oldest first)."""
        if not os.path.exists(self.checkpoint_dir):
            return []
        files = [
            f for f in os.listdir(self.checkpoint_dir)
            if f.startswith("chk_") and f.endswith(".json")
        ]
        # Sort by file modification time
        files.sort(key=lambda f: os.path.getmtime(os.path.join(self.checkpoint_dir, f)))
        return files

    def _rotate_checkpoints(self) -> None:
        """Keep only the latest N checkpoints."""
        files = self._list_checkpoint_files()
        while len(files) > self.max_checkpoints:
            oldest = files.pop(0)
            try:
                os.remove(os.path.join(self.checkpoint_dir, oldest))
                logger.debug(f"[StateManager:{self.pipeline_id}] Rotated old checkpoint: {oldest}")
            except OSError:
                pass
