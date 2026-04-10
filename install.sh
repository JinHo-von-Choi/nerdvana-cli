#!/usr/bin/env bash
# NerdVana CLI installer
# Usage: curl -fsSL https://raw.githubusercontent.com/JinHo-von-Choi/nerdvana-cli/main/install.sh | bash
set -euo pipefail

REPO="JinHo-von-Choi/nerdvana-cli"
INSTALL_DIR="${NERDVANA_HOME:-$HOME/.nerdvana-cli}"
BIN_NAME="nerdvana"
MIN_PYTHON="3.11"

# --- helpers ---------------------------------------------------------------

info()  { printf '\033[0;34m%s\033[0m\n' "$*"; }
ok()    { printf '\033[0;32m%s\033[0m\n' "$*"; }
warn()  { printf '\033[0;33m%s\033[0m\n' "$*"; }
fail()  { printf '\033[0;31m%s\033[0m\n' "$*" >&2; exit 1; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

# --- pre-flight checks -----------------------------------------------------

info "NerdVana CLI installer"
echo ""

# Python version check
PYTHON=""
for cmd in python3 python; do
    if command_exists "$cmd"; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        if [ -n "$ver" ]; then
            major=${ver%%.*}
            minor=${ver#*.}
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                PYTHON="$cmd"
                break
            fi
        fi
    fi
done

[ -z "$PYTHON" ] && fail "Python >= $MIN_PYTHON required. Install it first: https://www.python.org/downloads/"
info "Using $PYTHON ($($PYTHON --version 2>&1))"

# git check
command_exists git || fail "git is required. Install it first."

# --- install ----------------------------------------------------------------

if [ -d "$INSTALL_DIR" ]; then
    info "Updating existing installation at $INSTALL_DIR..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin main 2>/dev/null || {
        warn "Pull failed, re-cloning..."
        cd ..
        rm -rf "$INSTALL_DIR"
        git clone --depth 1 "https://github.com/$REPO.git" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    }
else
    info "Installing to $INSTALL_DIR..."
    git clone --depth 1 "https://github.com/$REPO.git" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Create venv
VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Install dependencies
info "Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e ".[all]"

# --- shell integration ------------------------------------------------------

SHELL_NAME=$(basename "${SHELL:-/bin/bash}")
WRAPPER=$(cat <<'WRAPPER_EOF'
#!/usr/bin/env bash
exec "$HOME/.nerdvana-cli/.venv/bin/nerdvana" "$@"
WRAPPER_EOF
)

# Determine bin directory
USER_BIN=""
for dir in "$HOME/.local/bin" "$HOME/bin"; do
    if [ -d "$dir" ]; then
        USER_BIN="$dir"
        break
    fi
done

if [ -z "$USER_BIN" ]; then
    USER_BIN="$HOME/.local/bin"
    mkdir -p "$USER_BIN"
fi

# Write wrapper scripts
for cmd in nerdvana nc; do
    cat > "$USER_BIN/$cmd" <<WRAPPER_SCRIPT
#!/usr/bin/env bash
exec "\$HOME/.nerdvana-cli/.venv/bin/$cmd" "\$@"
WRAPPER_SCRIPT
    chmod +x "$USER_BIN/$cmd"
done

# PATH check
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$USER_BIN"; then
    PROFILE=""
    case "$SHELL_NAME" in
        zsh)  PROFILE="$HOME/.zshrc" ;;
        bash) PROFILE="$HOME/.bashrc" ;;
        fish) PROFILE="$HOME/.config/fish/config.fish" ;;
    esac

    if [ -n "$PROFILE" ]; then
        if [ "$SHELL_NAME" = "fish" ]; then
            echo "set -gx PATH $USER_BIN \$PATH" >> "$PROFILE"
        else
            echo "export PATH=\"$USER_BIN:\$PATH\"" >> "$PROFILE"
        fi
        warn "Added $USER_BIN to PATH in $PROFILE"
        warn "Run: source $PROFILE"
    else
        warn "Add $USER_BIN to your PATH manually."
    fi
fi

# --- provider setup hint ----------------------------------------------------

echo ""
ok "NerdVana CLI installed successfully!"
echo ""
info "Quick start:"
echo "  nerdvana              # interactive mode (default: Anthropic Claude)"
echo "  nc                    # short alias"
echo "  nerdvana run 'hello'  # single prompt"
echo ""
info "Set your API key:"
echo "  export ANTHROPIC_API_KEY='sk-ant-...'"
echo "  export OPENAI_API_KEY='sk-...'"
echo "  export GEMINI_API_KEY='...'"
echo ""
info "Or run the setup wizard:"
echo "  nerdvana setup"
echo ""
