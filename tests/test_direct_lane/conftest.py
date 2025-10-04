"""
Pytest configuration for direct lane tests
"""

import sys
import os
from pathlib import Path

# Add parent directory to path so we can import app modules
backend_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(backend_dir))

# Set test environment variables
os.environ["ENABLE_DIRECT_LANE"] = "true"
os.environ["DIRECT_LANE_CONFIDENCE_THRESHOLD"] = "0.8"
