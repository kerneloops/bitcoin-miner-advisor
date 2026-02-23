#!/bin/bash
# Deploy latest code to production.
# Run from your local machine: bash deploy/push-to-prod.sh

set -e

echo "=== Pushing to production ==="
ssh root@172.233.136.138 '
    cd /home/miner/bitcoin-miner-advisor
    git pull
    source .venv/bin/activate
    pip install -r requirements.txt -q
    systemctl restart miner-advisor
    echo "=== Done ==="
    systemctl status miner-advisor --no-pager -l
'
