"""Guards spec section 8's "Isolamento": `import siftool` never pulls in Qt.

No `pytest.importorskip("PySide6")` here on purpose -- this must pass
even in an environment where the `gui` extra was never installed, since
that is exactly the property it is checking.
"""

from __future__ import annotations

import subprocess
import sys

_CODE = (
    "import siftool, siftool.cli, siftool.batch, sys; "
    "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'qfluentwidgets', 'siftool_gui')))"
)


def test_import_siftool_loads_no_qt(tmp_path):
    result = subprocess.run(
        [sys.executable, "-P", "-c", _CODE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
