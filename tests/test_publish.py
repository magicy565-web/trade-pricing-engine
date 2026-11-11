import subprocess
import sys
from pathlib import Path
from scripts.publish_github import sensitive


def test_publish_dry_run_has_no_network_or_mutation():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'scripts/publish_github.py','--dry-run'],cwd=root,text=True,capture_output=True,check=True)
    assert 'DRY_RUN_NO_CHANGES' in result.stdout and 'magicy565-web/trade-pricing-engine' in result.stdout


def test_publisher_blocks_private_paths():
    for path in ['private/server.json','keys/a.token','test.sqlite','secret.pem','.env','.env.production','data/costs.json']:
        assert sensitive(path)
    for path in ['README.md','tests/test_api.py','requirements.txt','.env.example']:
        assert not sensitive(path)
