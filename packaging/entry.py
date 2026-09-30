"""The frozen program's entry point: PyInstaller makes this file `Siftool.exe` (see `siftool.spec`).

A script of its own, beside the spec, so that the analysis only adds this
folder to the module search path -- not `src/siftool_gui`, whose modules
(`model`, `shell`, `settings`...) would shadow top-level names there.
"""

from siftool_gui.app import main

raise SystemExit(main())
