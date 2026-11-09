# oci-pilot

甲骨文云 (OCI) 自托管管理面板：Web 云管理 + Web SSH 终端 + Telegram 机器人，**全部功能免费、开源 (MIT)**。

## English summary

oci-pilot is a free, open-source (MIT), self-hosted management panel for Oracle Cloud Infrastructure (OCI). It reimplements the OCI feature set of tools like R-Bot with fully original code: instance management, a launch-retry ("capacity hunter") engine, image reinstall, terminate-while-keeping-the-boot-volume, public IP swap, multi-profile support, launching from an existing boot volume, object storage, serial console access, Web SSH/SFTP, a server-ops toolbox (fail2ban, key-only login, verified SSH port change, swap, BBR, timezone, system update), a traffic guard, an instance watchdog, and a Telegram bot. Your OCI API keys never leave your own server.

> 🤖 本项目全程由 [muse.ai](https://muse.ai)（Meta 的个人 AI 助手）协助完成：从需求分析、架构设计到全部代码、测试与部署文档，均由 Muse 与作者结对构建。

把 R-Bot 那类工具的 OCI 能力用原创代码实现了一遍：抢机、换 IP、保留启动盘删机、重装系统、流量守护、多 profile、从系统盘开机、对象存储、串行控制台。本项目只专注 OCI 管理本身，不做邮件、域名等周边功能。API 私钥只存在你自己的服务器上，不经过任何第三方。

## 与 R-Bot 的关系声明

本项目是学习 [R-Bot](https://github.com/semicons/java_oci_manage)（一款 OCI 多功能管理工具）后的**学习型复刻作品**：

- 功能思路参考了 R-Bot（含其付费版的功能清单），但**全部代码均为原创实现**，未复制 R-Bot 的任何源码；
- **不是**对 R-Bot 付费功能的破解、解锁或盗版，与 R-Bot 作者无任何关联，亦非其官方或分支版本；
- 如 R-Bot 作者认为本项目有任何不妥，欢迎联系，作者会第一时间积极处理。

## 功能

| 模块 | 能力 |
|---|---|
| 实例管理 | 列表/详情/开机/关机/重启/终止（可选保留启动盘）/改名/弹性升降配 |
| 抢机 | 后台循环重试（随机延迟），缺货一直抢、配额不足自动停、成功 Telegram 推送 IP |
| 重装系统 | 收集原机参数 → 删机 → 自动抢机重开 |
| 从启动盘开机 | 拿未附加的启动盘直接开机（机器和数据保留） |
| 网络 | 换公网 IP、保留 IP（创建/绑定/解绑/删除）、IPv6 附加/删除、一键开放安全组端口 |
| 硬盘 | 数据卷/启动盘：创建/扩容/调性能/分离/挂回/删除 |
| 串行控制台 | SSH 连不上时的救命通道 + 控制台历史查看；另有浏览器图形控制台：点开新窗口经双跳 SSH 隧道直连实例 VNC 画面（noVNC），Windows 实例救机首选。[串行控制台教程](docs/serial-console.md)（含抓 GRUB 编辑启动项、grub> 命令行手动启动、改密码实战） |
| 对象存储 | Bucket 增删、文件上传/下载/删除 |
| A1 体检 | 批量检查 ARM 实例是否超出免费额度（4C/24G） |
| 实例保活 | 意外停机 Telegram 通知 / 自动拉起 |
| 流量守护 | 按实例设月流出阈值，超了通知或自动关机 |
| Web SSH | 浏览器终端（多标签）、SFTP 文件管理、在线编辑、一键从 OCI 同步主机 |
| 服务器运维 | 固定模板一键执行并全程记录日志：fail2ban 防爆破、密钥登录（可关密码登录，先实测密钥能登录才关）、改 SSH 端口（双端口过渡 + 实测通过才关旧口 + OCI 安全列表同步放行）、添加 Swap、开启 BBR、设置时区、系统更新 |
| Telegram Bot | 上面几乎所有能力都能在手机上操作（/instances /launch /tasks /quota /volumes /watch /traffic /daily …） |
| 每日报告 | 每天 08:00 推送实例日报 |

## 快速开始

```bash
cd oci-pilot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# 可选: 把配置写进项目根目录的 .env 文件(复制 .env.example 改名即可),
# 也支持直接 export 环境变量(环境变量优先于 .env)
cp .env.example .env   # 然后编辑 .env 填写 TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_IDS (必填, 否则无法登录)
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9528
```

浏览器打开 `http://你的IP:9528`，按页面上的首次部署指引（Telegram `/bind` + `/logincode`）登录即可。

Docker（推荐 compose，一行启动）：

```bash
cp .env.example .env   # 填写 TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_IDS (必填, 否则无法登录)
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
  oci/               OCI 封装: base/compute/network/volumes/identity/
                     objectstore/metrics/console/launch(抢机引擎)
  routers/           REST API
  bot/               Telegram Bot
  ssh/               Web SSH 会话管理 + SFTP
  ops/               运维动作模板库 + 任务执行器
  monitor/           调度器: 保活/流量守护/日报
web/                 前端 SPA
```

## 开源协议

本项目采用 [MIT License](LICENSE) 开源：可自由使用、复制、修改、合并、发布、分发（含商用），唯一要求是在副本中保留版权声明与协议原文。详见项目根目录的 `LICENSE` 文件。

## 免责

本项目为原创开源实现，仅供学习研究。请遵守甲骨文云服务条款与当地法律法规；「抢机」「强开」类操作请自行评估账号风险。
