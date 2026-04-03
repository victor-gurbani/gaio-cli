#!/usr/bin/env bash

set -e

echo "🚀 Installing GAIO CLI (Google AI Overview)..."

INSTALL_DIR="$HOME/.local/share/gaio-cli"
BIN_DIR="$HOME/.local/bin"
REPO_URL="https://raw.githubusercontent.com/victor-gurbani/gaio-cli/main/ask_google.py" 

if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is required but not installed."
    exit 1
fi

mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

echo "📦 Creating Python virtual environment..."
python3 -m venv "$INSTALL_DIR/venv"

echo "⬇️  Installing dependencies (rich, playwright, playwright-stealth)..."
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q rich playwright playwright-stealth

echo "🌐 Downloading Chromium for Playwright (this might take a minute)..."
"$INSTALL_DIR/venv/bin/playwright" install chromium

echo "📜 Fetching the CLI script..."
if [ -f "ask_google.py" ]; then
    cp ask_google.py "$INSTALL_DIR/ask_google.py"
else
    curl -sSL "$REPO_URL" -o "$INSTALL_DIR/ask_google.py"
fi

echo "🔗 Creating symlink in $BIN_DIR/gaio..."
cat << 'EOF' > "$BIN_DIR/gaio"
#!/usr/bin/env bash
exec "$HOME/.local/share/gaio-cli/venv/bin/python" "$HOME/.local/share/gaio-cli/ask_google.py" "$@"
EOF

chmod +x "$BIN_DIR/gaio"

echo "✅ Installation complete!"
echo ""
echo "You can now use the command: gaio"
echo ""

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "⚠️  WARNING: $BIN_DIR is not in your PATH."
    echo "Please add the following line to your ~/.bashrc or ~/.zshrc:"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "Then restart your terminal."
fi
