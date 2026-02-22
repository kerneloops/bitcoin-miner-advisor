#!/bin/bash
# One-time server setup for Ubuntu 22.04 on DigitalOcean.
# Run as root on a fresh droplet:
#   bash setup.sh

set -e

echo "=== Installing system packages ==="
apt-get update -q && apt-get upgrade -y -q
apt-get install -y -q python3.11 python3.11-venv python3-pip git

echo "=== Creating app user ==="
if ! id "miner" &>/dev/null; then
    useradd -m -s /bin/bash miner
fi

echo "=== Cloning repo ==="
sudo -u miner bash -c '
    cd ~
    git clone https://github.com/kerneloops/bitcoin-miner-advisor.git
    cd bitcoin-miner-advisor
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -q
    cp .env.example .env
'

echo "=== Installing systemd service ==="
cp /home/miner/bitcoin-miner-advisor/deploy/miner-advisor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable miner-advisor

echo ""
echo "======================================================"
echo "Setup complete. Next steps:"
echo ""
echo "  1. Edit your API keys:"
echo "     nano /home/miner/bitcoin-miner-advisor/.env"
echo ""
echo "  2. Start the service:"
echo "     systemctl start miner-advisor"
echo ""
echo "  3. Check it's running:"
echo "     systemctl status miner-advisor"
echo "     curl http://localhost:8000"
echo ""
echo "  4. Open port 8000 in your DigitalOcean firewall,"
echo "     then visit http://<your-droplet-ip>:8000"
echo "======================================================"
