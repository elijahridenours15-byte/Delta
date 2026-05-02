#!/usr/bin/env bash
set -euo pipefail

ZIP="${1:-delta_coding_ionos_deploy.zip}"
if [ ! -f "$ZIP" ]; then
  echo "Error: $ZIP not found in $(pwd)"
  echo "Run: ./scripts/make_ionos_deploy_zip.py to create the bundle or pass the path to the zip."
  exit 1
fi

read -rp "IONOS SFTP host (e.g. sftp.clusterXX.ionos.com): " HOST
read -rp "IONOS username: " USER
read -rp "Remote webroot (e.g. /www/example.com/htdocs or /web/htdocs/www.example.com): " WEBROOT
read -rp "SFTP port [22]: " PORT
PORT="${PORT:-22}"

echo "Uploading $ZIP to $USER@$HOST:$WEBROOT ..."

# Upload with sftp
sftp -oPort="$PORT" "$USER@$HOST" <<EOF
mkdir $WEBROOT
put $(basename "$ZIP") $WEBROOT/$(basename "$ZIP")
quit
EOF

echo "Upload complete."

read -rp "Do you have SSH access to run unzip on the server? (y/N): " SSHY
if [ "${SSHY,,}" = "y" ]; then
  echo "Running remote unzip..."
  ssh -p "$PORT" "$USER@$HOST" "cd $WEBROOT && unzip -o $(basename \"$ZIP\") && rm -f $(basename \"$ZIP\")"
  echo "Remote unzip finished."
else
  echo "If you have SSH access, run this on your machine to unzip on the server:"
  echo "ssh -p $PORT $USER@$HOST 'cd $WEBROOT && unzip -o $(basename \"$ZIP\") && rm -f $(basename \"$ZIP\")'"
fi

echo "Done."
