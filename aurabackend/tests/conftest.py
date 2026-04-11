"""
AURA Test Configuration
========================
Shared fixtures and path setup for all test modules.
"""

import os
import sys

# Add aurabackend to sys.path so tests can import modules directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
