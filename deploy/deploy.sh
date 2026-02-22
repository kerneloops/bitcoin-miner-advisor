#!/bin/bash
# Run on the server to pull latest code and restart the service.
# Usage (from your local machine):
#   ssh miner@<droplet-ip> 'bash ~/bitcoin-miner-advisor/deploy/deploy.sh'

set -e

cd ~/bitcoin-miner-advisor

echo "=== Pulling latest code ==="
git pull

echo "=== Installing any new dependencies ==="
source .venv/bin/activate
pip install -r requirements.txt -q

echo "=== Restarting service ==="
sudo systemctl restart miner-advisor

echo "=== Status ==="
sudo systemctl status miner-advisor --no-pager -l
