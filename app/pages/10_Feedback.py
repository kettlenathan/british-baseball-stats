import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx
import streamlit as st

from config import GITHUB_REPO

st.set_page_config(page_title="Feedback", page_icon="💬", layout="wide")
st.title("Feedback / Bug Report")
st.caption(
    "Spotted a bug, a wrong-looking stat, or have a feature request? Submit it below — it "
    f"files an issue directly on the project's GitHub repo ({GITHUB_REPO})."
)

LABELS = {
    "Bug report": ["bug", "user-feedback"],
    "Feature request": ["enhancement", "user-feedback"],
    "General feedback": ["user-feedback"],
}

MAX_TITLE_LEN = 200
MAX_BODY_LEN = 5000


def _github_token() -> str | None:
    try:
        return st.secrets.get("GITHUB_TOKEN")
    except Exception:
        return None


token = _github_token()
if not token:
    st.info(
        "Feedback submission isn't configured yet — it needs a `GITHUB_TOKEN` secret with "
        "permission to open issues on this repo. Add it via the Community Cloud dashboard's "
        "Secrets box (or a local `.streamlit/secrets.toml` for testing)."
    )
else:
    kind = st.selectbox("Type", list(LABELS.keys()))
    title = st.text_input("Summary", max_chars=MAX_TITLE_LEN, placeholder="Short summary of the issue")
    description = st.text_area(
        "Details",
        max_chars=MAX_BODY_LEN,
        placeholder="What happened, what page you were on, what you expected instead...",
        height=200,
    )
    contact = st.text_input("Your email (optional, only if you want a reply)")

    if st.button("Submit", type="primary", disabled=not (title.strip() and description.strip())):
        body = description.strip()
        if contact.strip():
            body += f"\n\n---\nSubmitted by: {contact.strip()}"
        body += "\n\n*Filed automatically from the app's Feedback page.*"

        try:
            response = httpx.post(
                f"https://api.github.com/repos/{GITHUB_REPO}/issues",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "british-baseball-stats-feedback-form",
                },
                json={"title": title.strip(), "body": body, "labels": LABELS[kind]},
                timeout=10.0,
            )
            response.raise_for_status()
            issue_url = response.json().get("html_url")
            st.success("Thanks — your feedback has been submitted.")
            if issue_url:
                st.caption(f"Tracked at: {issue_url}")
        except httpx.HTTPError as exc:
            st.error(f"Couldn't submit feedback right now ({exc}). Please try again later.")
