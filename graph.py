"""LangGraph workflow for churn-risk evaluation with Human-in-the-Loop approval."""

from datetime import datetime, timezone
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from models import AuditEntry, append_audit_entry

AGENT_ID = "churn-risk-agent"
CONFIDENCE_THRESHOLD = 0.85
HIGH_RISK_ACTIONS = {"increase_credit_limit"}

LOW_RISK_NODE = "execute_low_risk_action"
HIGH_RISK_NODE = "execute_high_risk_action"


class GraphState(TypedDict, total=False):
    customer_id: str
    customer_name: str
    toi: float
    churn_probability: float
    proposed_action: str
    confidence_score: float
    reasoning: str
    human_decision: str | None  # "approve" | "reject" | "edit" | None
    edited_action: str | None
    reviewer_id: str | None
    final_status: str | None
    executed_action: str | None


# Mock customer book: Total Operating Income (TOI, VND) + churn probability.
# Stands in for a real feature store / scoring pipeline.
MOCK_CUSTOMERS: dict[str, dict] = {
    "CUST001": {"name": "Nguyen Van A", "toi": 82_000_000, "churn_probability": 0.18},
    "CUST002": {"name": "Tran Thi B", "toi": 45_000_000, "churn_probability": 0.23},
    "CUST003": {"name": "Le Van C", "toi": 130_000_000, "churn_probability": 0.81},
    "CUST004": {"name": "Pham Thi D", "toi": 95_000_000, "churn_probability": 0.93},
    "CUST005": {"name": "Hoang Van E", "toi": 60_000_000, "churn_probability": 0.47},
}


def mock_llm_evaluate(customer: dict) -> dict:
    """Simulate an LLM agent scoring churn risk from TOI + churn probability.

    High churn risk -> propose a credit-limit increase (financial, high-risk).
    Otherwise -> propose a retention email (low-risk). Confidence is a mock
    self-reported score, on purpose: it is NOT trusted blindly by routing.
    """
    toi = customer["toi"]
    churn = customer["churn_probability"]

    if churn >= 0.7:
        action = "increase_credit_limit"
        confidence = round(min(0.99, 0.70 + churn * 0.30), 2)
        reasoning = (
            f"Customer has high churn probability ({churn:.0%}) with TOI of "
            f"{toi:,.0f} VND. Increasing the credit limit may improve retention."
        )
    else:
        action = "send_email"
        confidence = round(min(0.99, 1.05 - churn), 2)
        reasoning = (
            f"Customer has {churn:.0%} churn probability with TOI of {toi:,.0f} VND. "
            "No high-risk financial action is required; a retention email is "
            "sufficient."
        )
    return {
        "proposed_action": action,
        "confidence_score": confidence,
        "reasoning": reasoning,
    }


def evaluate_customer(state: GraphState) -> dict:
    """Agent reasoning node: evaluate the customer and propose an action."""
    customer_id = state["customer_id"]
    customer = MOCK_CUSTOMERS.get(
        customer_id,
        {
            "name": customer_id,
            "toi": state.get("toi", 0.0),
            "churn_probability": state.get("churn_probability", 0.0),
        },
    )

    result = mock_llm_evaluate(customer)

    return {
        "customer_name": customer["name"],
        "toi": customer["toi"],
        "churn_probability": customer["churn_probability"],
        "proposed_action": result["proposed_action"],
        "confidence_score": result["confidence_score"],
        "reasoning": result["reasoning"],
        "human_decision": None,
        "edited_action": None,
        "final_status": None,
        "executed_action": None,
    }


def route_action(state: GraphState) -> str:
    """Conditional edge: Policy Override > Auto-Execute > Escalate.

    Hard policy rules are checked BEFORE the confidence threshold, so a
    high-confidence score can never bypass mandatory human review for a
    high-risk action.
    """
    action = state["proposed_action"]
    confidence = state["confidence_score"]

    # Rule 1 - Policy Override: high-risk actions always need human review,
    # no matter how confident the agent claims to be.
    if action in HIGH_RISK_ACTIONS:
        return HIGH_RISK_NODE

    # Rule 2 - Auto-Execute: confident, low-risk actions run immediately.
    if confidence >= CONFIDENCE_THRESHOLD:
        return LOW_RISK_NODE

    # Rule 3 - Escalate: low-risk action but confidence too low, force review.
    return HIGH_RISK_NODE


def _log(action: str, confidence: float, reviewer_id: str, decision: str) -> None:
    append_audit_entry(
        AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=AGENT_ID,
            action=action,
            confidence=confidence,
            reviewer_id=reviewer_id,
            decision=decision,
        )
    )


def execute_low_risk_action(state: GraphState) -> dict:
    """Auto-executed path (Rule 2). No human involved, still fully audited."""
    action = state["proposed_action"]
    _log(
        action=action,
        confidence=state["confidence_score"],
        reviewer_id="auto-system",
        decision="auto_approve",
    )
    return {
        "final_status": "auto_executed",
        "executed_action": action,
        "human_decision": "auto_approve",
    }


def execute_high_risk_action(state: GraphState) -> dict:
    """Human-gated path. Runs only after the graph resumes past the interrupt."""
    decision = state.get("human_decision")
    reviewer_id = state.get("reviewer_id") or "unknown_reviewer"
    proposed_action = state["proposed_action"]

    if decision == "approve":
        executed_action = proposed_action
        final_status = "executed"
    elif decision == "edit":
        executed_action = state.get("edited_action") or proposed_action
        final_status = "executed_edited"
    elif decision == "reject":
        executed_action = None
        final_status = "aborted"
    else:
        # Interrupted but resumed with no decision recorded - do nothing.
        executed_action = None
        final_status = "pending"

    _log(
        action=proposed_action,
        confidence=state["confidence_score"],
        reviewer_id=reviewer_id,
        decision=decision or "pending",
    )

    return {"final_status": final_status, "executed_action": executed_action}


def build_graph():
    """Build and compile the churn-risk HITL graph with a persistent checkpointer."""
    builder = StateGraph(GraphState)

    builder.add_node("evaluate_customer", evaluate_customer)
    builder.add_node(LOW_RISK_NODE, execute_low_risk_action)
    builder.add_node(HIGH_RISK_NODE, execute_high_risk_action)

    builder.set_entry_point("evaluate_customer")
    builder.add_conditional_edges(
        "evaluate_customer",
        route_action,
        {LOW_RISK_NODE: LOW_RISK_NODE, HIGH_RISK_NODE: HIGH_RISK_NODE},
    )
    builder.add_edge(LOW_RISK_NODE, END)
    builder.add_edge(HIGH_RISK_NODE, END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory, interrupt_before=[HIGH_RISK_NODE])
