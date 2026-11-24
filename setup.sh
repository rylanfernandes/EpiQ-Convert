#!/bin/zsh
# Sets up EpiQ Convert on a Mac:
#   1. creates a Python environment in ~/.venvs/epiq-map (if missing)
#   2. installs EpiQ-Map into it (if missing)
#   3. puts "EpiQ Convert.app" on the Desktop
#
# Usage:
#   ./setup.sh                       # install EpiQ-Map from PyPI
#   ./setup.sh /path/to/EpiQ-Map     # install from a local checkout (editable)
set -e
HERE="${0:A:h}"
VENV="$HOME/.venvs/epiq-map"
APP="$HOME/Desktop/EpiQ Convert.app"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating Python environment in $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
fi

if ! "$VENV/bin/python" -c "import epiq_map, PyQt6, h5py, scipy" 2>/dev/null; then
  if [[ -n "$1" ]]; then
    echo "Installing EpiQ-Map from $1"
    "$VENV/bin/python" -m pip install -e "$1[qt6]"
  else
    echo "Installing EpiQ-Map from PyPI"
    "$VENV/bin/python" -m pip install "EpiQ-Map[qt6]"
  fi
fi

echo "Creating $APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>EpiQ Convert</string>
<key>CFBundleDisplayName</key><string>EpiQ Convert</string>
<key>CFBundleIdentifier</key><string>local.epiq.convert</string>
<key>CFBundleExecutable</key><string>launch</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/launch" <<LAUNCH
#!/bin/zsh
mkdir -p "\$HOME/Library/Logs"
exec "$VENV/bin/python" "$HERE/epiq_convert.py" >> "\$HOME/Library/Logs/EpiQ-Convert.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/launch"
echo "Done. Double-click \"EpiQ Convert\" on the Desktop."
