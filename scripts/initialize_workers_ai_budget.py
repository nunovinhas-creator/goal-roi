"""Initialize and verify the sealed Workers AI budget using the Actions account secret."""

from datetime import datetime, timezone
import json
import os
import re
import sys

from scangoal.llm_budget import REF, BudgetUnavailable, GitBudget


def main():
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        print("The Cloudflare account secret is missing or invalid.", file=sys.stderr)
        return 1

    day = datetime.now(timezone.utc).date()
    try:
        sha, state = GitBudget(account).initialize(day)
    except BudgetUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({"ref": REF, "sha": sha, "schema": state["schema"], "day": state["day"],
                      "sealed": state["sealed"], "hold_count": len(state["holds"])}, sort_keys=True))
    if state["day"] != day.isoformat() or not state["sealed"]:
        print("The existing journal was preserved. A sealed state for the current UTC day is unverified.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
