import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.workspaces import _tenant_upload_dir_for, tenant_dir_name  # noqa: I001

def test_dir_name_slugs_safely():
    assert tenant_dir_name("org_ABC-123") == "org_ABC-123"
    assert "/" not in tenant_dir_name("../../etc")
    assert "\\" not in tenant_dir_name("..\\win")
    assert ".." not in tenant_dir_name("a/b/c")
    assert tenant_dir_name("") == "default"
    assert tenant_dir_name(None) == "default"

def test_upload_dir_is_contained(tmp_path):
    root = str(tmp_path)
    d = _tenant_upload_dir_for(root, "org_1")
    assert os.path.commonpath((os.path.abspath(d), os.path.abspath(root))) == os.path.abspath(root)
    assert os.path.basename(d) == "org_1"
    assert os.path.basename(_tenant_upload_dir_for(root, None)) == "default"
