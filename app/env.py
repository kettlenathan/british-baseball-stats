"""Shared helpers for telling a local run apart from the hosted deployment."""

import streamlit as st


def is_deployed() -> bool:
    """True only when the Community Cloud dashboard's Secrets box sets
    IS_DEPLOYED = true. Never set locally, so local runs always see False."""
    try:
        return bool(st.secrets.get("IS_DEPLOYED", False))
    except Exception:
        return False
