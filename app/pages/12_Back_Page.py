import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.data_access import coverage_summary
from config import DONATION_URL, GITHUB_REPO

st.set_page_config(page_title="The Back Page", page_icon="☕", layout="wide")
st.title("The Back Page")
st.caption("Why this site exists, and how to support it if you'd like to.")

coverage = coverage_summary()

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

st.divider()

st.subheader("What it actually is")
st.markdown(
    f"Every final box score from all {coverage['divisions']} divisions, "
    f"{coverage['first_year']}–{coverage['last_year']} — "
    f"**{coverage['games']:,} games** and **{coverage['players']:,} players** — scraped from "
    "the federation's own platform and turned into the kind of thing you'd find on "
    "Baseball-Reference or FanGraphs."
)
st.markdown(
    "The part I care most about is that the advanced stats are calibrated to *this* league "
    "rather than borrowed from MLB. A wRC+ of 130 here means 30% better than a British "
    "baseball hitter, not 30% better than a major leaguer — the league averages, the run "
    "environment and the runs-per-win conversion are all worked out from our own games, "
    "season by season. The Methodology page shows the workings, including the things the "
    "underlying data genuinely can't support. I'd rather tell you where the numbers run out "
    "than quietly make something up."
)
st.page_link("pages/10_Methodology.py", label="Read the methodology", icon="📖")

st.divider()

st.subheader("Support")
st.markdown(
    "**Please don't feel any obligation.** I want to be straight with you about this: "
    "nothing here is at risk. The hosting is free, the data is free, and there's no server "
    "bill quietly ticking away in the background. This isn't a fundraiser and nothing "
    "switches off if nobody ever clicks the link below. I'd be doing it anyway — I built it "
    "for myself and my own team long before I put it online."
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

st.divider()

st.subheader("Other ways to help — all of them free")
st.markdown(
    "Honestly, these are worth more to me than the coffee:\n\n"
    "- **Tell me when something looks wrong.** A stat that doesn't match your own memory of "
    "a game is the single most useful thing you can send me — several real bugs have been "
    "found exactly that way.\n"
    "- **Tell me what's missing.** If there's a view you keep wishing existed, say so.\n"
    "- **Show it to someone.** A teammate, a coach, someone who thinks their 2022 season was "
    "better than it was.\n"
    f"- **Or dig into the code** — it's all open source at "
    f"[{GITHUB_REPO}](https://github.com/{GITHUB_REPO})."
)
st.page_link("pages/11_Feedback.py", label="Report something / suggest a feature", icon="💬")

st.divider()
st.caption("Thanks for reading, and thanks for playing. See you at the field. — Nathan")
