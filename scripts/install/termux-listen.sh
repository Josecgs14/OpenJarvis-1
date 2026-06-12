#!/usr/bin/env bash
# termux-listen.sh — One-shot setup for `jarvis listen` ("Hola Claude") on
# Termux (Android).
#
# Usage — paste this into Termux:
#
#   curl -fsSL https://raw.githubusercontent.com/josecgs14/openjarvis-1/claude/jarvis-project-9g0igq/scripts/install/termux-listen.sh | bash
#
# What it does:
#   1. Installs Termux:API + Python via `pkg` (idempotent).
#   2. Installs OpenJarvis from this branch via pip.
#   3. Prompts for OPENAI_API_KEY (used for the cloud chat model and for
#      Whisper speech-to-text) and saves it to ~/.bashrc. The key is never
#      written anywhere except your own phone.
#   4. Writes ~/.openjarvis/config.toml pointing at the cloud engine.
#   5. Prints the remaining manual steps (mic permission, wake-lock) and how
#      to start listening.
#
# Environment overrides:
#   OPENJARVIS_REPO_URL     git repo URL (default: this fork)
#   OPENJARVIS_REPO_BRANCH  git branch/ref (default: claude/jarvis-project-9g0igq)

set -euo pipefail

REPO_URL="${OPENJARVIS_REPO_URL:-https://github.com/josecgs14/openjarvis-1.git}"
REPO_BRANCH="${OPENJARVIS_REPO_BRANCH:-claude/jarvis-project-9g0igq}"

echo "==> Instalando Termux:API y Python..."
pkg update -y
pkg install -y python git termux-api

echo "==> Instalando dependencias ligeras de OpenJarvis..."
# `jarvis listen` only needs the lightweight runtime deps below (verified by
# importing openjarvis.cli/agents/tools/voice with exactly this set). Skipped
# on purpose:
#  - datasets (and its pandas/pyarrow/numpy chain) — no usable wheels on
#    Termux's Bionic libc, and not needed here.
#  - ddgs — pulls in `primp`, a Rust extension with no wheel for Termux's
#    cpython-313-aarch64-linux-android target; pip falls back to building
#    from source via maturin/rustc, which doesn't support that target either.
#    Only the web_search tool needs it (lazily imported), so the rest of
#    `jarvis listen` works fine without it.
pip install \
    "click>=8" "httpx>=0.27" "openai>=1.30" \
    "nvidia-ml-py>=12.560.30" "posthog>=3.0" "python-telegram-bot>=22.6" \
    "rich>=13" "tomlkit>=0.12" "websockets>=15.0.1" "pyyaml"

echo "==> Instalando OpenJarvis (sin dependencias pesadas)..."
# Notes on Termux's pip:
#  - It refuses `pip install --upgrade pip` (managed via `pkg install
#    python-pip`), so never do that.
#  - A normal `pip install git+...` builds in an *isolated* env, which
#    pip tries to bootstrap/upgrade itself inside — same forbidden
#    operation. Install the build backend (a pure-Python package with a
#    prebuilt wheel, no isolation needed) up front, then build with
#    --no-build-isolation so pip never spins up that isolated env.
#  - --no-deps skips `datasets` and friends (see above); we installed the
#    deps that actually matter ourselves.
pip install hatchling
pip install --no-build-isolation --no-deps "git+${REPO_URL}@${REPO_BRANCH}"

# --- OPENAI_API_KEY -------------------------------------------------------
if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo
    echo "Necesito tu clave de OpenAI para el modelo de chat y para"
    echo "transcribir tu voz (Whisper). Se guarda solo en este telefono,"
    echo "en ~/.bashrc — nunca se sube a ningun repositorio."
    read -rsp "OPENAI_API_KEY (no se mostrara mientras escribes): " OPENAI_API_KEY
    echo
    if [[ -n "$OPENAI_API_KEY" ]]; then
        echo "export OPENAI_API_KEY=\"$OPENAI_API_KEY\"" >> "$HOME/.bashrc"
        export OPENAI_API_KEY
    fi
fi

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    echo "==> Escribiendo configuracion (~/.openjarvis/config.toml)..."
    if [[ -f "$HOME/.openjarvis/config.toml" ]]; then
        echo "    ya existe ~/.openjarvis/config.toml — no se sobrescribe."
        echo "    si jarvis listen no encuentra un motor, agrega:"
        echo '      [engine]'
        echo '      default = "cloud"'
    else
        jarvis _bootstrap --write-config --prefer-cloud-when-available
    fi
else
    echo "No configuraste OPENAI_API_KEY — jarvis listen no tendra un motor"
    echo "de IA disponible hasta que definas una clave. Puedes hacerlo despues con:"
    echo '  echo "export OPENAI_API_KEY=\"sk-...\"" >> ~/.bashrc && source ~/.bashrc'
fi

cat <<'EOF'

==> Listo. Antes de usar "jarvis listen":

  1. Instala la app "Termux:API" (F-Droid o Play Store) si no la tienes.
  2. En Ajustes de Android > Apps > Termux:API, dale permiso de MICROFONO.
  3. (Recomendado, para que el telefono no se duerma):
       termux-wake-lock

Para empezar a escuchar "Hola Claude":

  jarvis listen
EOF
