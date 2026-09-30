# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of the new Siftool (siftool_gui): windowed, onedir, dist/Siftool/Siftool.exe.

Run through packaging/build.py (distribution spec 2): it empties build/ and
dist/, writes the version resource from siftool.__version__ and passes its
path in SIFTOOL_VERSION_INFO, then has the result check itself
(Siftool.exe --self-test). That self-test is what proves nothing the
program needs went out with the exclusions below.

The program is src/ (pathex): the engine `siftool` and the window
`siftool_gui`, never the legacy siftool.py at the repository root.
"""

import os
from pathlib import Path

PACKAGING = Path(SPECPATH)  # noqa: F821 -- given by PyInstaller
SRC = PACKAGING.parent / "src"
ICON = SRC / "siftool_gui" / "assets" / "siftool.ico"

# Qt modules the program never imports: kept out even if something reachable mentions them.
UNUSED_QT_MODULES = [
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender",
    "QtAxContainer", "QtBluetooth", "QtCanvasPainter", "QtCharts", "QtConcurrent", "QtDataVisualization",
    "QtDBus", "QtDesigner", "QtGraphs", "QtGraphsWidgets", "QtHelp", "QtHttpServer", "QtLocation",
    "QtMultimedia", "QtMultimediaWidgets", "QtNetworkAuth", "QtNfc", "QtOpenGL", "QtOpenGLWidgets",
    "QtPdf", "QtPdfWidgets", "QtPositioning", "QtPrintSupport", "QtQml", "QtQuick", "QtQuick3D",
    "QtQuickControls2", "QtQuickTest", "QtQuickWidgets", "QtRemoteObjects", "QtScxml", "QtSensors",
    "QtSerialBus", "QtSerialPort", "QtSpatialAudio", "QtSql", "QtStateMachine", "QtTest",
    "QtTextToSpeech", "QtUiTools", "QtWebChannel", "QtWebEngineCore", "QtWebEngineQuick",
    "QtWebEngineWidgets", "QtWebSockets", "QtWebView",
]
# Python packages the program never uses. QFluentWidgets' optional acrylic blur (numpy, scipy, PIL,
# colorthief) is already off in development, where numpy is not installed; its media player and
# qframelesswindow's web view pull QtMultimedia and QtWebEngine. OpenSSL (ssl, _hashlib) serves no
# connection here: hashlib's SHA-256 is built into Python. The rest is the legacy program's.
UNUSED_PACKAGES = [
    "PIL", "numpy", "scipy", "colorthief", "qfluentwidgets.multimedia", "qframelesswindow.webengine",
    "ssl", "_ssl", "_hashlib", "tkinter", "tkinterdnd2", "piexif", "pytest", "pytestqt", "setuptools", "pip",
]
# Qt plugins the program does without, and the Qt libraries only they bring: the virtual keyboard's
# input context pulls Qt Quick, QML and OpenGL; the PDF image format, Qt PDF; TLS and network
# information serve connections the program never opens; TUIO is touch over the network. Direct2D and
# minimal are platforms it never runs on (windows does; offscreen is the self-test's). Of the image
# formats it keeps JPEG and WEBP (PNG is built in) for the previews, ICO for its icon and SVG for
# QFluentWidgets' icons.
UNUSED_PLUGINS = (
    "platforminputcontexts/", "generic/", "tls/", "networkinformation/",
    "platforms/qdirect2d.dll", "platforms/qminimal.dll",
    "imageformats/qgif.dll", "imageformats/qicns.dll", "imageformats/qpdf.dll", "imageformats/qtga.dll",
    "imageformats/qtiff.dll", "imageformats/qwbmp.dll",
)
UNUSED_LIBRARIES = ("qt6quick", "qt6qml", "qt6opengl", "qt6virtualkeyboard", "qt6pdf", "libssl", "libcrypto")


def needed(entry) -> bool:
    """Whether a collected binary or data file stays in the program folder."""
    name = entry[0].replace("\\", "/").lower()
    if name.startswith("pyside6/translations/"):
        return False  # Qt's own translations: the program loads none (its texts are Portuguese already)
    if name.startswith("pyside6/plugins/") and name[len("pyside6/plugins/"):].startswith(UNUSED_PLUGINS):
        return False
    file_name = name.rsplit("/", 1)[-1]
    if file_name == "opengl32sw.dll":
        return False  # software OpenGL: a widgets program draws without OpenGL
    return not (file_name.endswith(".dll") and file_name.startswith(UNUSED_LIBRARIES))


a = Analysis(
    [str(PACKAGING / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(ICON), "siftool_gui/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[*(f"PySide6.{module}" for module in UNUSED_QT_MODULES), *UNUSED_PACKAGES],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if needed(entry)]
a.datas = [entry for entry in a.datas if needed(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Siftool",
    icon=str(ICON),
    version=os.environ["SIFTOOL_VERSION_INFO"],
    console=False,
    disable_windowed_traceback=False,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Siftool",
)
