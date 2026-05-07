"""
Jupyter / IPython rich-repr for ``CounterfactualArtifact``.

Returning HTML from a Pydantic model's ``_repr_html_`` is the canonical
way to make notebook cells render rich content for an object — when a
user evaluates ``art`` in a notebook cell, IPython calls this function
and displays the result inline.

Visual language matches the operator card on the frontend (confidence
badge, headline, point + CI, debate reveal as a `<details>` block).
"""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CounterfactualArtifact


_CONFIDENCE_COLORS = {
    "low":    ("#7f1d1d", "#fca5a5", "rgba(220, 38, 38, 0.18)"),
    "medium": ("#854d0e", "#fde68a", "rgba(202, 138, 4, 0.18)"),
    "high":   ("#065f46", "#86efac", "rgba(5, 150, 105, 0.18)"),
}


def _badge(label: str) -> str:
    border, fg, bg = _CONFIDENCE_COLORS.get(label, ("#475569", "#cbd5e1", "rgba(71,85,105,0.18)"))
    return (
        f'<span style="padding:2px 8px;border-radius:4px;font-size:11px;'
        f'background:{bg};color:{fg};border:1px solid {border};'
        f'white-space:nowrap;">{escape(label)}</span>'
    )


def _headline(art: "CounterfactualArtifact") -> str:
    avg = art.average_point
    if avg is None:
        return "Estimation failed across all methods."
    direction = "increase" if avg > 0 else "decrease"
    outcome_col = art.query.outcome.column
    return (
        f"Counterfactual {direction} of about {avg:+.2f} on "
        f"{escape(outcome_col)} (confidence: {escape(art.confidence)})."
    )


def _estimates_table(art: "CounterfactualArtifact") -> str:
    rows = []
    for e in art.estimates:
        if e.error:
            rows.append(
                f'<tr style="opacity:0.6;">'
                f'<td style="padding:4px 8px;font-family:monospace;">{escape(e.method)}</td>'
                f'<td style="padding:4px 8px;" colspan="4">'
                f'<i>error: {escape(e.error[:80])}</i></td></tr>'
            )
        else:
            rows.append(
                f'<tr>'
                f'<td style="padding:4px 8px;font-family:monospace;">{escape(e.method)}</td>'
                f'<td style="padding:4px 8px;font-family:monospace;text-align:right;">{e.point:.4f}</td>'
                f'<td style="padding:4px 8px;font-family:monospace;text-align:right;">{e.ci_lower:.4f}</td>'
                f'<td style="padding:4px 8px;font-family:monospace;text-align:right;">{e.ci_upper:.4f}</td>'
                f'<td style="padding:4px 8px;font-family:monospace;text-align:right;">{e.n_samples}</td>'
                f'</tr>'
            )
    return (
        '<table style="border-collapse:collapse;font-size:12px;width:100%;">'
        '<thead><tr style="background:#1e293b;color:#f1f5f9;">'
        '<th style="padding:4px 8px;text-align:left;">method</th>'
        '<th style="padding:4px 8px;text-align:right;">point</th>'
        '<th style="padding:4px 8px;text-align:right;">ci_lower</th>'
        '<th style="padding:4px 8px;text-align:right;">ci_upper</th>'
        '<th style="padding:4px 8px;text-align:right;">n</th>'
        '</tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table>'
    )


def _refutations_table(art: "CounterfactualArtifact") -> str:
    rows = []
    for r in art.refutations:
        passed_label = "✓" if r.passed else "✗"
        passed_color = "#86efac" if r.passed else "#fca5a5"
        if r.error:
            rows.append(
                f'<tr style="opacity:0.6;">'
                f'<td style="padding:4px 8px;font-family:monospace;">{escape(r.refuter)}</td>'
                f'<td style="padding:4px 8px;" colspan="3">'
                f'<i>error: {escape(r.error[:80])}</i></td></tr>'
            )
        else:
            est = f'{r.estimate_after:.4f}' if r.estimate_after is not None else "—"
            pv = f'{r.p_value:.4f}' if r.p_value is not None else "—"
            rows.append(
                f'<tr>'
                f'<td style="padding:4px 8px;font-family:monospace;">{escape(r.refuter)}</td>'
                f'<td style="padding:4px 8px;font-family:monospace;text-align:right;">{est}</td>'
                f'<td style="padding:4px 8px;font-family:monospace;text-align:right;">{pv}</td>'
                f'<td style="padding:4px 8px;color:{passed_color};text-align:center;">{passed_label}</td>'
                f'</tr>'
            )
    return (
        '<table style="border-collapse:collapse;font-size:12px;width:100%;">'
        '<thead><tr style="background:#1e293b;color:#f1f5f9;">'
        '<th style="padding:4px 8px;text-align:left;">refuter</th>'
        '<th style="padding:4px 8px;text-align:right;">estimate_after</th>'
        '<th style="padding:4px 8px;text-align:right;">p_value</th>'
        '<th style="padding:4px 8px;text-align:center;">pass</th>'
        '</tr></thead><tbody>'
        + "".join(rows)
        + '</tbody></table>'
    )


def _challenges_block(art: "CounterfactualArtifact") -> str:
    if not art.challenges:
        return (
            '<p style="font-style:italic;color:#94a3b8;font-size:12px;margin:6px 0;">'
            'No challenges raised — refutation tests passed and the critic had no objections.'
            '</p>'
        )
    items = []
    for c in art.challenges:
        sc = (
            f'<div style="margin-left:24px;font-size:11px;color:#94a3b8;">→ '
            f'{escape(c.suggested_check)}</div>'
            if c.suggested_check else ""
        )
        items.append(
            f'<li style="margin:4px 0;">{_badge(c.severity)} {escape(c.text)}{sc}</li>'
        )
    return (
        '<ul style="list-style:none;padding-left:0;font-size:12px;">'
        + "".join(items) + '</ul>'
    )


def artifact_html(art: "CounterfactualArtifact") -> str:
    """Render a CounterfactualArtifact as an HTML block for Jupyter."""
    avg = art.average_point
    ci = art.ci_envelope
    ci_str = f'[{ci[0]:.2f}, {ci[1]:.2f}]' if ci else '—'
    point_str = f'{avg:+.2f}' if avg is not None else '—'

    sig_status = art.signature_status
    sig_color = "#86efac" if sig_status == "signed" else "#fde68a"
    regen_marker = " · regenerated_critic=true" if art.regenerated_critic else ""
    record_hash = art.audit_record_hash or ""

    return (
        '<div style="border:1px solid #1e293b;background:rgba(15,23,42,0.6);'
        'color:#cbd5e1;border-radius:8px;padding:14px;margin:8px 0;'
        'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;">'
        # Header
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">'
        f'<h3 style="margin:0;font-size:15px;color:#f1f5f9;font-weight:500;">'
        f'{escape(_headline(art))}'
        f'</h3>'
        f'{_badge(art.confidence)}'
        f'</div>'
        # Stats line
        f'<div style="margin-top:8px;font-size:12px;">'
        f'Point estimate <span style="font-family:monospace;">{point_str}</span> · '
        f'CI envelope <span style="font-family:monospace;">{ci_str}</span> · '
        f'{len(art.succeeded_estimators)}/{len(art.estimates)} estimators succeeded'
        f'</div>'
        # Tables — collapsed under <details> so they don't dominate the cell
        f'<details style="margin-top:10px;">'
        f'<summary style="cursor:pointer;color:#38bdf8;font-size:12px;'
        f'-webkit-user-select:none;user-select:none;">'
        f'Estimators ({len(art.estimates)})</summary>'
        f'<div style="margin-top:6px;">{_estimates_table(art)}</div>'
        f'</details>'
        f'<details style="margin-top:6px;">'
        f'<summary style="cursor:pointer;color:#38bdf8;font-size:12px;'
        f'-webkit-user-select:none;user-select:none;">'
        f'Refutations ({len(art.refutations)})</summary>'
        f'<div style="margin-top:6px;">{_refutations_table(art)}</div>'
        f'</details>'
        f'<details style="margin-top:6px;">'
        f'<summary style="cursor:pointer;color:#38bdf8;font-size:12px;'
        f'-webkit-user-select:none;user-select:none;">'
        f'Challenges ({len(art.challenges)}, '
        f'{len(art.high_severity_challenges)} high)</summary>'
        f'<div style="margin-top:6px;">{_challenges_block(art)}</div>'
        f'</details>'
        # Provenance footer
        f'<div style="margin-top:12px;font-family:monospace;font-size:10px;color:#64748b;">'
        f'audit_record_hash: {escape(record_hash[:16])}…'
        f' · <span style="color:{sig_color};">signature: {escape(sig_status)}</span>'
        f'{escape(regen_marker)}'
        f'</div>'
        f'</div>'
    )
