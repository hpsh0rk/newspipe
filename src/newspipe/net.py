"""资讯管线 v2 —— HTTP 出口层。

境内现实：HN/AIHOT 直连通，Reddit/linux.do/x.com 必须走代理。cron 环境 PATH/env 都不可信，
所以代理地址**默认写死**（127.0.0.1:7890）而不是从 env 猜；403/429/5xx 也换出口重试一次。
唯一例外是 `NEWSPIPE_PROXY`：容器里宿主回环地址不是 `127.0.0.1`（要用
`host.docker.internal`），这是**部署事实**，只能由部署显式给，不能让代码猜。

另有一条 HTTP/2 出口（`http2=True`）：Cloudflare Bot Management 按 HTTP 版本放行，同一出口下
HTTP/1.1 一律 403、h2 才 200（linux.do 的 tag feed 实测）。信源在 sources.yaml 里声明
`http2: true` 即走这条出口，不改全局默认。
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from email.message import Message

DEFAULT_PROXY = "http://127.0.0.1:7890"


def proxy_url() -> str:
    """代理出口。默认 127.0.0.1:7890（Clash）；容器里用 `NEWSPIPE_PROXY` 显式覆盖。"""
    return (os.environ.get("NEWSPIPE_PROXY") or "").strip() or DEFAULT_PROXY


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class ResponseLimitError(ValueError):
    """A source response exceeded an opt-in byte/redirect budget."""


class _LimitedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, limit):
        self.limit = limit
        self.count = 0
        self.max_repeats = self.max_redirections = limit + 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.count >= self.limit:
            fp.close()
            raise ResponseLimitError("Source redirect limit exceeded.")
        self.count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request_h2(url: str, proxy: str | None, headers: dict, timeout: float,
                *, max_bytes=None, max_redirects=None) -> bytes:
    """httpx + h2（ALPN 协商 HTTP/2）。异常统一转成 urllib 风格，调用方按同一套处理。"""
    import httpx

    try:
        options = {"max_redirects": max_redirects} if max_redirects is not None else {}
        with httpx.Client(proxy=proxy, http2=True, headers=headers,
                          timeout=timeout, follow_redirects=True, **options) as client:
            if max_bytes is None:
                resp = client.get(url)
                body = resp.content
            else:
                with client.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        raise urllib.error.HTTPError(url, resp.status_code, f"HTTP {resp.status_code}", Message(), None)
                    parts, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ResponseLimitError("Source response byte limit exceeded.")
                        parts.append(chunk)
                    body = b"".join(parts)
    except (ResponseLimitError, urllib.error.HTTPError):
        raise
    except httpx.TooManyRedirects:
        if max_redirects is not None:
            raise ResponseLimitError("Source redirect limit exceeded.") from None
        raise urllib.error.URLError("Too many redirects.") from None
    except Exception as exc:  # 连接层失败（timeout/reset/DNS/TLS）
        raise urllib.error.URLError(str(exc)) from exc
    if resp.status_code >= 400:
        raise urllib.error.HTTPError(url, resp.status_code, f"HTTP {resp.status_code}",
                                     Message(), None)
    return body


def request(url: str, method: str = "GET", data: bytes | None = None,
            headers: dict | None = None, timeout: float = 30,
            http2: bool = False, *, max_bytes: int | None = None,
            max_redirects: int | None = None) -> bytes:
    if (max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1)
            or max_redirects is not None and (type(max_redirects) is not int or max_redirects < 0)):
        raise ValueError("Source limits must be positive bytes and nonnegative redirect counts.")
    hdr = {"User-Agent": _UA}
    if headers:
        hdr.update(headers)
    last: Exception | None = None
    # 两次尝试：先直连（8s 快超时——被墙的域直连表现为悬挂而非快速失败，别陪它耗），
    # 失败/被拒再显式走 Clash（ProxyHandler 同时屏蔽网关 env 里的残留代理变量）。
    for proxy, tmo in ((None, min(timeout, 8.0)), (proxy_url(), timeout)):
        if http2:
            try:
                return _request_h2(url, proxy, hdr, tmo, max_bytes=max_bytes, max_redirects=max_redirects)
            except ResponseLimitError:
                raise
            except urllib.error.HTTPError as exc:
                last = exc
                exc.close()
                if proxy is None and exc.code in (401, 403, 429, 503):
                    continue  # 换出口可能不同结果
                raise
            except Exception as exc:  # 连接层失败（timeout/reset/DNS/TLS）
                last = exc
                continue
        handlers = [urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {})]
        if max_redirects is not None:
            handlers.append(_LimitedRedirect(max_redirects))
        try:
            with urllib.request.build_opener(*handlers).open(
                urllib.request.Request(url, data=data, headers=hdr, method=method),
                timeout=tmo) as resp:
                body = resp.read(max_bytes + 1) if max_bytes is not None else resp.read()
                if max_bytes is not None and len(body) > max_bytes:
                    raise ResponseLimitError("Source response byte limit exceeded.")
                return body
        except ResponseLimitError:
            raise
        except urllib.error.HTTPError as exc:
            last = exc
            exc.close()
            if proxy is None and exc.code in (401, 403, 429, 503):
                continue  # 换出口可能不同结果
            raise
        except Exception as exc:  # 连接层失败（timeout/reset/DNS）
            last = exc
            continue
    assert last is not None
    raise last


def html_to_text(html: str, *, limit: int = 6000) -> str:
    """把 HTML 正文抽成纯文本（够喂模型即可，不求完美）。

    stdlib 的 HTMLParser：丢掉 script/style/nav/header/footer/aside 等噪声块，保留可见文本，
    压平空白。抽不出内容时返回空串——调用方据此降级为「只用标题」，而不是编造正文。
    """
    from html.parser import HTMLParser

    drop = {"script", "style", "noscript", "nav", "header", "footer", "aside",
            "form", "svg", "template", "figure", "iframe"}
    keep_block = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                  "blockquote", "tr", "section", "article", "pre"}

    class _Extract(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in drop:
                self.depth += 1
            elif tag in keep_block and self.depth == 0:
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in drop and self.depth:
                self.depth -= 1
            elif tag in keep_block and self.depth == 0:
                self.parts.append("\n")

        def handle_data(self, data):
            if self.depth == 0:
                self.parts.append(data)

    parser = _Extract()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""
    text = " ".join("".join(parser.parts).split())
    return text[:limit]
