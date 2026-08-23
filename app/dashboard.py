#!/usr/bin/env python3
"""
dashboard.py
------------
NetSage AI — Phase 4: Streamlit dashboard.

Launch from the project root with:
    streamlit run app/dashboard.py

This file is UI only. All data loading and statistics computation lives
in app/dashboard_data.py, which has no Streamlit dependency and is
independently unit-tested (tests/test_dashboard_data.py). That separation
is what makes the numbers on this dashboard trustworthy: nothing here is
hardcoded, and dashboard_data.py's tests confirm the computations are
correct against the real stored CSVs.

Product/safety rules this dashboard follows throughout:
  - AI diagnoses are always labeled "AI-assisted diagnosis" -- never
    presented as a final or guaranteed-correct answer.
  - Confidence is shown as a number with an explicit note that it is not
    a guarantee of correctness.
  - Verification results always show the after-evidence that backs them
    up; a case with no verification is shown as unverified, never as
    silently "fine."
  - NetSage AI is stated plainly, in multiple places, to never
    automatically modify Cisco configuration.
  - The 5 Responsible AI examples are always labeled "Evaluation/Demo"
    and are never presented as real API failures.
"""

import os
import sys

import streamlit as st
import plotly.express as px

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import dashboard_data as dd  # noqa: E402
import review  # noqa: E402
import verify  # noqa: E402

st.set_page_config(page_title="NetSage AI Dashboard", layout="wide")


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _no_data(message: str) -> None:
    st.info(f"No data available yet. {message}")


def _safe_json_list(value: str) -> list[str]:
    """observed_evidence / fix_steps / evidence_used / checks_evaluated
    are stored as JSON strings in the CSVs. Parse defensively -- a
    malformed cell should show as one raw line, not crash the page."""
    import json
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [value]


# ---------------------------------------------------------------------------
# Section 1: Overview
# ---------------------------------------------------------------------------

def render_overview() -> None:
    st.header("Overview")
    st.caption(
        "All numbers below are computed live from the project's stored CSV "
        "files -- nothing on this page is hardcoded."
    )

    stats = dd.compute_overview_stats()

    row1 = st.columns(4)
    row1[0].metric("Total troubleshooting cases", stats["total_cases"])
    row1[1].metric("Cases with an AI diagnosis", stats["ai_diagnoses_count"])
    row1[2].metric("Accepted reviews", stats["accepted_count"])
    row1[3].metric("Edited reviews", stats["edited_count"])

    row2 = st.columns(4)
    row2[0].metric("Rejected reviews", stats["rejected_count"])
    row2[1].metric("Resolved verifications", stats["resolved_count"])
    row2[2].metric("Not Resolved verifications", stats["not_resolved_count"])
    row2[3].metric("Inconclusive verifications", stats["inconclusive_count"])

    if stats["ai_diagnoses_count"] == 0:
        _no_data("No AI diagnoses have been run yet (see src/diagnosis.py).")

    agreement = dd.compute_ai_human_agreement()
    if agreement:
        st.metric(
            "AI/human agreement rate",
            f"{agreement['agreement_rate']:.0%}",
            help=(
                f"{agreement['accepted']} of {agreement['total_reviewed']} reviewed "
                f"diagnoses were Accepted as-is by a human reviewer. This measures "
                f"agreement on THESE {stats['total_cases']} lab cases, not general "
                f"AI accuracy."
            ),
        )

    st.divider()
    chart_cols = st.columns(2)

    with chart_cols[0]:
        st.subheader("Cases by category")
        if stats["category_counts"]:
            fig = px.bar(
                x=list(stats["category_counts"].keys()),
                y=list(stats["category_counts"].values()),
                labels={"x": "Category", "y": "Cases"},
            )
            st.plotly_chart(fig, use_container_width=True)

    with chart_cols[1]:
        st.subheader("Cases by severity")
        if stats["severity_counts"]:
            fig = px.pie(
                names=list(stats["severity_counts"].keys()),
                values=list(stats["severity_counts"].values()),
            )
            st.plotly_chart(fig, use_container_width=True)

    chart_cols2 = st.columns(2)
    with chart_cols2[0]:
        st.subheader("Human review decisions")
        if stats["review_status_counts"]:
            fig = px.bar(
                x=list(stats["review_status_counts"].keys()),
                y=list(stats["review_status_counts"].values()),
                labels={"x": "Decision", "y": "Count"},
                color=list(stats["review_status_counts"].keys()),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            _no_data("No reviews recorded yet (see src/review.py).")

    with chart_cols2[1]:
        st.subheader("Verification outcomes")
        if stats["verification_status_counts"]:
            fig = px.bar(
                x=list(stats["verification_status_counts"].keys()),
                y=list(stats["verification_status_counts"].values()),
                labels={"x": "Outcome", "y": "Count"},
                color=list(stats["verification_status_counts"].keys()),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            _no_data("No post-fix verifications recorded yet (see src/verify.py).")


# ---------------------------------------------------------------------------
# Section 2 & 3: Case Explorer (includes the AI Diagnosis View)
# ---------------------------------------------------------------------------

def render_case_explorer() -> None:
    st.header("Case Explorer")

    cases = dd.load_cases()
    if not cases:
        _no_data("cases.csv could not be found.")
        return

    case_ids = [c["case_id"] for c in cases]
    case_id = st.selectbox("Select a case", case_ids)
    bundle = dd.get_case_bundle(case_id)
    case = bundle["case"]

    st.subheader(f"{case['case_id']} — {case['category']}")
    meta_cols = st.columns(3)
    meta_cols[0].metric("Category", case["category"])
    meta_cols[1].metric("Severity", case["severity"])
    meta_cols[2].metric("OSI layer (expected)", case["osi_layer"])

    st.markdown(f"**Symptom:** {case['symptom']}")
    st.markdown(f"**Topology:** {case['topology_note']}")
    with st.expander("Cisco show-command evidence", expanded=True):
        st.code(case["show_output"], language="text")
    with st.expander("Expected fault (case ground truth)"):
        st.write(case["expected_fault"])

    st.divider()

    # --- AI Diagnosis View ---
    st.subheader("AI Diagnosis")
    st.caption("AI-assisted diagnosis. Human review required — this is a recommendation, not a final answer.")
    ai = bundle["ai_diagnosis"]
    if ai is None:
        _no_data(f"No AI diagnosis has been run for {case_id} yet (run: python diagnosis.py --case-id {case_id}).")
    else:
        ai_cols = st.columns(3)
        ai_cols[0].metric("Confidence", f"{ai.get('confidence', '?')}/100")
        ai_cols[1].metric("OSI layer (AI)", ai.get("osi_layer", ""))
        ai_cols[2].metric("Evidence grounded", ai.get("evidence_grounded", ""))
        st.caption("Confidence reflects how strongly the evidence supports this conclusion — it is not a guarantee of correctness.")

        st.markdown(f"**Root cause:** {ai.get('root_cause', '')}")

        ev_col, inf_col = st.columns(2)
        with ev_col:
            st.markdown("**Observed Evidence** *(facts extracted from Cisco output)*")
            for item in _safe_json_list(ai.get("observed_evidence", "")):
                st.markdown(f"- {item}")
        with inf_col:
            st.markdown("**Inference** *(AI reasoning based on that evidence)*")
            st.write(ai.get("inference", ""))

        st.markdown(f"**Next Command:** `{ai.get('next_command', '') or '(none)'}`")
        st.markdown("**Fix Steps** *(proposed remediation — a human must apply this)*")
        for i, step in enumerate(_safe_json_list(ai.get("fix_steps", "")), start=1):
            st.markdown(f"{i}. {step}")

        if ai.get("evidence_grounded") == "False":
            st.warning("Some observed evidence could not be matched to the case's real show-command output — treat this diagnosis with extra scrutiny.")

    st.divider()

    # --- Human Review ---
    st.subheader("Human Review")
    rv = bundle["review"]
    if rv is None:
        _no_data(f"No human review has been recorded for {case_id} yet (run: python review.py --case-id {case_id}).")
    else:
        decision = rv.get("review_status", "")
        badge = {"Accepted": "🟢", "Edited": "🟡", "Rejected": "🔴"}.get(decision, "")
        st.markdown(f"**Decision:** {badge} {decision}")
        st.markdown(f"**Final diagnosis:** {rv.get('final_root_cause', '')}")
        if rv.get("reviewer_notes"):
            st.markdown(f"**Reviewer notes:** {rv.get('reviewer_notes', '')}")
        evidence_used = _safe_json_list(rv.get("evidence_used", ""))
        if evidence_used:
            st.markdown("**Evidence used:**")
            for item in evidence_used:
                st.markdown(f"- {item}")

    st.divider()

    # --- Verification ---
    st.subheader("Verification")
    vr = bundle["verification"]
    if vr is None:
        _no_data(f"No post-fix verification has been recorded for {case_id} yet.")
    else:
        status = vr.get("verification_status", "")
        badge = {"Resolved": "✅", "Not Resolved": "❌", "Inconclusive": "❔"}.get(status, "")
        st.markdown(f"**Result:** {badge} {status}")
        st.caption("Verification requires real post-fix evidence — a fix is never assumed successful just because it was recommended.")
        with st.expander("Before evidence"):
            st.code(vr.get("before_evidence", ""), language="text")
        with st.expander("After evidence"):
            st.code(vr.get("after_evidence", ""), language="text")
        st.markdown(f"**Reason:** {vr.get('verification_reason', '')}")


# ---------------------------------------------------------------------------
# Section 4: Human-in-the-Loop
# ---------------------------------------------------------------------------

def render_human_in_the_loop() -> None:
    st.header("Human-in-the-Loop")
    st.caption("AI recommendation → human review → final human decision. Nothing is finalized without this step.")

    reviews = dd.load_reviews()
    if not reviews:
        _no_data("No reviews have been recorded yet (see src/review.py).")
        return

    valid_reviews = [r for r in reviews if r.get("review_status") in review.VALID_STATUSES]
    counts = {
        status: sum(1 for r in valid_reviews if r["review_status"] == status)
        for status in review.VALID_STATUSES
    }
    cols = st.columns(3)
    cols[0].metric("🟢 Accepted", counts["Accepted"])
    cols[1].metric("🟡 Edited", counts["Edited"])
    cols[2].metric("🔴 Rejected", counts["Rejected"])

    st.subheader("Corrected cases (Edited or Rejected)")
    corrected = [r for r in valid_reviews if r["review_status"] in ("Edited", "Rejected")]
    if not corrected:
        st.info("No reviews have required a correction yet.")
    else:
        st.dataframe(
            [
                {
                    "Case": r["case_id"],
                    "Decision": r["review_status"],
                    "AI root cause": r.get("ai_root_cause", ""),
                    "Final (human) root cause": r.get("final_root_cause", ""),
                    "Reviewer notes": r.get("reviewer_notes", ""),
                }
                for r in corrected
            ],
            use_container_width=True,
        )

    st.subheader("All review records")
    st.dataframe(
        [
            {
                "Case": r["case_id"], "Decision": r.get("review_status", ""),
                "Final root cause": r.get("final_root_cause", ""),
                "Timestamp": r.get("timestamp", ""),
            }
            for r in reviews
        ],
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Section 5: Responsible AI
# ---------------------------------------------------------------------------

def render_responsible_ai() -> None:
    st.header("Responsible AI Log")
    st.warning(
        "The examples below are labeled by `source_type`. **Evaluation/Demo** "
        "examples are hand-authored illustrations built on real case evidence "
        "to demonstrate what a correction looks like — they are **not** real "
        "API failures. Only records labeled **Real AI Run** came from an "
        "actual model call."
    )

    records = dd.load_responsible_ai()
    if not records:
        _no_data("No Responsible AI records exist yet (run: python responsible_ai.py --seed-demo).")
        return

    for r in records:
        source = r.get("source_type", "")
        source_badge = "🧪 Evaluation/Demo" if source == "Evaluation/Demo" else "📡 Real AI Run"
        with st.expander(f"{r['case_id']} — {source_badge} — {r.get('human_decision', '')}"):
            st.markdown(f"**Source type:** {source_badge}")
            st.markdown(f"**AI diagnosis:** {r.get('ai_root_cause', '')}")
            st.markdown(f"**Human correction:** {r.get('human_corrected_root_cause', '')}")
            st.markdown(f"**Decision:** {r.get('human_decision', '')}")
            st.markdown(f"**Why the AI was wrong/incomplete:** {r.get('correction_reason', '')}")
            st.markdown("**Supporting evidence (from the real case):**")
            st.code(r.get("supporting_evidence", ""), language="text")


# ---------------------------------------------------------------------------
# Section 6: Verification
# ---------------------------------------------------------------------------

def render_verification() -> None:
    st.header("Verification")
    st.info(
        "NetSage AI does **not** automatically modify Cisco configuration. "
        "A human applies the fix externally, then supplies new post-fix "
        "evidence, which is what verification actually checks."
    )

    verifications = dd.load_verifications()
    if not verifications:
        _no_data("No verifications have been recorded yet (see src/verify.py).")
        return

    valid = [v for v in verifications if v.get("verification_status") in verify.VALID_STATUSES]
    counts = {
        status: sum(1 for v in valid if v["verification_status"] == status)
        for status in verify.VALID_STATUSES
    }
    cols = st.columns(3)
    cols[0].metric("✅ Resolved", counts["Resolved"])
    cols[1].metric("❌ Not Resolved", counts["Not Resolved"])
    cols[2].metric("❔ Inconclusive", counts["Inconclusive"])

    st.subheader("All verification records")
    case_id = st.selectbox(
        "Filter by case (optional)",
        ["(all)"] + sorted({v["case_id"] for v in verifications}),
        key="verification_case_filter",
    )
    shown = verifications if case_id == "(all)" else [v for v in verifications if v["case_id"] == case_id]

    for v in shown:
        status = v.get("verification_status", "")
        badge = {"Resolved": "✅", "Not Resolved": "❌", "Inconclusive": "❔"}.get(status, "")
        with st.expander(f"{v['case_id']} — {badge} {status} — {v.get('timestamp', '')}"):
            st.markdown(f"**Final diagnosis:** {v.get('final_diagnosis', '')}")
            st.markdown("**Before evidence:**")
            st.code(v.get("before_evidence", ""), language="text")
            st.markdown("**Fix applied externally by a human** *(NetSage AI never applies fixes automatically)*")
            st.markdown("**After evidence:**")
            st.code(v.get("after_evidence", ""), language="text")
            st.markdown(f"**Verification result:** {badge} {status}")
            st.markdown(f"**Reason:** {v.get('verification_reason', '')}")


# ---------------------------------------------------------------------------
# Section 7: Pipeline / Architecture
# ---------------------------------------------------------------------------

def render_pipeline() -> None:
    st.header("Pipeline / Architecture")
    st.caption("How a case moves through NetSage AI, start to finish.")

    steps = [
        ("1", "Network Problem", "A junior engineer hits a symptom in the Packet Tracer lab."),
        ("2", "Evidence", "Symptom + topology note + real Cisco show-command output are collected."),
        ("3", "Deterministic Rule Checker", "checker.py runs 6 fixed rules (duplicate IP, wrong mask, gateway mismatch, interface down, missing VLAN, missing route) against the evidence — fast, auditable, no AI involved."),
        ("4", "AI Diagnosis", "diagnosis.py sends the evidence + rule-checker findings to the Anthropic API and gets back a structured, evidence-grounded diagnosis. Always labeled AI-assisted — never final."),
        ("5", "Human Review", "review.py: a human marks the diagnosis Accepted / Edited / Rejected. Nothing is treated as correct without this step."),
        ("6", "Human Applies Fix", "The human applies the agreed fix directly in the lab. NetSage AI never touches device configuration itself."),
        ("7", "Post-Fix Evidence", "The human captures new show-command output after applying the fix."),
        ("8", "Verification", "verify.py compares before/after evidence and returns Resolved / Not Resolved / Inconclusive — never assumed, always evidence-based."),
    ]

    for num, title, desc in steps:
        cols = st.columns([1, 4])
        with cols[0]:
            st.markdown(f"### {num}")
        with cols[1]:
            st.markdown(f"**{title}**")
            st.caption(desc)
        if num != steps[-1][0]:
            st.markdown("<div style='text-align:center; color:#888;'>↓</div>", unsafe_allow_html=True)

    st.divider()
    st.error(
        "Safety rule, stated plainly: **NetSage AI recommends. It never "
        "automatically changes Cisco configuration.** Every fix is applied "
        "by a human, and every claim of resolution requires real post-fix "
        "evidence."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("NetSage AI — Network Troubleshooting Dashboard")
    st.caption(
        "AI-assisted diagnosis for Cisco Packet Tracer labs. Human review "
        "required. NetSage AI never automatically modifies Cisco configuration."
    )

    tab_names = [
        "Overview", "Case Explorer", "Human-in-the-Loop",
        "Responsible AI", "Verification", "Pipeline",
    ]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_overview()
    with tabs[1]:
        render_case_explorer()
    with tabs[2]:
        render_human_in_the_loop()
    with tabs[3]:
        render_responsible_ai()
    with tabs[4]:
        render_verification()
    with tabs[5]:
        render_pipeline()


main()
