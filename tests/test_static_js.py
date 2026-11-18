"""Every page script parses as an ES module. A browser refuses a whole module over one early
error (0.4.2 shipped a second `const pf` in settings.js, and the Settings page went blank), and
plain `node --check` reads .js as a script, so it misses module-only errors."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = sorted((Path(__file__).parent.parent / "paperjay" / "static" / "js").glob("*.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS, ids=[p.name for p in JS])
def test_module_parses(path):
    r = subprocess.run(["node", "--input-type=module", "--check"], input=path.read_text(encoding="utf-8"),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
