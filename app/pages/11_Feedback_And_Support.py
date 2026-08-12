import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx
import streamlit as st

from app.components.data_access import coverage_summary
from config import DONATION_URL, GITHUB_REPO

st.set_page_config(page_title="Feedback & Support", page_icon="💬", layout="wide")
st.title("Feedback & Support")
st.caption(
    "Tell me what's broken or missing — and, if you'd like, support the project. "
    "The first one is free and genuinely more useful."
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


st.divider()

st.subheader("Send feedback")
st.caption(
    "Spotted a bug, a wrong-looking stat, or have a feature request? Submit it below — it "
    f"files an issue directly on the project's GitHub repo ({GITHUB_REPO})."
)

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
        body += "\n\n*Filed automatically from the app's Feedback & Support page.*"

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

st.divider()

st.subheader("Why I built this")
st.markdown(
    "I play in this league. That's the whole reason this exists.\n\n"
    "Every winter I'd go looking for my own numbers from the season just gone, and every "
    "winter I'd find the same thing: the data was all *there* — the federation's platform "
    "records every game, every at-bat, every error — but it was locked in a box score at a "
    "time. You could see what happened last Sunday. You couldn't see whether you'd actually "
    "improved since 2023, or how your team's offence stacked up against the rest of the "
    "division, or who was quietly having the best season in the country."
)
st.markdown(
    "British baseball is small. There are no beat writers, no analytics departments, no one "
    "whose job it is to turn any of this into a story. Nobody was ever going to build this "
    "for us. But the games are real, the seasons are real, and the people playing them — on "
    "cold Sunday mornings, on fields we've usually chalked out ourselves — deserve a record "
    "that treats them seriously."
)
st.markdown("So I built one.")

coverage = coverage_summary()
st.markdown(
    f"It's now every final box score from all {coverage['divisions']} divisions, "
    f"{coverage['first_year']}–{coverage['last_year']} — "
    f"**{coverage['games']:,} games** and **{coverage['players']:,} players** — turned into "
    "the kind of thing you'd find on Baseball-Reference or FanGraphs. The part I care most "
    "about is that the advanced stats are calibrated to *this* league rather than borrowed "
    "from MLB: a wRC+ of 130 here means 30% better than a British baseball hitter, not 30% "
    "better than a major leaguer. The Methodology page shows the workings, including the "
    "things the underlying data genuinely can't support — I'd rather tell you where the "
    "numbers run out than quietly make something up."
)
st.page_link("pages/10_Methodology.py", label="Read the methodology", icon="📖")

st.divider()

st.subheader("Support")
st.markdown(
    "**Please don't feel any obligation.** I want to be straight with you about this: "
    "nothing here is at risk. The hosting is free, the data is free, and there's no server "
    "bill quietly ticking away in the background. This isn't a fundraiser and nothing "
    "switches off if nobody ever clicks the button below. I'd be doing it anyway — I built "
    "it for myself and my own team long before I put it online."
)
st.markdown(
    "What it does cost is evenings. Chasing down why a stat looked wrong, backfilling five "
    "years of history, working out how to follow a player across seasons when the source "
    "site gives them a new ID every year. If any of that has been useful to you — if you've "
    "used it to settle an argument in a dugout, or found your own name on a leaderboard and "
    "grinned at it — and you'd like to see more of it, there's a tip jar."
)
st.link_button("☕ Buy me a coffee", DONATION_URL, type="primary")
st.caption(
    "No ads, no sign-up, no paywall, and none of that changes whether or not anyone donates."
)

st.markdown(
    "**And the free ways to help are worth more to me than the coffee.** The form at the top "
    "of this page is the big one: a stat that doesn't match your own memory of a game is the "
    "single most useful thing you can send me, and several real bugs have been found exactly "
    "that way. Beyond that — tell me what's missing if there's a view you keep wishing "
    "existed, show the site to a teammate or a coach or someone who reckons their 2022 "
    "season was better than it was, or dig into the code itself at "
    f"[{GITHUB_REPO}](https://github.com/{GITHUB_REPO})."
)

st.divider()
st.caption("Thanks for reading, and thanks for playing. See you at the field. — Nathan")
