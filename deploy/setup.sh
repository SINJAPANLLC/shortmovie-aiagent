#!/bin/bash
set -e

DOMAIN="shortmovie-aiagent-sinjapan.site"
APP_DIR="/opt/shortmovie-aiagent"
REPO="https://github.com/SINJAPANLLC/shortmovie-aiagent.git"

echo "=== VPS Setup for CEOの扉 ==="

apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx ffmpeg curl fonts-noto-cjk

if [ ! -d "$APP_DIR" ]; then
    git clone "$REPO" "$APP_DIR"
else
    cd "$APP_DIR" && git pull
fi

cd "$APP_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r deploy/requirements.txt

mkdir -p app/static/{scenes,videos,audio,scene_images,thumbnail,characters,bgm,subtitle}

if [ ! -f "$APP_DIR/.env" ]; then
    cp deploy/.env.example "$APP_DIR/.env"
    echo ""
    echo ">>> .env ファイルを作成しました。APIキーを設定してください："
    echo "    nano $APP_DIR/.env"
    echo ""
fi

cp deploy/shortmovie.service /etc/systemd/system/shortmovie.service
systemctl daemon-reload
systemctl enable shortmovie

cat > /etc/nginx/sites-available/$DOMAIN << 'NGINX'
server {
    listen 80;
    server_name shortmovie-aiagent-sinjapan.site;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_connect_timeout 600s;
    }

    location /static/ {
        alias /opt/shortmovie-aiagent/app/static/;
        expires 1d;
        add_header Cache-Control "public, immutable";
    }
}
NGINX

ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "次の手順："
echo "1. .env を編集:  nano $APP_DIR/.env"
echo "2. アプリ起動:   systemctl start shortmovie"
echo "3. SSL設定:      certbot --nginx -d $DOMAIN"
echo "4. 確認:         systemctl status shortmovie"
echo ""
