"""
PDF renderer for the auditor view of a CounterfactualArtifact.

One-pager-ish: headline, point estimate, CI, confidence, full
estimator + refutation tables, full challenge list, schema version,
dataset fingerprint, audit record hash, signature status. Designed to
print on a single A4 page for short artifacts but flows naturally
across pages when there are many challenges.

Built on reportlab (pure-Python, no system deps). If reportlab is not
installed this module logs a warning and ``render_pdf`` returns
``None`` so callers can serve a 503 with a useful error rather than
crashing the engine.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("aura.counterfactual.pdf")


# ── Optional dep ──────────────────────────────────────────────────────

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    _REPORTLAB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REPORTLAB_AVAILABLE = False


def pdf_available() -> bool:
    return _REPORTLAB_AVAILABLE


# ── Styles ────────────────────────────────────────────────────────────

def _styles() -> Dict[str, Any]:
    base = getSampleStyleSheet()
    return {
        "title":    ParagraphStyle("Title",    parent=base["Title"],   fontSize=18, spaceAfter=12),
        "subtitle": ParagraphStyle("Subtitle", parent=base["Heading2"], fontSize=12,
                                    spaceBefore=8, spaceAfter=6, textColor=colors.HexColor("#334155")),
        "body":     ParagraphStyle("Body",     parent=base["BodyText"], fontSize=10, spaceAfter=4),
        "mono":     ParagraphStyle("Mono",     parent=base["BodyText"], fontSize=9,  fontName="Courier",
                                    textColor=colors.HexColor("#475569")),
        "small":    ParagraphStyle("Small",    parent=base["BodyText"], fontSize=8,
                                    textColor=colors.HexColor("#64748b")),
    }


def _confidence_color(label: str) -> Any:
    return {
        "high":   colors.HexColor("#065f46"),
        "medium": colors.HexColor("#854d0e"),
        "low":    colors.HexColor("#7f1d1d"),
    }.get(label, colors.HexColor("#475569"))


# ── Public API ────────────────────────────────────────────────────────

def render_pdf(artifact_dict: Dict[str, Any]) -> Optional[bytes]:
    """Render an audit-grade PDF from an artifact dict.

    Returns the PDF bytes, or ``None`` if reportlab is unavailable.

    The input is the raw artifact dict (``CounterfactualArtifact.model_dump``),
    not the operator-rendered shape — the renderer needs the full
    estimator + refutation + challenge lists.
    """
    if not _REPORTLAB_AVAILABLE:
        logger.warning("reportlab not installed — PDF unavailable")
        return None

    s = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title="Counterfactual Audit Artifact")

    story = []

    # ── Header ────────────────────────────────────────────────────
    record_id = artifact_dict.get("record_id", "")
    confidence = artifact_dict.get("confidence", "low")
    audit_hash = artifact_dict.get("audit_record_hash", "")

    story.append(Paragraph("Counterfactual Audit Artifact", s["title"]))
    story.append(Paragraph(
        f"Record <b>{record_id}</b> &nbsp;·&nbsp; "
        f"Confidence: <font color='{_confidence_color(confidence).hexval()}'>"
        f"<b>{confidence}</b></font>",
        s["body"],
    ))

    # ── Attestation badge — this artifact's whole value is that it's
    # independently verifiable, so surface the signature up top, not just
    # in the provenance footer.
    if artifact_dict.get("signature_status") == "signed":
        story.append(Paragraph(
            "<font color='#065f46'><b>SIGNED</b></font> &nbsp;— ED25519 "
            f"(key: {artifact_dict.get('signing_key_source', '—') or '—'}) &nbsp;·&nbsp; "
            f"verify independently at <font name='Courier'>/verify/{audit_hash[:16]}…</font>",
            s["small"],
        ))

    # ── Verdict — plain-English summary of the audited effect ──────
    ok_est = [e for e in artifact_dict.get("estimates", []) if not e.get("error")]
    if ok_est:
        pts = [float(e.get("point", 0.0)) for e in ok_est]
        avg = sum(pts) / len(pts)
        direction = "decreased" if avg < 0 else "increased"
        agree = sum(1 for p in pts if (p < 0) == (avg < 0))
        story.append(Paragraph("Verdict", s["subtitle"]))
        story.append(Paragraph(
            f"Across <b>{len(ok_est)}</b> independent estimators, the intervention "
            f"<b>{direction}</b> the outcome (average effect "
            f"<b>{avg:+.3f}</b>; {agree}/{len(ok_est)} agree on direction).",
            s["body"],
        ))

    # ── Question + treatment + outcome ────────────────────────────
    query = artifact_dict.get("query", {})
    story.append(Paragraph("Question", s["subtitle"]))
    story.append(Paragraph(query.get("question", "—"), s["body"]))

    treatment = query.get("treatment", {})
    outcome = query.get("outcome", {})
    spec_table = Table(
        [
            ["Treatment column", treatment.get("column", "—"),
             "Actual value",         f"{treatment.get('actual', '—')}",
             "Counterfactual",       f"{treatment.get('counterfactual', '—')}"],
            ["Outcome column",   outcome.get("column", "—"),
             "Aggregation",       outcome.get("agg", "—"),
             "Window",            f"{outcome.get('window', ['—', '—'])[0]} → {outcome.get('window', ['—', '—'])[1]}"],
        ],
        colWidths=[35 * mm, 35 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm],
    )
    spec_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(Spacer(1, 6))
    story.append(spec_table)

    # ── Estimates ─────────────────────────────────────────────────
    story.append(Paragraph("Estimates", s["subtitle"]))
    est_rows = [["Method", "Point", "CI lower", "CI upper", "n", "Status"]]
    for e in artifact_dict.get("estimates", []):
        status = "ok" if not e.get("error") else f"error: {e['error'][:40]}"
        est_rows.append([
            e.get("method", "—"),
            f"{e.get('point', 0):.4f}" if not e.get("error") else "—",
            f"{e.get('ci_lower', 0):.4f}" if not e.get("error") else "—",
            f"{e.get('ci_upper', 0):.4f}" if not e.get("error") else "—",
            str(e.get("n_samples", "—")),
            status,
        ])
    est_table = Table(est_rows, colWidths=[35 * mm, 25 * mm, 25 * mm, 25 * mm, 15 * mm, 50 * mm])
    est_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(est_table)

    # ── Refutations ───────────────────────────────────────────────
    story.append(Paragraph("Refutation tests", s["subtitle"]))
    ref_rows = [["Refuter", "Estimate after", "p-value", "Passed", "Status"]]
    for r in artifact_dict.get("refutations", []):
        status = "ok" if not r.get("error") else f"error: {r['error'][:40]}"
        passed = "Yes" if r.get("passed") else "No"
        ref_rows.append([
            r.get("refuter", "—"),
            f"{r['estimate_after']:.4f}" if r.get("estimate_after") is not None else "—",
            f"{r['p_value']:.4f}" if r.get("p_value") is not None else "—",
            passed,
            status,
        ])
    ref_table = Table(ref_rows, colWidths=[40 * mm, 30 * mm, 20 * mm, 20 * mm, 65 * mm])
    ref_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
    ]))
    story.append(ref_table)

    # ── Challenges ────────────────────────────────────────────────
    story.append(Paragraph("Adversarial challenges", s["subtitle"]))
    challenges = artifact_dict.get("challenges", [])
    if not challenges:
        story.append(Paragraph(
            "<i>No challenges raised — the critic had no objections.</i>",
            s["body"],
        ))
    else:
        for c in challenges:
            sev_color = _confidence_color(c.get("severity", "low")).hexval()
            text = (
                f"<font color='{sev_color}'><b>[{c.get('severity', '—')}]</b></font> "
                f"{c.get('text', '—')}"
            )
            story.append(Paragraph(text, s["body"]))
            sc = c.get("suggested_check")
            if sc:
                story.append(Paragraph(f"&nbsp;&nbsp;→ <i>{sc}</i>", s["small"]))

    # ── Provenance footer ─────────────────────────────────────────
    story.append(Spacer(1, 14))
    story.append(Paragraph("Provenance", s["subtitle"]))
    sig_status = artifact_dict.get("signature_status", "unsigned")
    sig_source = artifact_dict.get("signing_key_source", "—") or "—"
    regen = artifact_dict.get("regenerated_critic", False)
    story.append(Paragraph(f"audit_record_hash: {audit_hash}", s["mono"]))
    story.append(Paragraph(
        f"dataset_fingerprint: {artifact_dict.get('dataset_fingerprint', '—')}",
        s["mono"],
    ))
    story.append(Paragraph(
        f"schema_version: {artifact_dict.get('schema_version', '—')} &nbsp;·&nbsp; "
        f"signature_status: <b>{sig_status}</b> (source: {sig_source}) &nbsp;·&nbsp; "
        f"regenerated_critic: {regen}",
        s["small"],
    ))

    doc.build(story)
    return buf.getvalue()
