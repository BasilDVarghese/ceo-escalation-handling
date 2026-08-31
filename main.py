"""CLI entrypoint.

    python main.py run       — start the Gmail polling loop (never blocks on stdin)
    python main.py review    — interactively approve/reject pending escalations
"""

from __future__ import annotations

import argparse
import datetime
import logging
import time

from langgraph.types import Command

import db
import gmail_client
from config import CONFIG
from graph import compiled_graph, thread_id_for

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
            emails = gmail_client.fetch_new_escalation_emails(service)
        except Exception:
            logger.exception("Failed to fetch emails this cycle; will retry next cycle")
            emails = []

        for email in emails:
            try:
                _process_one_email(email)
            except Exception:
                logger.exception("Failed to process email %s", email.get("gmail_message_id"))

        time.sleep(CONFIG.gmail_poll_interval_minutes * 60)


def _process_one_email(email: dict) -> None:
    escalation_id = db.create_escalation(
        gmail_message_id=email["gmail_message_id"],
        gmail_thread_id=email.get("gmail_thread_id"),
        sender=email.get("sender"),
        subject=email.get("subject"),
        raw_body=email.get("raw_body"),
        received_at=datetime.datetime.now(datetime.timezone.utc),
        thread_checkpoint_id=thread_id_for(email["gmail_message_id"]),
    )

    initial_state = {
        "escalation_id": escalation_id,
        "gmail_message_id": email["gmail_message_id"],
        "gmail_thread_id": email.get("gmail_thread_id", ""),
        "sender": email.get("sender", ""),
        "subject": email.get("subject", ""),
        "raw_body": email.get("raw_body", ""),
        "received_at": email.get("received_at", ""),
    }
    config = {"configurable": {"thread_id": thread_id_for(email["gmail_message_id"])}}

    # Returns as soon as it hits the human_approval interrupt (or completes early,
    # e.g. if triage decided this wasn't a real escalation).
    compiled_graph.invoke(initial_state, config=config)
    logger.info("Processed escalation id=%s subject=%r", escalation_id, email.get("subject"))


def cmd_review(_args: argparse.Namespace) -> None:
    pending = db.get_pending_approvals()
    if not pending:
        print("No pending approvals.")
        return

    valid_teams = {t["name"] for t in db.get_team_taxonomy()}

    for escalation in pending:
        print("\n" + "=" * 72)
        print(f"Escalation #{escalation.id}")
        print(f"From:      {escalation.sender}")
        print(f"Subject:   {escalation.subject}")
        print(f"Severity:  {escalation.severity}")
        print(f"Summary:   {escalation.summary}")
        print(f"Team:      {escalation.routed_team}  ({escalation.owner_email})")
        print(f"Action:    {escalation.recommended_action}")
        print("=" * 72)

        choice = input("[a]pprove / [r]eject / [e]dit / [s]kip / [q]uit: ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            continue

        final_team = escalation.routed_team
        final_action = escalation.recommended_action

        if choice == "e":
            team_input = input(f"Team [{escalation.routed_team}]: ").strip()
            if team_input:
                if team_input not in valid_teams:
                    print(f"Unknown team {team_input!r}; keeping {escalation.routed_team!r}.")
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

        resume_payload = {
            "decision": decision,
            "final_team": final_team,
            "final_action": final_action,
            "notes": notes,
        }
        config = {"configurable": {"thread_id": thread_id_for(escalation.gmail_message_id)}}
        compiled_graph.invoke(Command(resume=resume_payload), config=config)

        if decision == "approved":
            print(f"Approved and sent to {final_team}.")
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
