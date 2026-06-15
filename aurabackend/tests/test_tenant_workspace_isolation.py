"""SaaS Phase 1B — tenant data isolation at the workspace chokepoint.

`current_workspace_id` is the single function every gateway data route uses to
scope saved queries, history, files, dashboards and lineage. These tests pin
the security property: when a request is authenticated, the scope is the
caller's TENANT (org_id from the verified token), and a forged X-Workspace-Id
header can never resolve to another tenant's scope. Unauthenticated dev/open
requests keep the legacy 'default' behaviour.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.workspaces import DEFAULT_WORKSPACE_ID, current_workspace_id

_HEADER = "X-Workspace-Id"


def _request(user=None, headers=None):
    """Minimal stand-in for a Starlette Request: `.state.user` (set by
    JWTAuthMiddleware) and a `.headers` mapping with `.get`."""
    class _State:
        pass

    req = type("_Req", (), {})()
    req.state = _State()
    if user is not None:
        req.state.user = user
    req.headers = headers or {}
    return req


def test_authenticated_requests_isolate_by_tenant():
    a = current_workspace_id(_request(user={"org_id": "org-a"}))
    b = current_workspace_id(_request(user={"org_id": "org-b"}))
    assert a == "org-a"
    assert b == "org-b"
    assert a != b


def test_forged_workspace_header_cannot_cross_tenant():
    # Tenant A names tenant B's scope in the header — must stay inside org-a.
    scope = current_workspace_id(
        _request(user={"org_id": "org-a"}, headers={_HEADER: "org-b"})
    )
    assert scope != "org-b"
    assert scope.startswith("org-a")


def test_within_tenant_folder_is_namespaced_under_the_tenant():
    scope = current_workspace_id(
        _request(user={"org_id": "org-a"}, headers={_HEADER: "marketing"})
    )
    assert scope == "org-a::marketing"


def test_default_header_maps_to_the_tenant_itself():
    scope = current_workspace_id(
        _request(user={"org_id": "org-a"}, headers={_HEADER: DEFAULT_WORKSPACE_ID})
    )
    assert scope == "org-a"


def test_legacy_token_without_org_id_isolates_by_subject():
    scope = current_workspace_id(_request(user={"sub": "user-1"}))
    assert scope == "user-1"


def test_unauthenticated_request_uses_legacy_default_scope():
    # No verified identity (dev/open mode) -> legacy header behaviour.
    assert current_workspace_id(_request()) == DEFAULT_WORKSPACE_ID
    # An unknown workspace id still falls back to default (never raises).
    assert current_workspace_id(_request(headers={_HEADER: "ghost"})) == DEFAULT_WORKSPACE_ID
