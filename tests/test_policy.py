import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_policy_is_scoped_to_the_app():
    out = subprocess.run([sys.executable, str(ROOT / "iam" / "render_policy.py"), "--app", "siteselector",
                          "--database", "gi_site_selector", "--s3", "predik-geointelligence/tables/siteselector/"],
                         capture_output=True, text=True, check=True).stdout
    p = json.loads(out)
    text = json.dumps(p)
    assert "workgroup/siteselector" in text and "workgroup/primary" not in text
    assert "arn:aws:s3:::predik-geointelligence/tables/siteselector/*" in text
    assert "arn:aws:s3:::cadsourcedata/athenaoutput/siteselector/*" in text
    actions = {a for s in p["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
    assert not any(a.startswith(("glue:Create", "glue:Update", "glue:Delete")) or a == "*" for a in actions)
    writes = [s for s in p["Statement"] if "s3:PutObject" in s["Action"]]
    assert len(writes) == 1 and writes[0]["Resource"].startswith("arn:aws:s3:::cadsourcedata/athenaoutput/")
