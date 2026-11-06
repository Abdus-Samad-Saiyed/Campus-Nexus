# Campus Nexus — Full Production Deployment & Operations Guide

**Platform Version:** 2.5.0  
**Target Environment:** Linux (Ubuntu 24.04 LTS / Debian 12)  
**Documentation Suite:** Mirrored in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)  
**Architecture:** Django 6.0.5 + Channels 4 + Daphne ASGI + PostgreSQL 16 / SQLite3 + Redis 7 + NGINX + WhiteNoise  
**Repository:** [https://github.com/Abdus-Samad-Saiyed/Campus-Nexus](https://github.com/Abdus-Samad-Saiyed/Campus-Nexus)

---

## 1. Architecture Overview

```mermaid
flowchart TD
    Client(["Student Browsers & Mobile Clients"]) -->|HTTPS:443 & WSS:443| NGINX["NGINX Reverse Proxy (SSL Termination / HTTP/2)"]
    
    subgraph HostServer ["Production Host Server"]
        NGINX -->|HTTP Reverse Proxy| Daphne["Daphne ASGI Process Cluster (127.0.0.1:8000)"]
        NGINX -->|Direct Serve / Cache| WhiteNoiseStatic["WhiteNoise Compressed Static Files"]
        
        Daphne --> DjangoCore["Campus Nexus Django App (campus.settings.prod)"]
        
        DjangoCore -->|Database Queries| Postgres[("PostgreSQL 16 (Relational DB)")]
        DjangoCore -->|Channel Layer & Caching| Redis[("Redis 7 (Channel Layer & Throttles)")]
        
        DjangoCore -->|MIME / SMTP TLS:587| GmailSMTP["Google SMTP Relay (noreply.campus.nexus@gmail.com)"]
        DjangoCore -->|Media Uploads / Realpath Gate| MediaStorage["Protected Media Storage (/opt/campus-nexus/media)"]
    end

    DjangoCore -->|WebAuthn Assertion / Registration| FIDO2["FIDO2 / Windows Hello Security Keys"]
```

---

## 2. Server Specifications & Prerequisites

### Minimum Hardware Sizing
* **Pilot / Moderate Campus Traffic (< 2,500 active students):**
  * **vCPU:** 2 Cores
  * **RAM:** 4 GB
  * **Disk:** 40 GB NVMe / SSD
  * **Bandwidth:** 100 Mbps unmetered
* **High-Volume / Full Campus (> 10,000 active students):**
  * **vCPU:** 4 Cores
  * **RAM:** 8 GB
  * **Disk:** 80 GB NVMe / SSD

### Domain & DNS Configuration
Configure the following DNS records with your registrar or campus network administrator before provisioning:
* `A` Record: `campus-nexus.yourdomain.edu` &rarr; `<Public_IPv4>`
* `AAAA` Record (optional): `campus-nexus.yourdomain.edu` &rarr; `<Public_IPv6>`

---

## 3. Host Server Preparation

Connect to your target production server via SSH as `root` or a `sudo` user:

```bash
# 1. Update system packages
sudo apt update && sudo apt upgrade -y

# 2. Install essential dependencies, Python, PostgreSQL, Redis, and NGINX
sudo apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    libpq-dev \
    postgresql \
    postgresql-contrib \
    redis-server \
    nginx \
    certbot \
    python3-certbot-nginx \
    git \
    curl \
    ufw

# 3. Create a dedicated system service user
sudo useradd -m -d /opt/campus-nexus -s /bin/bash campusnexus
sudo usermod -aG www-data campusnexus
```

---

## 4. PostgreSQL & Redis Provisioning

### A. PostgreSQL 16
```bash
sudo -u postgres psql
```
Execute the following SQL commands (replace `SECURE_DB_PASSWORD_HERE` with a strong 32+ character random string):
```sql
CREATE DATABASE campus_nexus_prod TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';
CREATE USER nexus_app WITH PASSWORD 'SECURE_DB_PASSWORD_HERE';
GRANT ALL PRIVILEGES ON DATABASE campus_nexus_prod TO nexus_app;
ALTER DATABASE campus_nexus_prod OWNER TO nexus_app;
\q
```

### B. Redis 7
Enable Redis and ensure it listens strictly on localhost:
```bash
# Verify redis configuration: bind to localhost and enforce noeviction for reliable WebSocket pub/sub
sudo sed -i 's/^bind .*/bind 127.0.0.1 ::1/' /etc/redis/redis.conf
sudo sed -i 's/^# maxmemory-policy .*/maxmemory-policy noeviction/' /etc/redis/redis.conf
sudo systemctl restart redis-server
sudo systemctl enable redis-server

# Test Redis connectivity
redis-cli ping
# Expected response: PONG
```

---

## 5. Application Deployment & Virtual Environment

Switch to the `campusnexus` user:
```bash
sudo -u campusnexus -i
cd /opt/campus-nexus

# Clone the repository
git clone https://github.com/Abdus-Samad-Saiyed/Campus-Nexus.git .

# Create and activate Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Upgrade pip and install production dependencies
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

---

## 6. Production Environment Configuration (`.env`)

Create the `/opt/campus-nexus/.env` file. This file must be owned by `campusnexus:www-data` with `chmod 600`:

```bash
cat << 'EOF' > /opt/campus-nexus/.env
# ==============================================================================
# CAMPUS NEXUS PRODUCTION CONFIGURATION (.env)
# ==============================================================================

# Core Django Environment
DJANGO_SETTINGS_MODULE=campus.settings.prod
DJANGO_DEBUG=False
DJANGO_LOG_LEVEL=INFO

# Generate with: python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
DJANGO_SECRET_KEY=REPLACE_WITH_LONG_RANDOM_SECRET_KEY_MINIMUM_50_CHARACTERS_HERE

# Allowed Hostnames (Comma-separated, NO wildcards, NO localhost)
DJANGO_ALLOWED_HOSTS=campus-nexus.yourdomain.edu

# CSRF Trusted Origins (Must start with https://)
CSRF_TRUSTED_ORIGINS=https://campus-nexus.yourdomain.edu

# Database (PostgreSQL)
DATABASE_URL=postgres://nexus_app:SECURE_DB_PASSWORD_HERE@127.0.0.1:5432/campus_nexus_prod

# Distributed Cache & WebSockets (Redis)
REDIS_URL=redis://127.0.0.1:6379/0

# SMTP / Email OTP Relay (Google App Password)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=noreply.campus.nexus@gmail.com
EMAIL_HOST_PASSWORD=REPLACE_WITH_YOUR_16_CHAR_GOOGLE_APP_PASSWORD
DEFAULT_FROM_EMAIL=Campus Nexus <noreply.campus.nexus@gmail.com>

# Security & Proxy Headers
DJANGO_SECURE_SSL_REDIRECT=True
DJANGO_SESSION_COOKIE_SECURE=True
DJANGO_CSRF_COOKIE_SECURE=True
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True
DJANGO_SECURE_HSTS_PRELOAD=True
DJANGO_TRUST_X_FORWARDED_FOR=True
DJANGO_TRUSTED_PROXY_IPS=127.0.0.1

# WebAuthn / Passkeys (RP ID must exactly match domain)
WEBAUTHN_RP_ID=campus-nexus.yourdomain.edu
WEBAUTHN_RP_NAME=Campus Nexus NIT Srinagar
WEBAUTHN_ALLOWED_ORIGINS=https://campus-nexus.yourdomain.edu

# SMS Gateway (Roadmap feature: keep False until telecom resources procured)
SMS_ENABLED=False

# Root Superaccount Auto-Provisioning (Optional initial setup - or use python setup.py --add-user --admin)
PERSONAL_SUPERACCOUNT_ENROLLMENT=2024BCSE001
PERSONAL_SUPERACCOUNT_FIRST_NAME=Campus
PERSONAL_SUPERACCOUNT_LAST_NAME=Administrator
PERSONAL_SUPERACCOUNT_EMAIL=admin@yourdomain.edu
PERSONAL_SUPERACCOUNT_PHONE=+919999999999
EOF

chmod 600 /opt/campus-nexus/.env
```

---

## 7. Database Migration, Assets & Superaccount

Still as the `campusnexus` user inside `/opt/campus-nexus`:

```bash
source venv/bin/activate

# 1. Run migrations against PostgreSQL
python manage.py migrate --settings=campus.settings.prod

# 2. Compile and compress static assets for WhiteNoise
python manage.py collectstatic --noinput --settings=campus.settings.prod

# 3. Create storage directories with correct permissions
mkdir -p /opt/campus-nexus/media/profiles
chmod -R 750 /opt/campus-nexus/media

# 4. Provision Root Superuser & Run Database Setup
python setup.py --add-user --enrollment 2024BCSE001 --first-name "Campus" --last-name "Admin" --admin
# (Or launch the interactive setup menu: python setup.py)

# Exit back to root
exit
```

---

## 8. Process Supervisor (Systemd & Daphne ASGI)

Campus Nexus utilizes a multi-worker ASGI architecture across available CPU cores (e.g. 4 to 8 workers on modern multi-core CPUs like the Intel Core 7 240H) to multiply concurrent request capacity by 4x to 6x.

### Option A: Systemd Template Cluster (Linux Production)
Create the systemd template unit `/etc/systemd/system/campus-nexus@.service` to run multiple Daphne workers on sequential ports (8001..8004):

```ini
[Unit]
Description=Campus Nexus Daphne ASGI Worker %i
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=campusnexus
Group=www-data
WorkingDirectory=/opt/campus-nexus
EnvironmentFile=/opt/campus-nexus/.env
ExecStart=/opt/campus-nexus/venv/bin/daphne \
    -b 127.0.0.1 \
    -p 800%i \
    --proxy-headers \
    --access-log /var/log/campus-nexus/daphne.%i.access.log \
    campus.asgi:application

Restart=always
RestartSec=5s
KillSignal=SIGQUIT
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=campus-nexus

# Hardened Systemd Security Sandbox
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/opt/campus-nexus/media /var/log/campus-nexus

[Install]
WantedBy=multi-user.target
```

Create logging directories and enable the service:

```bash
sudo mkdir -p /var/log/campus-nexus
sudo chown -R campusnexus:www-data /var/log/campus-nexus

sudo systemctl daemon-reload
sudo systemctl enable campus-nexus
sudo systemctl start campus-nexus

# Verify service is running
sudo systemctl status campus-nexus
```

---

## 9. NGINX Reverse Proxy & SSL Configuration

Create `/etc/nginx/sites-available/campus-nexus.conf` (replace `campus-nexus.yourdomain.edu` with your actual domain):

```nginx
# Upstream Daphne Multi-Worker ASGI Cluster (Load balanced across 4-8 CPU worker processes)
upstream daphne_backend {
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
    server 127.0.0.1:8004;
    keepalive 32;
}

# Rate Limiting Zone
limit_req_zone $binary_remote_addr zone=campus_ratelimit:10m rate=15r/s;

server {
    listen 80;
    listen [::]:80;
    server_name campus-nexus.yourdomain.edu;

    # Certbot ACME Challenge Path
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    # Redirect all HTTP to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name campus-nexus.yourdomain.edu;

    # SSL Certificate Paths (Managed by Certbot)
    ssl_certificate /etc/letsencrypt/live/campus-nexus.yourdomain.edu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/campus-nexus.yourdomain.edu/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Security Headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; img-src 'self' data: https: blob:; connect-src 'self' wss: ws:;" always;

    # Upload Body Size Limits
    client_max_body_size 10M;

    # Logging
    access_log /var/log/nginx/campus_nexus_access.log;
    error_log /var/log/nginx/campus_nexus_error.log;

    # Health Check (Unthrottled for ALBs / Monitors)
    location /healthz/ {
        proxy_pass http://daphne_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WhiteNoise Pre-Compressed Static Files Direct Serve
    location /static/ {
        alias /opt/campus-nexus/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
        access_log off;
    }

    # Protected Media (Proxied through Django View with Permission & Realpath Gating)
    location /media/ {
        proxy_pass http://daphne_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket Real-Time Routing (/ws/)
    location /ws/ {
        proxy_pass http://daphne_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket Timeout Tuning
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    # Standard Web Application Routing
    location / {
        limit_req zone=campus_ratelimit burst=25 nodelay;

        proxy_pass http://daphne_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the NGINX site and acquire Let's Encrypt TLS certificate:

```bash
# Enable site
sudo ln -sf /etc/nginx/sites-available/campus-nexus.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Obtain SSL Certificate via Certbot
sudo certbot --nginx -d campus-nexus.yourdomain.edu

# Test configuration and reload NGINX
sudo nginx -t
sudo systemctl reload nginx
```

---

## 10. Firewall Configuration

Lock down server networking to essential ports only:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

---

## 11. Day-2 Operations Runbook

### A. Health Monitoring Probe
The `/healthz/` endpoint verifies database connectivity, Redis cache read/writes, and telemetry. It returns HTTP 200:
```bash
curl -I https://campus-nexus.yourdomain.edu/healthz/
# Expected: HTTP/2 200
```

### B. Automated PostgreSQL Backups
Create `/etc/cron.daily/campus-nexus-backup`:

```bash
sudo cat << 'EOF' > /etc/cron.daily/campus-nexus-backup
#!/bin/bash
set -euo pipefail

BACKUP_DIR="/var/backups/campus-nexus"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# Perform compressed PostgreSQL dump using .pgpass credentials
export PGPASSFILE="/opt/campus-nexus/.pgpass"
pg_dump -U nexus_app -h 127.0.0.1 campus_nexus_prod | gzip > "$BACKUP_DIR/db_$TIMESTAMP.sql.gz"

# Backup user media uploads
tar -czf "$BACKUP_DIR/media_$TIMESTAMP.tar.gz" -C /opt/campus-nexus media

# Retain backups for 30 days
find "$BACKUP_DIR" -type f -name "*.gz" -mtime +30 -delete
EOF

sudo chmod +x /etc/cron.daily/campus-nexus-backup
```

### C. Graceful Application Update Script
Save this script as `/opt/campus-nexus/deploy_update.sh`:

```bash
#!/bin/bash
set -e

echo "[*] Starting Campus Nexus Production Deployment..."
cd /opt/campus-nexus

# 1. Fetch latest changes
git pull origin main

# 2. Activate virtual environment
source venv/bin/activate

# 3. Install any updated dependencies
pip install -r requirements.txt

# 4. Run database migrations
python manage.py migrate --settings=campus.settings.prod

# 5. Collect static assets
python manage.py collectstatic --noinput --settings=campus.settings.prod

# 6. Restart Daphne ASGI service
sudo systemctl restart campus-nexus

echo "[SUCCESS] Campus Nexus successfully updated and online."
```
Make executable:
```bash
sudo chmod +x /opt/campus-nexus/deploy_update.sh
```

---

## 12. Physical Verification Desk On-Ground Protocol

The platform enforces root of trust through **In-Person Physical Verification**:

```mermaid
sequenceDiagram
    autonumber
    actor Student as Student
    actor Desk as Desk Operator / Superuser
    participant System as Campus Nexus Platform (/operations/)

    Student->>System: Submits Application (/apply/) with Enrollment & Phone
    System->>Student: Dispatches 6-digit Email OTP to Verify Ownership
    Student->>System: Enters OTP & Sets Password -> Application Queued
    
    Note over Student,Desk: Physical Interaction at Campus Verification Desk
    Student->>Desk: Presents Physical NIT Student ID Card
    Desk->>System: Accesses Operations Console (/operations/)
    Desk->>Desk: Verifies Student Name & Enrollment on Physical ID Card
    Desk->>System: Clicks "Approve" on Application
    System->>System: Creates Verified User Account & Activates Direct Features
    System-->>Student: Account Active: Student Can Now Sign In & Ride
```

---

*Sic Itur Ad Astra.*
