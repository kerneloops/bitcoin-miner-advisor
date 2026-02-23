#!/bin/bash
# Deploy latest code to production.
# Run from your local machine: bash deploy/push-to-prod.sh

set -e

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new"

echo "=== Pushing to production ==="
$SSH root@172.233.136.138 '
    sudo -u miner bash -c "cd ~/bitcoin-miner-advisor && git pull && source .venv/bin/activate && pip install -r requirements.txt -q"
    systemctl restart miner-advisor
    echo "=== Done ==="
    systemctl status miner-advisor --no-pager -l
'
