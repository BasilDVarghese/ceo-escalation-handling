"""CLI entrypoint.

    python main.py run       — start the Gmail polling loop (never blocks on stdin)
    python main.py review    — interactively approve/reject pending escalations

Don't run `run`/`review` at the same time as `uvicorn api:app` against the same
CHECKPOINT_DB_PATH — see pipeline.py / README's Concurrency note. When the API is in use,
prefer it (POST /escalations, POST /approve) over these CLI commands.
"""

from __future__ import annotations

import argparse
import logging
import time

import db
import gmail_client
import pipeline
from config import CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


def cmd_run(_args: argparse.Namespace) -> None:
    db.init_db()
    service = gmail_client.get_gmail_service()
    logger.info(
        "Polling Gmail every %s minute(s) with query %r",
        CONFIG.gmail_poll_interval_minutes,
        CONFIG.gmail_query,
    )

    while True:
        try:
            pipeline.poll_once(service)
        except Exception:
            logger.exception("Poll cycle failed; will retry next cycle")
        time.sleep(CONFIG.gmail_poll_interval_minutes * 60)


def cmd_review(_args: argparse.Namespace) -> None:
    pending = db.get_pending_approvals()
    if not pending:
        print("No pending approvals.")
        return

    valid_teams = {t["name"] for t in db.get_team_taxonomy()}

    for escalation in pending:
        print("\n" + "=" * 72)
        print(f"Escalation #{escalation['escalation_id']}")
        print(f"From:      {escalation.get('sender')}")
        print(f"Subject:   {escalation.get('subject')}")
        print(f"Severity:  {escalation.get('severity')}")
        print(f"Summary:   {escalation.get('summary')}")
        print(f"Team:      {escalation.get('routed_team')}  ({escalation.get('owner_email')})")
        print(f"Action:    {escalation.get('recommended_action')}")
        print("=" * 72)

        choice = input("[a]pprove / [r]eject / [e]dit / [s]kip / [q]uit: ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            continue

        final_team = escalation.get("routed_team")
        final_action = escalation.get("recommended_action")

        if choice == "e":
            team_input = input(f"Team [{escalation.get('routed_team')}]: ").strip()
            if team_input:
                if team_input not in valid_teams:
                    print(f"Unknown team {team_input!r}; keeping {escalation.get('routed_team')!r}.")
                else:
                    final_team = team_input
            action_input = input("Recommended action (blank to keep current): ").strip()
            if action_input:
                final_action = action_input
            decision = "approved"
        elif choice == "a":
            decision = "approved"
        elif choice == "r":
            decision = "rejected"
        else:
            print("Unrecognized choice, skipping.")
            continue

        notes = input("Notes (optional): ").strip()

        result = pipeline.resolve_approval(
            escalation["escalation_id"],
            {
                "decision": decision,
                "final_team": final_team,
                "final_action": final_action,
                "notes": notes,
            },
        )

        if decision == "approved":
            print(f"Approved and sent to {final_team}. Status: {result.get('status') if result else '?'}")
        else:
            print("Rejected and archived.")


def main() -> None:
    parser = argparse.ArgumentParser(description="CEO escalation handling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Start the Gmail polling loop").set_defaults(func=cmd_run)
    subparsers.add_parser("review", help="Review pending approvals").set_defaults(func=cmd_review)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down.")


if __name__ == "__main__":
    main()
