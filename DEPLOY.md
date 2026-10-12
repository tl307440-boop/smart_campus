# 智悦校园 · Docker 部署指南

## 前置条件

- 云服务器已安装 Docker 和 docker-compose
- SSH 访问权限

## 一、上传项目到服务器

### 1.1 本地打包

```powershell
cd d:\4C_competition\smart_campus\smart_campus
tar -czf smart_campus.tar.gz --exclude="venv" --exclude="__pycache__" --exclude="*.mp3" --exclude="chroma_db_data" --exclude="test_data" .
```

### 1.2 上传到服务器

```powershell
scp smart_campus.tar.gz root@你的服务器IP:/root/
```

### 1.3 服务器上解压

```bash
ssh root@你的服务器IP
mkdir -p /opt/smart_campus && cd /opt/smart_campus
tar -xzf /root/smart_campus.tar.gz
rm /root/smart_campus.tar.gz
```

## 二、配置环境变量

```bash
cat > /opt/smart_campus/backend/.env << 'EOF'
deepseek_API_key=sk-你的DeepSeek密钥
deepseek_url=https://api.deepseek.com

kimi_API_key=sk-你的Kimi密钥
kimi_url=https://api.moonshot.cn/v1

zhi_pu_API_KEY=你的智谱AI密钥

xufei_APPID=你的讯飞APPID
xufei_APIKey=你的讯飞APIKey
xufei_APISecret=你的讯飞APISecret

REDIS_URL=redis://redis:6379
EOF
```

## 三、一键启动

前端在构建期会把 API 地址内联进产物，先把 `docker-compose.yml` 里 `web.build.args.NEXT_PUBLIC_API_BASE`
改成用户浏览器能访问到的地址（如 `http://你的服务器IP:8001` 或 `https://你的域名.com/api-host`），
并把同一地址加进 `api` 服务的 `FRONTEND_ORIGINS`。

```bash
cd /opt/smart_campus
docker-compose up -d
```

**首次启动**需要构建镜像（下载 Python 依赖 + 前端依赖），约 5-15 分钟。

会起三个服务：

| 服务 | 端口 | 说明 |
|---|---|---|
| `web` | 3000 | Next.js 前端 |
| `api` | 8001 | FastAPI 接口层 |
| `redis` | 6380 | Redis Stack |

## 四、验证

```bash
# 查看容器状态
docker-compose ps

# 查看日志
docker-compose logs -f api
docker-compose logs -f web

# 测试接口
curl.exe http://localhost:8001/api/health
```

浏览器打开：`http://你的服务器IP:3000`

需要旧版 Streamlit 界面时：`docker-compose --profile legacy up -d app`（8502）。

## 五、常用运维命令

| 操作 | 命令 |
|------|------|
| 启动 | `docker-compose up -d` |
| 停止 | `docker-compose down` |
| 重启 | `docker-compose restart` |
| 重新构建并启动 | `docker-compose up -d --build` |
| 查看状态 | `docker-compose ps` |
| 查看日志 | `docker-compose logs -f` |
| 只看接口日志 | `docker-compose logs -f api` |
| 只看前端日志 | `docker-compose logs -f web` |
| 只看 Redis 日志 | `docker-compose logs -f redis` |
| 进入应用容器 | `docker-compose exec app bash` |
| 进入 Redis 容器 | `docker-compose exec redis bash` |
| 清空 Redis | `docker-compose exec redis redis-cli FLUSHALL` |

## 六、更新部署

代码修改后，上传并重新构建：

```powershell
# 本地上传（PowerShell）
scp -r backend/ root@你的服务器IP:/opt/smart_campus/
```

```bash
# 服务器上重新构建并重启
cd /opt/smart_campus
docker-compose up -d --build
```

### 小内存服务器（阿里云 2 核 2G）

不要在服务器上 `--build`：编译前端会内存溢出，构建缓存也会撑满磁盘。改为 push 到 `master`（或在 Actions 页手动触发 `deploy-aliyun`），由 `.github/workflows/deploy-aliyun.yml` 在 GitHub 构建机上构建三个镜像，流式 `docker load` 到服务器，再 `docker-compose -f docker-compose.aliyun.yml up -d --no-build`，并清理旧镜像和构建缓存。

- 仓库 Secrets：`ECS_HOST`（如 `root@服务器IP`）、`ECS_SSH_KEY`（私钥）
- 服务器 `/opt/smart_campus/.env` 必须有 `PUBLIC_API_BASE`，构建前端时从这里读取

## 七、防火墙

```bash
# 前端与接口端口
sudo ufw allow 3000
sudo ufw allow 8001
```

## 八、配置 HTTPS（可选）

```bash
# 安装 nginx
sudo apt install nginx -y

cat > /etc/nginx/sites-available/smart_campus << 'EOF'
server {
    listen 80;
    server_name 你的域名.com;

    # 接口层：SSE 必须关缓冲，否则回答会攒到最后一次性吐出
    location /api/ {
        proxy_pass http://localhost:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

ln -s /etc/nginx/sites-available/smart_campus /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 申请 SSL 证书
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d 你的域名.com
```

前后端同域时，把 `web.build.args.NEXT_PUBLIC_API_BASE` 置为空字符串，前端会以相对路径 `/api/...`
请求，不再需要跨域配置：

```yaml
web:
  build:
    args:
      NEXT_PUBLIC_API_BASE: ""
```
