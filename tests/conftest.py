"""Checkpoints are deserialized strictly in tests, so a state type missing from
``CHECKPOINT_TYPES`` fails here instead of warning in production."""

import os

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
