#!/usr/bin/env bash
# Generate self-signed TLS certificates for NexusHV
# Run: bash scripts/generate-certs.sh

set -euo pipefail

CERT_DIR="${1:-$(dirname "$0")/../certs}"
mkdir -p "$CERT_DIR"

HOSTNAME=$(hostname -f 2>/dev/null || hostname)
DAYS=3650  # 10 years

echo "Generating self-signed TLS certificate for NexusHV..."
echo "  Hostname: $HOSTNAME"
echo "  Output: $CERT_DIR/"

openssl req -x509 -newkey rsa:4096 \
    -keyout "$CERT_DIR/nexushv.key" \
    -out "$CERT_DIR/nexushv.crt" \
    -sha256 -days "$DAYS" -nodes \
    -subj "/C=US/ST=State/L=City/O=NexusHV/OU=Hypervisor/CN=$HOSTNAME" \
    -addext "subjectAltName=DNS:$HOSTNAME,DNS:localhost,IP:127.0.0.1" \
    2>/dev/null

chmod 600 "$CERT_DIR/nexushv.key"
chmod 644 "$CERT_DIR/nexushv.crt"

echo "Certificate generated:"
echo "  Key:  $CERT_DIR/nexushv.key"
echo "  Cert: $CERT_DIR/nexushv.crt"
echo ""
echo "To use HTTPS, set environment variables:"
echo "  export NEXUSHV_TLS_CERT=$CERT_DIR/nexushv.crt"
echo "  export NEXUSHV_TLS_KEY=$CERT_DIR/nexushv.key"
echo ""
echo "Or start the API with:"
echo "  python3 api/nexushv_api.py --ssl-keyfile $CERT_DIR/nexushv.key --ssl-certfile $CERT_DIR/nexushv.crt"
