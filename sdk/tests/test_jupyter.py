"""Smoke tests for the Jupyter rich-repr."""
from __future__ import annotations

from aura_counterfactual import CounterfactualArtifact
from aura_counterfactual.jupyter import artifact_html


def test_repr_html_contains_headline_and_confidence_badge(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    html = art._repr_html_()
    # Headline mentions the outcome column
    assert "y" in html
    # Confidence badge text present
    assert "medium" in html
    # Tables are collapsed under <details> so users don't get a wall of HTML
    assert "<details" in html


def test_repr_html_includes_estimator_methods(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    html = art._repr_html_()
    assert "linear_regression" in html
    assert "psm" in html


def test_repr_html_marks_signed_with_status(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    html = art._repr_html_()
    assert "signature: signed" in html


def test_repr_html_with_no_challenges_shows_empty_state(sample_artifact):
    payload = dict(sample_artifact, challenges=[])
    art = CounterfactualArtifact(**payload)
    html = art._repr_html_()
    assert "No challenges raised" in html


def test_repr_html_truncates_audit_hash(sample_artifact):
    art = CounterfactualArtifact(**sample_artifact)
    html = art._repr_html_()
    # First 16 hex chars of the hash plus an ellipsis
    assert "audit_record_hash: aaaaaaaaaaaaaaaa…" in html


def test_repr_html_marks_regenerated_critic(sample_artifact):
    payload = dict(sample_artifact, regenerated_critic=True)
    art = CounterfactualArtifact(**payload)
    html = art._repr_html_()
    assert "regenerated_critic=true" in html


def test_repr_html_escapes_user_text(sample_artifact):
    """Challenge text from the LLM must be HTML-escaped — otherwise an
    LLM-injected ``<script>`` tag would execute when an analyst views
    the artifact in a notebook."""
    payload = dict(sample_artifact)
    payload["challenges"] = [{
        "text": "<script>alert('xss')</script>",
        "severity": "high",
        "suggested_check": None,
    }]
    art = CounterfactualArtifact(**payload)
    html = artifact_html(art)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
