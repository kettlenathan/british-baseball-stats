"""Shared helpers for telling a local run apart from the hosted deployment.

is_deployed() itself now lives in config.py, since db/storage.py needs the
same signal and db/ must not depend on app/ — re-exported here so existing
`from app.env import is_deployed` call sites in app/ don't need to change.
"""

from config import is_deployed

__all__ = ["is_deployed"]
