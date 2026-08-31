"""Summarizer agent: a concise, context-rich brief for the receiving team."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import db
from agents.llm import get_llm
from state import EscalationState

SYSTEM_PROMPT = """\
You write concise incident/escalation briefs for internal teams at a company. \
Your reader is a busy team lead who needs to understand what happened and why it \
matters in a few seconds. Write 2-5 sentences. Be concrete and actionable — \
incorporate the severity and key facts you're given rather than re-deriving them \
from scratch, and don't pad with generic filler.
"""


class SummaryResult(BaseModel):
    summary: str = Field(description="2-5 sentence context-rich brief for the receiving team.")


def summarizer_node(state: EscalationState) -> dict:
    llm = get_llm().with_structured_output(SummaryResult)
    key_facts = "\n".join(f"- {fact}" for fact in state.get("key_facts", []))
    result: SummaryResult = llm.invoke(
        [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Severity: {state.get('severity', '')}\n"
                    f"Urgency notes: {state.get('urgency_notes', '')}\n"
                    f"Key facts:\n{key_facts}\n\n"
                    f"Original email:\n{state.get('raw_body', '')}"
                )
            ),
        ]
    )

    db.update_escalation(state["escalation_id"], summary=result.summary)

    return {"summary": result.summary}
