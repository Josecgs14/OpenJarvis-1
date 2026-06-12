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

# Best-effort: Termux ships prebuilt numpy/pandas wheels for its own Python.
# Installing them via `pkg` first lets pip reuse them instead of compiling
# from source (slow/likely to fail on-device). Safe to skip if unavailable.
pkg install -y python-numpy python-pandas 2>/dev/null || true

echo "==> Instalando OpenJarvis (puede tardar varios minutos)..."
# Note: do NOT `pip install --upgrade pip` here — Termux's pip refuses to
# upgrade itself (it's managed via `pkg install python-pip`).
pip install "git+${REPO_URL}@${REPO_BRANCH}"

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
