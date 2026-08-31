"""Triage agent: is this a genuine escalation, how severe, and what are the key facts?"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import db
from agents.llm import get_llm
from state import EscalationState

SYSTEM_PROMPT = """\
You are the triage classifier for a CEO's escalation inbox. Your job is to decide \
whether an incoming email is a genuine escalation that needs to be routed to an \
internal team, or noise (newsletters, FYIs, low-stakes internal chatter, spam).

A genuine escalation is something with real business impact: an angry or at-risk \
customer, a legal or compliance concern, a security or trust incident, a significant \
outage or bug, a stalled deal, an HR/personnel issue, or anything else that plausibly \
needs a team to act and the CEO to be aware of.

If it is a genuine escalation, extract the concrete facts (who is involved, what \
happened, the impact, any deadline or time pressure) as a short bullet list, and \
assign a severity conservatively — reserve "critical" for things that are actively \
harming the business right now.
"""


class TriageResult(BaseModel):
    is_genuine_escalation: bool = Field(
        description="True if this email is a real escalation that should be routed to a team."
    )
    severity: Literal["low", "medium", "high", "critical"] = Field(
        description="Severity if it is an escalation; use 'low' if not applicable."
    )
    urgency_notes: str = Field(description="One or two sentences on why this is/isn't urgent.")
    key_facts: list[str] = Field(
        default_factory=list,
        description="Concrete extracted facts as short bullet strings (who, what, impact, deadline).",
    )


def triage_node(state: EscalationState) -> dict:
    llm = get_llm().with_structured_output(TriageResult)
    result: TriageResult = llm.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"From: {state.get('sender', '')}\n"
                    f"Subject: {state.get('subject', '')}\n\n"
                    f"{state.get('raw_body', '')}"
                )
            ),
        ]
    )

    # Status only moves to a terminal state here if this isn't a real escalation;
    # otherwise it stays "pending_triage" until router_node sets "pending_approval"
    # right before the human approval gate.
    update_fields = dict(
        is_genuine_escalation=result.is_genuine_escalation,
        severity=result.severity,
        urgency_notes=result.urgency_notes,
        key_facts="\n".join(result.key_facts),
    )
    if not result.is_genuine_escalation:
        update_fields["status"] = "not_escalation"
    db.update_escalation(state["escalation_id"], **update_fields)

    return {
        "is_genuine_escalation": result.is_genuine_escalation,
        "severity": result.severity,
        "urgency_notes": result.urgency_notes,
        "key_facts": result.key_facts,
    }
