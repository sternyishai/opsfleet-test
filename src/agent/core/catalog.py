"""The message catalog: one plain-language message with a next step per ``MessageClass``
(HLD 06 § The message catalog).

This is the bundled wording. The active Persona's ``messages`` override it class by class, so
the catalog's tone follows the Persona (HLD 09). ``{ref}`` is replaced by the turn's trace ID,
the reference a User quotes to support.
"""

from agent.domain.persona import Persona
from agent.errors import MessageClass

BUNDLED: dict[MessageClass, str] = {
    MessageClass.DATA_SOURCE_DOWN: (
        "The sales data is unavailable right now. You can still open your saved reports, "
        "or ask again in a few minutes. (ref: {ref})"
    ),
    MessageClass.MODEL_BUSY: "I'm a bit overloaded. Please ask again in a minute. (ref: {ref})",
    MessageClass.SELF_CORRECTION_EXHAUSTED: (
        "I couldn't build a reliable query for that. Could you rephrase, for example with a "
        "specific period?"
    ),
    MessageClass.QUERY_TOO_LARGE: (
        "That question needs more data than one query may scan. Try a shorter period or a "
        "specific Brand."
    ),
    MessageClass.OUT_OF_SCOPE: (
        "That is outside the Brands you can analyze. Ask about one of your own Brands."
    ),
    MessageClass.QUOTA_REACHED: ("You've reached today's analysis limit. It resets at midnight."),
    MessageClass.UNAVAILABLE: (
        "I can't confirm who you are right now, so I can't answer. Please try again shortly. "
        "(ref: {ref})"
    ),
    MessageClass.UNCLASSIFIED: (
        "Something went wrong on my side. I've logged it (ref: {ref}). Try rephrasing or ask "
        "again shortly."
    ),
    MessageClass.OFF_TOPIC: (
        "I can only help with questions about our sales data, for example revenue, products, "
        "Customers or returns."
    ),
    MessageClass.PII_REQUEST: (
        "I can't show personal details, but I can show your top Customers by ID with their "
        "total spend, state and age group."
    ),
    MessageClass.INJECTION: (
        "I can't do that. I can answer questions about the sales data you have access to."
    ),
    MessageClass.NO_ROLE: (
        "You don't have access to the sales data yet. Ask your administrator to assign you a "
        "role or Brands."
    ),
    MessageClass.PERSONA_FORBIDDEN: "Only the CEO's office can change the tone of the reports.",
    MessageClass.PERSONA_GATE_FAILED: (
        "I kept the current tone: the new one did not pass the quality checks."
    ),
    MessageClass.PERSONA_NOTHING_TO_ROLL_BACK: (
        "There is no earlier version of the tone to go back to."
    ),
    MessageClass.NOTHING_MATCHED: (
        "None of your saved reports match that. Ask to list your reports to see what you have."
    ),
    MessageClass.PLAN_EXPIRED: (
        "That confirmation is no longer open, so nothing was deleted. Ask again to start over."
    ),
    MessageClass.REPORT_NOT_FOUND: (
        "I couldn't find that report. Ask to list your reports to see what you have."
    ),
    MessageClass.REPORT_OUT_OF_SCOPE: (
        "That report covers Brands you can no longer analyze, so I can only show its title."
    ),
}


def render(message_class: MessageClass, persona: Persona, *, ref: str) -> str:
    wording = persona.messages.get(message_class) or BUNDLED[message_class]
    return wording.replace("{ref}", ref)
