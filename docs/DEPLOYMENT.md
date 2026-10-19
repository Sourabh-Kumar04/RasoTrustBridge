# TrustBridge Production Deployment Guide

This guide details step-by-step instructions for deploying **TrustBridge** in production. TrustBridge is built with FastAPI, PostgreSQL / SQLite WAL, JWT dual-token authentication, PayPal sandbox/live REST APIs, and automated GitHub PR cryptographic verification.

---

## 1. Architecture Overview

```
                      +-----------------------------+
                      |   Cloudflare / Nginx (SSL)  |
                      +--------------+--------------+
                                     |
               +---------------------+---------------------+
               |                                           |
               v                                           v
     +-------------------+                       +-------------------+
     | Static Frontend   |                       | FastAPI Core API  |
     | (HTML/CSS/JS SPA) |                       | (Uvicorn / Docker)|
     +-------------------+                       +---------+---------+
                                                           |
                      +-------------------+----------------+------------------+
                      |                   |                                   |
                      v                   v                                   v
             +-----------------+ +-------------------+             +-------------------+
             | PostgreSQL 16   | | PayPal REST API   |             | GitHub Webhooks   |
             | Persistent DB   | | (Sandbox / Live)  |             | (HMAC Verification)|
             +-----------------+ +-------------------+             +-------------------+
```

---

## 2. Environment Variables Reference

| Variable | Required | Default / Example | Purpose |
| :--- | :---: | :--- | :--- |
| `ENVIRONMENT` | Yes | `production` | Enables production mode optimizations |
| `DEBUG` | Yes | `False` | Disables interactive debug logs |
| `HOST` | No | `0.0.0.0` | Bind host address |
| `PORT` | No | `8000` | Application HTTP port |
| `SECRET_KEY` | Yes | `[32+ byte random hex]` | Core session cryptographic salt |
| `JWT_SECRET` | Yes | `[32+ byte random hex]` | Signing secret for access & refresh JWTs |
| `JWT_ALGORITHM` | No | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` (24h) | Access token validity window |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `30` | Refresh token retention window |
| `DATABASE_URL` | Yes | `postgresql://user:pass@host:5432/dbname` | Database connection string |
| `PAYPAL_CLIENT_ID` | Optional | `AXXX...` | PayPal Developer Sandbox/Live App ID |
| `PAYPAL_CLIENT_SECRET` | Optional | `EXXX...` | PayPal Developer App Secret |
| `PAYPAL_MODE` | No | `sandbox` or `live` | Target PayPal processing environment |
| `PAYPAL_API_BASE` | No | `https://api-m.sandbox.paypal.com` | Base URL for PayPal REST endpoints |
| `PAYPAL_WEBHOOK_ID` | Optional | `4JHXXXX` | PayPal webhook identifier for verification |
| `GITHUB_WEBHOOK_SECRET` | Optional | `[random secret]` | HMAC-SHA256 secret for repository webhooks |
| `EMAIL_DRIVER` | No | `console` (default) | Email transport (`console`, `sendgrid`, `ses`) |
| `FX_RATES_API_URL` | No | `https://open.er-api.com/v6/latest/USD` | Public foreign exchange ticker endpoint |
| `FX_CACHE_TTL_SECONDS` | No | `3600` | FX rate cache TTL in seconds |

> **Generate cryptographic secrets:**
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

---

## 3. Primary Deployment: Railway (Recommended PaaS)

Railway provides native Dockerfile builds, automated TLS, and zero-configuration managed PostgreSQL.

### Step 1: Create a Railway Project
1. Log in to [Railway.app](https://railway.app).
2. Click **New Project** → **Provision PostgreSQL**.
3. Railway will provision a high-availability PostgreSQL instance and output `DATABASE_URL`.

### Step 2: Deploy the TrustBridge Service
1. Click **New** → **GitHub Repo** → select `Sourabh-Kumar04/RasoTrustBridge`.
2. Railway detects the `Dockerfile` in the root repository.
3. In the service settings, add the environment variables:
   - `DATABASE_URL`: `${{Postgres.DATABASE_URL}}` (automatically references PostgreSQL)
   - `ENVIRONMENT`: `production`
   - `DEBUG`: `False`
   - `SECRET_KEY`: `[generated 64-char hex]`
   - `JWT_SECRET`: `[generated 64-char hex]`
   - `GITHUB_WEBHOOK_SECRET`: `[your webhook secret]`
   - `PAYPAL_CLIENT_ID`: `[your sandbox/live client ID]`
   - `PAYPAL_CLIENT_SECRET`: `[your sandbox/live secret]`
   - `PAYPAL_MODE`: `sandbox`
4. Set Healthcheck Path: `/health`.

### Step 3: Domain & Verification
1. Under **Networking**, click **Generate Domain** (e.g., `trustbridge.up.railway.app`) or add a custom domain.
2. Verify deployment:
   ```bash
   curl -s https://trustbridge.up.railway.app/health
   # Response: {"status":"healthy","database_connected":true,"engine":"FastAPI","database":"postgresql",...}
   ```

---

## 4. Alternative Deployment: Render

### Step 1: Provision Render PostgreSQL
1. Log in to [Render.com](https://render.com) and click **New** → **PostgreSQL**.
2. Name: `trustbridge-db`, Region: closest to your users, Plan: Starter or Free.
3. Copy the **Internal Database URL**.

### Step 2: Deploy Web Service
1. Click **New** → **Web Service** → Connect your repository `Sourabh-Kumar04/RasoTrustBridge`.
2. Select **Docker** environment (Render uses the root `Dockerfile`).
3. Set the environment variables:
   - `DATABASE_URL`: `[Internal Database URL from Step 1]`
   - `ENVIRONMENT`: `production`
   - `DEBUG`: `False`
   - `SECRET_KEY`: `[generated 64-char hex]`
   - `JWT_SECRET`: `[generated 64-char hex]`
   - `PORT`: `8000`
4. Health Check Path: `/health`.
5. Click **Create Web Service**.

---

## 5. Alternative Deployment: Linux VPS (Ubuntu with Docker Compose)

For high-performance self-hosted infrastructure on AWS EC2, DigitalOcean Droplet, or Hetzner.

### Step 1: Provision the Server & Install Docker
```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl git ufw

# Install Docker & Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

### Step 2: Configure Firewall
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

### Step 3: Clone Repository & Configure Environment
```bash
git clone https://github.com/Sourabh-Kumar04/RasoTrustBridge.git /opt/trustbridge
cd /opt/trustbridge

cp .env.example .env
nano .env  # Populate JWT_SECRET, SECRET_KEY, PAYPAL credentials
```

### Step 4: Launch via Docker Compose
```bash
# Build and run containers in detached mode
docker compose up -d --build

# Verify running containers
docker compose ps
docker compose logs -f web
```

### Step 5: Configure Nginx Reverse Proxy with TLS (Let's Encrypt)
Create `/etc/nginx/sites-available/trustbridge`:
```nginx
server {
    listen 80;
    server_name api.trustbridge.network;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable site and install SSL certificate:
```bash
sudo ln -s /etc/nginx/sites-available/trustbridge /etc/nginx/sites-enabled/
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.trustbridge.network
```

---

## 6. Email Service Integration (SendGrid / AWS SES)

TrustBridge includes a pluggable `NotificationService.send_email` driver. In development and testing, it defaults to `console` (logging outbound transactional emails without credential dependencies).

### How to Plug In SendGrid:
1. In `app/services/notification_service.py`, add `sendgrid`:
   ```python
   # uv add sendgrid
   from sendgrid import SendGridAPIClient
   from sendgrid.helpers.mail import Mail

   def send_email_sendgrid(to_email: str, subject: str, body_text: str):
       message = Mail(
           from_email=settings.SENDGRID_FROM_EMAIL,
           to_emails=to_email,
           subject=subject,
           plain_text_content=body_text
       )
       sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
       sg.send(message)
   ```
2. Set `EMAIL_DRIVER=sendgrid` and configure `SENDGRID_API_KEY` in your environment.

### How to Plug In AWS SES:
1. In `app/services/notification_service.py`, add `boto3`:
   ```python
   # uv add boto3
   import boto3

   def send_email_ses(to_email: str, subject: str, body_text: str):
       ses = boto3.client('ses', region_name=settings.AWS_SES_REGION)
       ses.send_email(
           Source=settings.SES_FROM_EMAIL,
           Destination={'ToAddresses': [to_email]},
           Message={
               'Subject': {'Data': subject},
               'Body': {'Text': {'Data': body_text}}
           }
       )
   ```
2. Set `EMAIL_DRIVER=ses` and configure standard AWS credentials.

---

## 7. Health Check & Uptime Monitoring

TrustBridge provides a zero-overhead health check endpoint at `GET /health`.

### Response Payload:
```json
{
  "status": "healthy",
  "database_connected": true,
  "engine": "FastAPI",
  "database": "postgresql",
  "paypal_configured": true,
  "version": "0.1.0"
}
```

Configure your uptime monitoring service (e.g., BetterStack, Pingdom, or AWS Route53 Health Checks) to poll `https://<YOUR_DOMAIN>/health` every 30 seconds, looking for HTTP 200 and `"status": "healthy"`.

---

## 8. Database Migrations & Maintenance (Alembic + PostgreSQL)

PostgreSQL is the default production and development database configured via `DATABASE_URL`. SQLite is preserved as a zero-setup local dev fallback.

### Running Alembic Migrations:
TrustBridge uses **Alembic** for reproducible database schema migrations across all models (Users, Contracts, Milestones, AuditLogs, Disputes, EscrowHolds, Notifications, LedgerEntries, and ProcessedWebhookDeliveries):

```bash
# Run migrations to bring database up to date
alembic upgrade head

# Create a new migration revision after model changes
alembic revision --autogenerate -m "describe_change"

# Check migration history and current head
alembic current
alembic history
```

- When running in Docker Compose or containerized environments, database schemas can be migrated via `alembic upgrade head` or auto-initialized via `init_db()` in `app/main.py` lifespan.
- **Automated Database Backups (PostgreSQL):**
  ```bash
  # Daily cron backup command:
  docker exec trustbridge_db pg_dump -U trustbridge trustbridge | gzip > /opt/backups/tb_$(date +%Y%m%d).sql.gz
  ```
