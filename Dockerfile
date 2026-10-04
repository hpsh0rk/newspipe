# newspipe 常驻服务（采集 + 加工 + 发卡 + 只读视图）。
#
# 为什么容器化：把引擎与宿主机的 Python 环境隔开，并且让「clone 下来就能跑」成立。
# 容器里**没有** macOS 钥匙串，所以凭据走 dotenv（挂宿主的 .env 进来，见 compose.yaml）。
FROM python:3.12-slim

# 只装运行期需要的东西：httpx[http2] 供 `http2: true` 的 CF 站点与代理出口。
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[h2]' \
    && useradd --create-home --uid 10001 newspipe

# 数据目录 = 挂进来的 Vault 侧 info/news（配置 + state），容器里固定 /data/news
ENV NEWSPIPE_NEWS_DIR=/data/news \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

USER newspipe
EXPOSE 8787

# 健康检查打 `/`：它证明「HTTP 线程活着」「配置能加载」「视图能渲染」——
# 比 ping 一个端口有意义得多（端口通但配置坏了 = 面板全红却查不出原因）。
# （不证明 state 可写：单个源没跑过是正常状态，缺文件不该判不健康。）
# 打 `/` 而不是 `/view`：`/view` 的路径来自 `service.yaml: view.path`，写死在这里会随配置漂移；
# `/` 是不可配的根路径（同一份 builder 渲染），配置坏时返回 503 → urlopen 抛错 → 退出码非 0。
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/', timeout=8).status == 200 else 1)"

CMD ["python", "-m", "newspipe.cli", "serve"]
