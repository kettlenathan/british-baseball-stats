import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st
from sqlalchemy import select

from db.engine import get_session
from db.models import ScrapeLog
from scraper.discovery import SENIOR_LEAGUE_CODES

st.set_page_config(page_title="Data Admin", page_icon="🛠️", layout="wide")
st.title("Data Admin")

st.subheader("Run scraper + recompute stats")
col1, col2 = st.columns(2)
leagues = col1.multiselect("Leagues", SENIOR_LEAGUE_CODES, default=["nbl"])
years = col2.text_input("Years (e.g. 2026 or 2024-2026)", value="2026")
force_refresh = st.checkbox("Force refresh (bypass cache, re-hit the site)")

if st.button("Run refresh", type="primary", disabled=not leagues):
    cmd = [
        sys.executable,
        "-m",
        "scripts.refresh_data",
        "--leagues",
        ",".join(leagues),
        "--years",
        years,
    ]
    if force_refresh:
        cmd.append("--force-refresh")

    with st.spinner("Running scraper + stats recompute — this can take a while for a full season ..."):
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent.parent))

    st.code(result.stdout + "\n" + result.stderr)
    if result.returncode == 0:
        st.success("Refresh complete.")
        st.cache_data.clear()
    else:
        st.error(f"Refresh failed (exit code {result.returncode}).")

st.divider()
st.subheader("Recent scrape activity")

session = get_session()
try:
    rows = session.execute(
        select(ScrapeLog).order_by(ScrapeLog.fetched_at.desc()).limit(100)
    ).scalars().all()
finally:
    session.close()

if not rows:
    st.info("No scrape activity recorded yet.")
else:
    df = pd.DataFrame(
        [
            {
                "fetched_at": r.fetched_at,
                "entity_type": r.entity_type,
                "source_id": r.source_id,
                "status": r.status,
                "error_message": r.error_message,
            }
            for r in rows
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)
