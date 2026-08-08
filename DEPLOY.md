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

```bash
cd /opt/smart_campus
docker-compose up -d
```

**首次启动**需要构建镜像（下载 Python 依赖），约 5-10 分钟。

## 四、验证

```bash
# 查看容器状态
docker-compose ps

# 查看应用日志
docker-compose logs -f app

# 测试访问
curl http://localhost:8502
```

浏览器打开：`http://你的服务器IP:8502`

## 五、常用运维命令

| 操作 | 命令 |
|------|------|
| 启动 | `docker-compose up -d` |
| 停止 | `docker-compose down` |
| 重启 | `docker-compose restart` |
| 重新构建并启动 | `docker-compose up -d --build` |
| 查看状态 | `docker-compose ps` |
| 查看日志 | `docker-compose logs -f` |
| 只看应用日志 | `docker-compose logs -f app` |
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

## 七、防火墙

```bash
# 开放 8502 端口
sudo ufw allow 8502
```

## 八、配置 HTTPS（可选）

```bash
# 安装 nginx
sudo apt install nginx -y

cat > /etc/nginx/sites-available/smart_campus << 'EOF'
server {
    listen 80;
    server_name 你的域名.com;

    location / {
        proxy_pass http://localhost:8502;
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
