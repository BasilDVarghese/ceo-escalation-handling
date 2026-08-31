"""Router agent: picks the owning team (from the live SQL taxonomy) and drafts an action."""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import db
from agents.llm import get_llm
from config import CONFIG
from state import EscalationState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You route escalations to the correct internal team and recommend what that team \
should do about it. You will be given a list of teams with descriptions of what \
each one owns — choose exactly one team whose description best matches this \
escalation. Your team_name output MUST match one of the given team names exactly, \
character for character.

Then draft a specific, actionable recommendation (1-3 sentences) addressed to that \
team, referencing the summary you were given — not generic advice.
"""


class RoutingResult(BaseModel):
    team_name: str = Field(description="Must exactly match one of the provided team names.")
    recommended_action: str = Field(description="1-3 sentence actionable recommendation for the team.")


def router_node(state: EscalationState) -> dict:
    teams = db.get_team_taxonomy()
    team_list = "\n".join(f"- {t['name']}: {t['description']}" for t in teams)
    valid_names = {t["name"] for t in teams}

    llm = get_llm().with_structured_output(RoutingResult)
    result: RoutingResult = llm.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Teams:\n{team_list}\n\n"
                    f"Severity: {state.get('severity', '')}\n"
                    f"Summary: {state.get('summary', '')}"
                )
            ),
        ]
    )

    team_name = result.team_name
    if team_name not in valid_names:
        logger.warning(
            "Router returned unknown team %r (valid: %s) — falling back to %r",
            team_name,
            sorted(valid_names),
            CONFIG.fallback_team,
        )
        team_name = CONFIG.fallback_team if CONFIG.fallback_team in valid_names else next(iter(valid_names))

    owner_email = db.get_owner_email(team_name)

    db.update_escalation(
        state["escalation_id"],
        routed_team=team_name,
        recommended_action=result.recommended_action,
        owner_email=owner_email,
        status="pending_approval",
    )

    return {
        "routed_team": team_name,
        "recommended_action": result.recommended_action,
        "owner_email": owner_email or "",
        "approval_status": "pending",
    }
