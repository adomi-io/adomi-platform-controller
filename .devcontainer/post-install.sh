#!/bin/bash
set -euo pipefail

echo "===================================="
echo "Adomi Platform Controller DevContainer Setup"
echo "===================================="

# Detect architecture using uname
MACHINE=$(uname -m)
case "${MACHINE}" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "WARNING: Unsupported architecture ${MACHINE}, defaulting to amd64"; ARCH="amd64" ;;
esac
echo "Architecture: ${ARCH}"

echo ""
echo "------------------------------------"
echo "Installing Python dependencies (editable + dev)..."
echo "------------------------------------"
pip install --upgrade pip
pip install -e ".[dev]"

echo ""
echo "------------------------------------"
echo "Installing Kubernetes tooling (kind, kubectl, helm)..."
echo "------------------------------------"

if ! command -v kind &> /dev/null; then
  curl -Lo /usr/local/bin/kind "https://kind.sigs.k8s.io/dl/latest/kind-linux-${ARCH}"
  chmod +x /usr/local/bin/kind
  echo "kind installed"
fi

if ! command -v kubectl &> /dev/null; then
  KUBECTL_VERSION=$(curl -Ls https://dl.k8s.io/release/stable.txt)
  curl -Lo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl"
  chmod +x /usr/local/bin/kubectl
  echo "kubectl installed"
fi

if ! command -v helm &> /dev/null; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  echo "helm installed"
fi

echo ""
echo "------------------------------------"
echo "Verifying installations..."
echo "------------------------------------"
python --version
pip show adomi-platform-controller | grep -E '^(Name|Version)' || true
kind version || true
kubectl version --client || true
helm version || true

echo ""
echo "===================================="
echo "DevContainer ready!"
echo "===================================="