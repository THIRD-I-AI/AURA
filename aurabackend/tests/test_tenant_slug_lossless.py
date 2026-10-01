"""BUG-231: distinct tenant ids must never share an upload directory, and ids that
were already safe must keep their exact old directory name (no data moves)."""
import uuid

import pytest

from shared.storage.base import tenant_slug


@pytest.mark.parametrize("tid", ["default", "orgA", "tenant_1", "acme-corp", str(uuid.uuid4())])
def test_already_safe_ids_keep_their_exact_old_name(tid):
    assert tenant_slug(tid) == tid


@pytest.mark.parametrize("tid", [None, ""])
def test_empty_ids_are_still_default(tid):
    assert tenant_slug(tid) == "default"


@pytest.mark.parametrize("a,b", [
    ("acme.com", "acmec.om"),
    ("orgA::x", "orgAx"),
    ("a/b", "ab"),
    ("org 1", "org1"),
])
def test_ids_that_used_to_collide_no_longer_share_a_directory(a, b):
    assert tenant_slug(a) != tenant_slug(b)


@pytest.mark.parametrize("hostile", ["../../etc", r"..\..\x", "a/../../b", "x\x00y"])
def test_hostile_ids_still_cannot_escape(hostile):
    s = tenant_slug(hostile)
    assert s and all(c.isalnum() or c in "-_" for c in s), s


def test_the_two_helpers_are_one_definition():
    from api_gateway.routers.workspaces import tenant_dir_name

    for tid in ["default", "orgA::x", "acme.com", None, str(uuid.uuid4())]:
        assert tenant_dir_name(tid) == tenant_slug(tid)
