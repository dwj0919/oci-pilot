"""oci-pilot 主入口。

运行: uvicorn app.main:app --host 0.0.0.0 --port 9528
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR, TELEGRAM_BOT_TOKEN, PORT
from .db import init_db
from .logutil import install_log_redaction
from .notify import notify
from .oci import launch
from .monitor.jobs import start_scheduler, scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
install_log_redaction()  # 日志脱敏: 私钥 / Bot Token 进日志前自动打码
log = logging.getLogger("oci-pilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_log_redaction()  # 兜底: 覆盖 lifespan 之后才加上的 handler
    init_db()
    launch.set_notifier(notify)
    start_scheduler()
    bot_started = False
    if TELEGRAM_BOT_TOKEN:
        try:
            from .bot.bot import start_bot, stop_bot
            app.state.bot = await start_bot()
            bot_started = app.state.bot is not None
            app.state.bot_stop = stop_bot
        except Exception:  # noqa: BLE001
            log.exception("bot 启动失败, 继续运行 Web 服务")
    else:
        log.warning("未配置 TELEGRAM_BOT_TOKEN, Bot 不启动 —— "
                    "面板登录唯一方式是 Telegram 验证码, 不配则谁也无法登录!")
    await launch.resume_all()
    log.info("oci-pilot ready")
    yield
    scheduler.shutdown(wait=False)
    if bot_started:
        try:
            await app.state.bot_stop()
        except Exception:  # noqa: BLE001
            pass


app = FastAPI(title="oci-pilot", lifespan=lifespan,
              # 生产环境关闭公开文档: /docs 与 /openapi.json 会泄露全部接口结构
              docs_url=None, redoc_url=None, openapi_url=None)

from .routers import (auth, accounts, instances, network, volumes,
                      objectstore, console, email, cloudflare,
                      monitors, overview)

for r in (auth.router, accounts.router, instances.router, network.router,
          volumes.router, objectstore.router, console.router, email.router,
          cloudflare.router, monitors.router, overview.router):
    app.include_router(r)

try:
    from .routers.ssh import router as ssh_router
    app.include_router(ssh_router)
except ImportError:
    log.warning("ssh 路由未就绪, 跳过")


@app.get("/api/health")
def health():
    return {"ok": True}


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True),
              name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT)
