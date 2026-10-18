# oci-pilot

甲骨文云 (OCI) 自托管管理面板：Web 云管理 + Web SSH 终端 + Telegram 机器人，**全部功能免费、开源 (MIT)**。

> 🤖 本项目全程由 [muse.ai](https://muse.ai)（Meta 的个人 AI 助手）协助完成：从需求分析、架构设计到全部代码、测试与部署文档，均由 Muse 与作者结对构建。

把 R-Bot 那类工具的 OCI 能力用原创代码实现了一遍：抢机、换 IP、保留启动盘删机、重装系统、流量守护、多 profile、从系统盘开机、对象存储、邮件域名、串行控制台、Cloudflare DNS、域名证书监控。API 私钥只存在你自己的服务器上，不经过任何第三方。

## 功能

| 模块 | 能力 |
|---|---|
| 实例管理 | 列表/详情/开机/关机/重启/终止（可选保留启动盘）/改名/弹性升降配 |
| 抢机 | 后台循环重试（随机延迟），缺货一直抢、配额不足自动停、成功 Telegram 推送 IP |
| 重装系统 | 收集原机参数 → 删机 → 自动抢机重开 |
| 从启动盘开机 | 拿未附加的启动盘直接开机（机器和数据保留） |
| 网络 | 换公网 IP、保留 IP（创建/绑定/解绑/删除）、IPv6 附加/删除、一键开放安全组端口 |
| 硬盘 | 数据卷/启动盘：创建/扩容/调性能/分离/挂回/删除 |
| 串行控制台 | SSH 连不上时的救命通道 + 控制台历史查看 |
| 对象存储 | Bucket 增删、文件上传/下载/删除 |
| 邮件服务 | 发信域名（返回 DKIM 记录）、发件人、SMTP 账号（密码只显示一次）、测试发信 |
| A1 体检 | 批量检查 ARM 实例是否超出免费额度（4C/24G） |
| 实例保活 | 意外停机 Telegram 通知 / 自动拉起 |
| 流量守护 | 按实例设月流出阈值，超了通知或自动关机 |
| 域名监控 | 域名 + SSL 证书到期 30/14/7/1 天分档提醒，可从 Cloudflare 一键导入 |
| Cloudflare | 域名解析增删改查、换 IP 自动更新 A 记录 |
| Web SSH | 浏览器终端（多标签）、SFTP 文件管理、在线编辑、一键从 OCI 同步主机 |
| Telegram Bot | 上面几乎所有能力都能在手机上操作（/instances /launch /tasks /quota /volumes /watch /traffic /domains /dns …） |
| 每日报告 | 每天 08:00 推送实例日报 |

## 快速开始

```bash
cd oci-pilot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 可选: 把配置写进项目根目录的 .env 文件(复制 .env.example 改名即可),
# 也支持直接 export 环境变量(环境变量优先于 .env)
cp .env.example .env   # 然后编辑 .env 填 TELEGRAM_BOT_TOKEN 等
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9528
```

浏览器打开 `http://你的IP:9528`，按页面上的首次部署指引（Telegram `/bind` + `/logincode`）登录即可。

Docker（推荐 compose，一行启动）：

```bash
cp .env.example .env   # 按需填 TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_IDS，不填也能跑
docker compose up -d --build
```

浏览器打开 `http://你的IP:9528`。数据持久化在 `./data`，升级重建容器不丢。

只用裸 Docker 也行：

```bash
docker build -t oci-pilot .
docker run -d -p 9528:9528 -v oci-pilot-data:/data \
  -e OCI_PILOT_DATA=/data \
  -e TELEGRAM_BOT_TOKEN="..." \
  --name oci-pilot oci-pilot
```

## 配置 OCI 账号

在「设置 → OCI 账号」里添加 profile，需要 5 样东西（甲骨文控制台 → 用户设置 → API 密钥处获取）：

- Tenancy OCID、User OCID、指纹、私钥 PEM、区域（如 `us-ashburn-1`）

支持多个 profile，可按账号走不同的代理（`https_proxy` 格式）。

## Telegram Bot 对接（唯一登录方式）

面板没有密码体系，登录只认 Telegram 验证码：

1. 找 @BotFather 建机器人，拿到 token，写进项目根目录 `.env` 文件的 `TELEGRAM_BOT_TOKEN`（或 export 环境变量）。
2. 给 @userinfobot 发任意消息，查到自己的 Telegram 数字 ID，写进 `.env` 的 `TELEGRAM_ALLOWED_IDS`（多个用逗号分隔），重启。不配则 Bot 拒绝所有人。
3. 给机器人发 `/bind` 创建面板账号（白名单用户自动开户，首次部署用它）。
4. 发 `/logincode` 拿 6 位码，在 Web 登录页输入即登录（5 分钟有效，登录成功后机器人自动删除这条验证码消息）。

## 数据与安全

- 所有数据（账号、私钥、SSH 密码）只存在本地 `data/` 目录，私钥类字段用 Fernet 加密存储。
- 面板无密码体系，登录为 Telegram 验证码 + JWT；`.env`（含 Bot Token）与 `data/` 均已加入 `.gitignore`，不会随代码泄露。
- 备份 `data/` 目录即备份一切；`.secret` 丢了则加密字段无法解密。

## 项目结构

```
app/
  main.py            入口 (FastAPI + lifespan)
  config.py db.py security.py deps.py notify.py
  cloudflare.py      Cloudflare DNS
  oci/               OCI 封装: base/compute/network/volumes/identity/
                     objectstore/metrics/console/email/launch(抢机引擎)
  routers/           REST API
  bot/               Telegram Bot
  ssh/               Web SSH 会话管理 + SFTP
  monitor/           调度器: 保活/流量守护/域名证书/日报
web/                 前端 SPA
```

## 开源协议

本项目采用 [MIT License](LICENSE) 开源：可自由使用、复制、修改、合并、发布、分发（含商用），唯一要求是在副本中保留版权声明与协议原文。详见项目根目录的 `LICENSE` 文件。

## 免责

本项目为原创开源实现，仅供学习研究。请遵守甲骨文云服务条款与当地法律法规；「抢机」「强开」类操作请自行评估账号风险。
