"""Build a static copy of the dashboard (index.html + state.json) for GitHub Pages.

    python -m predbot.export_site --db predbot.db --out site
"""
import argparse
import json
import os
import sqlite3

from .dashboard import PAGE, state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="predbot.db")
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    # Plain connection on purpose: don't create any tables in the trade database.
    db = sqlite3.connect(a.db)
    db.row_factory = sqlite3.Row
    with open(os.path.join(a.out, "state.json"), "w") as f:
        json.dump(state(db), f)
    page = PAGE.replace("<script>", "<script>window.API_URL='state.json';", 1)
    page = page.replace("setInterval(load,10000)", "setInterval(load,60000)")
    with open(os.path.join(a.out, "index.html"), "w") as f:
        f.write(page)
    open(os.path.join(a.out, ".nojekyll"), "w").close()
    print(f"Dashboard written to {a.out}/")


if __name__ == "__main__":
    main()
