# Production-Deployment-Ubuntu

# Gvoiceus — Production Deployment on Hostinger Ubuntu (uWSGI + Nginx + PostgreSQL + HTTPS)

**Stack:** Ubuntu 22.04/24.04 • Python 3.10+ • Django 5 • uWSGI • Nginx • PostgreSQL • Let’s Encrypt

* Project directory: `/home/deploy/gvoiceus`
* Django settings module (prod): `gvoiceus.settings_prod`
* Domain: `gvoiceus.com` (and `www.gvoiceus.com`)
* Database: `gvoice***` / user `******`
* System user: `*****`

---

## 1) Create sudo user (once)

```bash
adduser deploy
usermod -aG sudo deploy
su - deploy
```

---

## 2) Install system packages

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install git python3-venv python3-pip python3-dev build-essential \
                    postgresql postgresql-contrib libpq-dev \
                    nginx ufw \
                    uwsgi-core uwsgi-plugin-python3 \
                    certbot python3-certbot-nginx
```

---

## 3) Clone repository & create virtualenv

```bash
cd /home/deploy
git clone <GIT_REPO_URL> gvoiceus
cd gvoiceus

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
```

> Ensure `uwsgi` and `psycopg2`/`psycopg2-binary` are in `requirements.txt`.

---

## 4) PostgreSQL database

```bash
sudo -u postgres psql
CREATE DATABASE gvoiceus_db;
CREATE USER **** WITH ENCRYPTED PASSWORD '<STRONG_DB_PASSWORD>';
GRANT ALL PRIVILEGES ON DATABASE gvoiceus_db TO gvoiceus_user;
\q
```

---

## 5) Environment file

Create `/home/deploy/gvoiceus/.env`:

```dotenv
# Django
DJANGO_SETTINGS_MODULE=gvoiceus.settings_prod
DJANGO_SECRET_KEY=<SET_A_STRONG_SECRET>
DEBUG=False
TIME_ZONE=UTC

# Hosts / CSRF
ALLOWED_HOSTS=gvoiceus.com,www.gvoiceus.com,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://gvoiceus.com,https://www.gvoiceus.com

# Site URL
SITE_URL=https://gvoiceus.com

# Database (PostgreSQL)
DB_ENGINE=django.db.backends.postgresql
DB_NAME=gvoice****
DB_USER=******
DB_PASSWORD=<STRONG_DB_PASSWORD>
DB_HOST=127.0.0.1
DB_PORT=5****

# Email (Gmail App Password recommended)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=****verify@gmail.com
EMAIL_HOST_PASSWORD=<GMAIL_APP_PASSWORD>
DEFAULT_FROM_EMAIL="Gvoiceus <*****verify@gmail.com>"
EMAIL_TIMEOUT=20

# Security (optional overrides)
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31****
```

> Never commit `.env` to Git.

---

## 6) Django production settings

`gvoiceus/settings_prod.py` (already present in your repo). It should effectively contain:

* `DEBUG = False`
* `ALLOWED_HOSTS = ["gvoiceus.com", "www.gvoiceus.com", "127.0.0.1", "localhost"]`
* `SITE_URL = "https://gvoiceus.com"`
* `CSRF_TRUSTED_ORIGINS = ["https://gvoiceus.com","https://www.gvoiceus.com"]`
* `STATIC_ROOT = "/home/deploy/gvoiceus/staticfiles"`
* `MEDIA_ROOT  = "/home/deploy/gvoiceus/media"`
* PostgreSQL settings pointing to `gvoiceus_db` / `gvoiceus_user`
* Security headers and HSTS as in your file

---

## 7) Migrate, collect static, create superuser

```bash
cd /home/deploy/gvoiceus
source .venv/bin/activate

python manage.py migrate --settings=gvoiceus.settings_prod
python manage.py collectstatic --noinput --settings=gvoiceus.settings_prod
python manage.py createsuperuser --settings=gvoiceus.settings_prod
```

---

## 8) uWSGI configuration

Create `/home/deploy/gvoiceus/uwsgi.ini`:

```ini
[uwsgi]
chdir = /home/deploy/gvoiceus
module = gvoiceus.wsgi:application
env = DJANGO_SETTINGS_MODULE=gvoiceus.settings_prod
home = /home/deploy/gvoiceus/.venv

master = true
processes = **
threads = **
harakiri = **
vacuum = true
disable-logging = true

socket = /run/uwsgi/gvoiceus.sock
chmod-socket = 660
uid = www-data
gid = www-data

logto = /var/log/uwsgi/gvoiceus.log
```

Create runtime & log dirs:

```bash
sudo mkdir -p /run/uwsgi
sudo mkdir -p /var/log/uwsgi
sudo chown www-data:www-data /run/uwsgi /var/log/uwsgi
```

---

## 9) systemd service for uWSGI

Create `/etc/systemd/system/uwsgi-gvoiceus.service`:

```ini
[Unit]
Description=uWSGI for gvoiceus
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/home/deploy/gvoiceus
Environment="DJANGO_SETTINGS_MODULE=gvoiceus.settings_prod"
ExecStart=/usr/bin/uwsgi --ini /home/deploy/gvoiceus/uwsgi.ini
Restart=always
KillSignal=SIGINT
Type=notify
NotifyAccess=all
RuntimeDirectory=uwsgi

[Install]
WantedBy=multi-user.target
```

Enable & start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now uwsgi-gvoiceus
sudo systemctl status uwsgi-gvoiceus
```

---

## 10) Nginx virtual host

Create `/etc/nginx/sites-available/gvoiceus`:

```nginx
server {
    listen 80;
    server_name gvoiceus.com www.gvoiceus.com;

    client_max_body_size 20m;

    location /static/ {
        alias /home/deploy/gvoiceus/staticfiles/;
    }

    location /media/ {
        alias /home/deploy/gvoiceus/media/;
    }

    location / {
        include uwsgi_params;
        uwsgi_pass unix:/run/uwsgi/gvoiceus.sock;
        uwsgi_read_timeout 60s;
    }

    add_header X-Content-Type-Options nosniff;
    add_header Referrer-Policy "strict-origin-when-cross-origin";
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/gvoiceus /etc/nginx/sites-enabled/gvoiceus
sudo nginx -t
sudo systemctl reload nginx
```

---

## 11) HTTPS with Let’s Encrypt

```bash
sudo certbot --nginx -d gvoiceus.com -d www.gvoiceus.com --agree-tos -m admin@gvoiceus.com --redirect
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

---

## 12) Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # opens 80 & 443
sudo ufw enable
sudo ufw status
```

---

## 13) Permissions (static/media, sockets, logs)

```bash
sudo chown -R deploy:deploy /home/deploy/gvoiceus
sudo chown -R www-data:www-data /home/deploy/gvoiceus/staticfiles /home/deploy/gvoiceus/media
sudo chown -R www-data:www-data /run/uwsgi /var/log/uwsgi
```

---

## 14) Email quick checks (Gmail SMTP)

```bash
source /home/deploy/gvoiceus/.venv/bin/activate
python - <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE","gvoiceus.settings_prod")
from django.conf import settings
print("EMAIL:", settings.EMAIL_HOST, settings.EMAIL_PORT, settings.EMAIL_USE_TLS, settings.EMAIL_HOST_USER)
PY

python - <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE","gvoiceus.settings_prod")
from django.core.mail import send_mail
print(send_mail("SMTP test","Hello from Gvoiceus",
                "Gvoiceus <gvoiceus.verify@gmail.com>",
                ["gvoiceus.verify@gmail.com"], fail_silently=False))
PY
```

---

## 15) Logs & live monitoring

```bash
journalctl -u uwsgi-gvoiceus -f
sudo tail -n 200 /var/log/uwsgi/gvoiceus.log
sudo tail -n 200 /var/log/nginx/access.log
sudo tail -n 200 /var/log/nginx/error.log
```

---

## 16) Deploy updates

```bash
cd /home/deploy/gvoiceus
git pull --ff-only
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate --settings=gvoiceus.settings_prod
python manage.py collectstatic --noinput --settings=gvoiceus.settings_prod
sudo systemctl reload nginx
sudo systemctl restart uwsgi-gvoiceus
```

---

## 17) Health checks

```bash
curl -I https://gvoiceus.com/
curl -I https://gvoiceus.com/static/admin/css/base.css
sudo ss -ltnp | grep ':80\|:443'
sudo systemctl status uwsgi-gvoiceus nginx
```

---

## 18) Security notes

* Keep `.env` private (never push to Git)
* Use strong DB and email app passwords
* Regular OS/package updates
* Review `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for `gvoiceus.com`
* Consider SSH key auth, disable root SSH, and Fail2ban if needed

---

## 19) Backup idea

* Database: `pg_dump gvoiceus_db > /home/deploy/backup/gvoiceus_$(date +%F).sql`
* Media: rsync/S3
* Automate with cron + offsite storage

---
