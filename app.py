"""Streamlit Human-in-the-Loop approval console for the churn-risk agent."""

import uuid

import pandas as pd
import streamlit as st

from graph import CONFIDENCE_THRESHOLD, HIGH_RISK_ACTIONS, MOCK_CUSTOMERS, build_graph
from models import read_audit_log

st.set_page_config(page_title="Churn Risk HITL Console", layout="wide")

# --- session state -----------------------------------------------------
if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "threads" not in st.session_state:
    st.session_state.threads = {}  # thread_id -> customer_id

graph = st.session_state.graph


def config_for(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def is_pending(thread_id: str) -> bool:
    snapshot = graph.get_state(config_for(thread_id))
    return bool(snapshot.next)


# --- sidebar: launch a new evaluation -----------------------------------
st.sidebar.header("New Evaluation")
customer_id = st.sidebar.selectbox(
    "Customer",
    options=list(MOCK_CUSTOMERS.keys()),
    format_func=lambda cid: f"{cid} - {MOCK_CUSTOMERS[cid]['name']}",
)
reviewer_id = st.sidebar.text_input("Reviewer ID", value="operator_01")

if st.sidebar.button("Run Evaluation", type="primary"):
    thread_id = f"{customer_id}-{uuid.uuid4().hex[:6]}"
    graph.invoke({"customer_id": customer_id}, config_for(thread_id))
    st.session_state.threads[thread_id] = customer_id
    st.rerun()

st.sidebar.divider()
st.sidebar.caption(
    f"Auto-execute threshold: confidence >= {CONFIDENCE_THRESHOLD}\n\n"
    f"Hard policy (always human review): {', '.join(sorted(HIGH_RISK_ACTIONS))}"
)

st.title("Churn Risk Agent - Human-in-the-Loop Console")

pending_ids = [t for t in st.session_state.threads if is_pending(t)]
done_ids = [t for t in st.session_state.threads if t not in pending_ids]

# --- pending approvals ----------------------------------------------------
st.subheader(f"Pending Approvals ({len(pending_ids)})")

if not pending_ids:
    st.info("No actions waiting for review. Run an evaluation from the sidebar.")

for thread_id in pending_ids:
    snapshot = graph.get_state(config_for(thread_id))
    s = snapshot.values
    with st.container(border=True):
        col1, col2 = st.columns([3, 1])
        with col1:
            st.markdown(f"**Customer ID:** {s['customer_id']} ({s.get('customer_name', '')})")
            st.markdown(f"**Proposed Action:** `{s['proposed_action']}`")
            st.markdown(f"**Confidence:** {s['confidence_score']:.2f}")
            st.markdown(f"**Reasoning:** {s['reasoning']}")
        with col2:
            st.metric("TOI (VND)", f"{s.get('toi', 0):,.0f}")
            st.metric("Churn Probability", f"{s.get('churn_probability', 0):.0%}")
            if s["proposed_action"] in HIGH_RISK_ACTIONS:
                st.warning("Hard policy: high-risk action")
            elif s["confidence_score"] < CONFIDENCE_THRESHOLD:
                st.warning("Escalated: confidence below threshold")

        approve_col, reject_col, edit_col = st.columns(3)

        if approve_col.button("Approve", key=f"approve-{thread_id}", type="primary"):
            graph.update_state(
                config_for(thread_id),
                {"human_decision": "approve", "reviewer_id": reviewer_id},
            )
            graph.invoke(None, config_for(thread_id))
            st.rerun()

        if reject_col.button("Reject", key=f"reject-{thread_id}"):
            graph.update_state(
                config_for(thread_id),
                {"human_decision": "reject", "reviewer_id": reviewer_id},
            )
            graph.invoke(None, config_for(thread_id))
            st.rerun()

        with edit_col.popover("Edit"):
            edited_action = st.text_area(
                "Edited action",
                value=s["proposed_action"],
                key=f"edit-text-{thread_id}",
            )
            if st.button("Submit Edit", key=f"edit-submit-{thread_id}"):
                graph.update_state(
                    config_for(thread_id),
                    {
                        "human_decision": "edit",
                        "edited_action": edited_action,
                        "reviewer_id": reviewer_id,
                    },
                )
                graph.invoke(None, config_for(thread_id))
                st.rerun()

# --- completed actions -----------------------------------------------------
st.subheader(f"Completed Actions ({len(done_ids)})")

if done_ids:
    rows = []
    for thread_id in done_ids:
        s = graph.get_state(config_for(thread_id)).values
        rows.append(
            {
                "thread_id": thread_id,
                "customer_id": s.get("customer_id"),
                "proposed_action": s.get("proposed_action"),
                "confidence": s.get("confidence_score"),
                "human_decision": s.get("human_decision"),
                "final_status": s.get("final_status"),
                "executed_action": s.get("executed_action"),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

# --- audit trail -------------------------------------------------------
st.subheader("Audit Log")
entries = read_audit_log()
if entries:
    df = pd.DataFrame(entries).sort_values("timestamp", ascending=False)
    st.dataframe(df, width="stretch", hide_index=True)
else:
    st.caption("No audit entries yet.")
