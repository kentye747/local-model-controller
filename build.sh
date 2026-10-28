#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${LMC_BUILD_PYTHON:-.venv/bin/python}"
APP="dist/Local Model Controller.app"
VERSION="2.5.0"
ARCH="$(uname -m)"
if [[ "$ARCH" != arm64 && "$ARCH" != x86_64 ]]; then echo 'macOS arm64 or x86_64 is required.'; exit 1; fi
"$PYTHON" scripts/prepare_build.py
"$PYTHON" -m PyInstaller --noconfirm --clean --onedir --name LMCBackend --distpath build/backend --workpath build/pyinstaller --specpath build --paths build/portable-stdlib --paths . --hidden-import activity --hidden-import remote_activity --hidden-import conversation --hidden-import performance --hidden-import usage_history --hidden-import runtime_config --hidden-import websocket --hidden-import configparser --hidden-import zoneinfo --copy-metadata psutil --copy-metadata PyYAML --copy-metadata websocket-client backend_entry.py
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# dist/ is a generated, ignored directory. Replace only this build's own Backend.
if [[ -d "$APP/Contents/Resources/Backend" ]]; then mv "$APP/Contents/Resources/Backend" "build/previous-backend-$(date +%s)"; fi
cp -R build/backend/LMCBackend "$APP/Contents/Resources/Backend"
swiftc -parse-as-library -O -target "$ARCH-apple-macosx13.0" ControllerV2.swift HistoryView.swift InteractionDesign.swift Localization.swift -o "$APP/Contents/MacOS/LocalModelControl"
cp -R locales "$APP/Contents/Resources/"
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/"
# Bundle the license text of the Python distribution and frozen dependencies.
"$PYTHON" scripts/collect_licenses.py "$APP/Contents/Resources/ThirdPartyLicenses"
swift Icon.swift build/AppIcon.iconset
iconutil -c icns build/AppIcon.iconset -o "$APP/Contents/Resources/AppIcon.icns"
"$PYTHON" - "$APP" "$VERSION" <<'PY'
import pathlib,plistlib,sys
p=pathlib.Path(sys.argv[1])/'Contents/Info.plist'
p.write_bytes(plistlib.dumps({'CFBundleExecutable':'LocalModelControl','CFBundleIdentifier':'io.github.kentye747.local-model-controller','CFBundleName':'Local Model Controller','CFBundleDisplayName':'Local Model Controller','CFBundlePackageType':'APPL','CFBundleShortVersionString':sys.argv[2],'CFBundleVersion':'14','CFBundleIconFile':'AppIcon','CFBundleDevelopmentRegion':'en','CFBundleLocalizations':[row['id'] for row in __import__('json').loads(pathlib.Path('locales/languages.json').read_text())],'LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'NSPrincipalClass':'NSApplication'}))
PY
codesign --force --deep --sign "${LMC_SIGN_IDENTITY:--}" "$APP"
codesign --verify --deep --strict "$APP"
echo "Built $APP ($ARCH). Developer ID notarization is a separate release step."

"$PYTHON" scripts/check_binary.py "$APP"
