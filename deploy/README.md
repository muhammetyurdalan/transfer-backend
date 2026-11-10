# Sunucu kurulumu (cihanviptransfers.com)

Tüm sistem tek bir VPS'te Docker Compose ile çalışır. Bu klasördeki dosyalar
backend workflow'u tarafından her deploy'da sunucudaki `/opt/transfer`
klasörüne kopyalanır.

| Adres | Servis |
|---|---|
| `www.cihanviptransfers.com` | `frontend` (nginx, React build) |
| `cihanviptransfers.com` | `www`'ya yönlendirir |
| `api.cihanviptransfers.com` | `backend` (daphne), `/static` ve `/media` Caddy'den |
| `ms.cihanviptransfers.com` | `microservice` (uvicorn) |

Arka planda: `celery-worker`, `celery-beat`, `postgres`, `redis`, `caddy` (HTTPS).

## Sunucudaki klasör yapısı

```
/opt/transfer/
├── docker-compose.yml        ← repodan (workflow kopyalar)
├── caddy/Caddyfile           ← repodan
├── backup.sh                 ← repodan
├── env/                      ← SADECE sunucuda, git'e girmez
│   ├── backend.env
│   ├── postgres.env
│   └── microservice.env
├── microservice/settings/    ← microservice'in *.json ayar dosyaları
├── firebase/                 ← (opsiyonel) credentials.json
└── backups/                  ← gece alınan veritabanı yedekleri
```

`env/*.example` dosyaları hangi değerlerin gerektiğini gösterir.

## İlk kurulum (bir kez)

```bash
# deploy kullanıcısıyla
sudo mkdir -p /opt/transfer && sudo chown deploy:deploy /opt/transfer
mkdir -p /opt/transfer/{env,caddy,microservice/settings,firebase,backups}
chmod 700 /opt/transfer/env /opt/transfer/microservice

# GitHub Actions'ın sunucuya bağlanacağı anahtar
ssh-keygen -t ed25519 -f ~/.ssh/github_actions -N "" -C github-actions
cat ~/.ssh/github_actions.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_actions        # → GitHub secret: SSH_PRIVATE_KEY
```

Her üç repoda **Settings → Secrets and variables → Actions**:

| Secret | Değer |
|---|---|
| `SSH_HOST` | Sunucu IP'si |
| `SSH_PRIVATE_KEY` | `~/.ssh/github_actions` dosyasının tamamı |
| `FRONTEND_ENV` | Sadece frontend reposunda, `env/frontend.env.example` doldurulmuş hali |

Deploy sırası: **backend → microservice → frontend**.

## GitHub Actions olmadan deploy

Actions kullanılamadığında image'lar sunucuda build edilir:

```bash
/opt/transfer/deploy.sh backend        # veya microservice, frontend, all
```

Script `main` branch'ini `/opt/transfer/src` altına çeker, image'ı build eder
ve servisi yeniden başlatır. Frontend için `VITE_*` değerleri
`/opt/transfer/env/frontend.env` dosyasında olmalı. Private repolar için
sunucuda salt-okuma yetkili bir GitHub token'ı `~/.git-credentials` içinde durur.

## Günlük kullanım

```bash
cd /opt/transfer
docker compose ps                     # ne çalışıyor
docker compose logs -f backend        # canlı log
docker compose restart microservice   # tek servisi yeniden başlat
docker compose exec backend python manage.py createsuperuser
```

## Yedekleme

```bash
crontab -e
# her gece 03:30
30 3 * * * /opt/transfer/backup.sh >> /opt/transfer/backups/backup.log 2>&1
```

Geri yükleme:

```bash
gunzip -c backups/transfer-YYYY-MM-DD-HHMM.sql.gz | \
  docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
```
