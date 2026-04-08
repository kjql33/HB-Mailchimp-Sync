cd ~/docker-apps
unzip HB-Mautic-Sync-V3.zip
cd HB-Mautic-Sync-V3
pip install -r requirements.txt
cp .env.example .env
nano .env          # Fill in your credentials
python -m corev2.cli validate-config
python -m corev2.cli plan --output corev2/artifacts/plan.json
python -m corev2.cli apply --plan corev2/artifacts/plan.json --dry-run
python -m corev2.cli apply --plan corev2/artifacts/plan.json
