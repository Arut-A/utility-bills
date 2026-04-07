# Setup Guide

This guide walks you through deploying the Utility Bills app from scratch on a Synology NAS (or any Docker host).

---

## Prerequisites

- **Docker** and **Docker Compose** installed (Synology: Container Manager)
- A **Gmail account** that receives your utility bill emails
- A **Google Cloud project** with the Gmail API enabled
- A **Telegram bot** (optional, for notifications)
- Ports: `8888` available on your host (or configure your own)

---

## Step 1 — Create the directory structure

On your NAS (via SSH or File Station):

```bash
mkdir -p /volume1/bills/config
mkdir -p /volume1/bills/credentials
mkdir -p /volume1/bills/raw
mkdir -p /volume1/docker/utility-bills/utility-bills
```

---

## Step 2 — Set up Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project (e.g. `utility-bills`).
2. Enable the **Gmail API** for the project.
3. Create **OAuth 2.0 credentials** → Application type: **Desktop app**.
4. Download the JSON file → rename it to `gmail_credentials.json`.
5. Copy it to the NAS:
   ```bash
   cp gmail_credentials.json /volume1/bills/credentials/gmail_credentials.json
   ```
6. **Important:** In OAuth consent screen settings, publish your app to **Production** (not Testing). Testing mode tokens expire after 7 days.

### First-time token generation

Run this once on a machine that has a browser to complete the OAuth flow:

```bash
pip install google-auth-oauthlib google-auth-httplib2
python refresh_gmail_token.py
```

This creates `gmail_token.json`. Copy it to:
```bash
cp gmail_token.json /volume1/bills/credentials/gmail_token.json
```

---

## Step 3 — Create a Telegram bot (optional)

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot`
2. Note the **bot token**.
3. Add the bot to your chat/group and get the **chat ID** (send a message, then call `https://api.telegram.org/bot<TOKEN>/getUpdates`).

---

## Step 4 — Copy vendor config

```bash
cp config/vendors.yaml /volume1/bills/config/vendors.yaml
```

Edit it to match your actual utility providers. See [`VENDOR_CONFIG.md`](VENDOR_CONFIG.md) for details.

---

## Step 5 — Create `docker-compose.yml`

Create `/volume1/docker/utility-bills/utility-bills/docker-compose.yml`:

```yaml
version: "3.9"

services:
  bills_mariadb:
    image: mariadb:10.11
    container_name: bills_mariadb
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: rootpassword        # change this
      MYSQL_DATABASE: utility_bills
      MYSQL_USER: bills_user
      MYSQL_PASSWORD: ${DB_PASSWORD}
    volumes:
      - /volume1/docker/utility-bills/mariadb:/var/lib/mysql
    networks:
      - bills-net

  bills_parser:
    image: youruser/bills-parser:latest        # your built image
    container_name: bills_parser
    restart: unless-stopped
    environment:
      DB_HOST: mariadb
      DB_USER: bills_user
      DB_PASSWORD: ${DB_PASSWORD}
      DB_NAME: utility_bills
      API_KEY: ${API_KEY}
      VENDOR_CONFIG_PATH: /data/config/vendors.yaml
      DASHBOARD_PATH: /data/dashboard.html
    volumes:
      - /volume1/bills:/data
    depends_on:
      - bills_mariadb
    networks:
      - bills-net

  bills_gmail_scraper:
    image: youruser/bills-scraper:latest       # your built image
    container_name: bills_gmail_scraper
    restart: unless-stopped
    environment:
      DB_HOST: mariadb
      DB_USER: bills_user
      DB_PASSWORD: ${DB_PASSWORD}
      DB_NAME: utility_bills
      API_KEY: ${API_KEY}
      PARSER_URL: http://bill-parser:8001
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID}
      POLL_DAILY_TIME: "09:00"
      GMAIL_TOKEN_PATH: /data/credentials/gmail_token.json
      GMAIL_CREDENTIALS_PATH: /data/credentials/gmail_credentials.json
      VENDOR_CONFIG_PATH: /data/config/vendors.yaml
    volumes:
      - /volume1/bills:/data
    depends_on:
      - bills_parser
    networks:
      - bills-net

  bills_api:
    image: youruser/bills-api:latest           # your built image
    container_name: bills_api
    restart: unless-stopped
    ports:
      - "8888:8000"
    environment:
      DB_HOST: mariadb
      DB_USER: bills_user
      DB_PASSWORD: ${DB_PASSWORD}
      DB_NAME: utility_bills
      API_KEY: ${API_KEY}
      DASHBOARD_PATH: /data/dashboard.html
      RAW_PDF_DIR: /data/raw
    volumes:
      - /volume1/bills:/data
    depends_on:
      - bills_mariadb
    networks:
      - bills-net

networks:
  bills-net:
    name: utility-bills_default
```

Create a `.env` file in the same directory (never commit this file):

```ini
DB_PASSWORD=choose-a-strong-password
API_KEY=choose-a-random-api-key
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
```

---

## Step 6 — Start the stack

```bash
cd /volume1/docker/utility-bills/utility-bills
docker compose up -d
```

Check logs:
```bash
docker logs bills_gmail_scraper --follow
docker logs bills_parser --follow
```

---

## Step 7 — Verify

1. Open `http://your-nas-ip:8888` — you should see the dashboard (empty at first).
2. Trigger a manual Gmail poll:
   ```bash
   docker exec bills_gmail_scraper python -c "from main import run_once; run_once()"
   ```
3. Check the dashboard again — bills should appear.

---

## Step 8 — (Optional) Set up HTTPS via Synology Reverse Proxy

In Synology **Control Panel → Login Portal → Advanced → Reverse Proxy**:

- **Source:** `https://your-nas.synology.me:8443`
- **Destination:** `http://localhost:8888`

This allows accessing the dashboard via HTTPS from outside your network.

---

## Updating the app

To apply code changes:

```bash
# Rebuild the image (from the service's source directory)
docker build -t youruser/bills-parser:latest ./bill-parser/
docker compose up -d --no-deps bills_parser

# Or for a quick in-place fix without rebuilding:
docker cp your_file.py bills_parser:/app/your_file.py
docker restart bills_parser
```

> Changes made directly inside a container are **lost on recreate**. Always copy changes back to the source directory.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Dashboard is empty | Check `docker logs bills_parser`. Run a manual poll (Step 7). |
| Gmail token expired | Run `refresh_gmail_token.py` locally, copy new token to `/volume1/bills/credentials/` |
| Bill parsed as wrong vendor | Check `parsing_errors` table in DB. Adjust `classification` in `vendors.yaml`. |
| PDF amount not extracted | Check `parsing_errors`. Improve `total_patterns` regex for that vendor. |
| Telegram not sending | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Ensure bot is in the chat. |
| uvicorn module cache | After any `.py` change: `docker restart bills_parser` |
