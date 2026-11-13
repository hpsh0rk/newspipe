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
from urllib.parse import quote, urljoin, urlsplit
import zlib
from email.message import Message

DEFAULT_PROXY = "http://127.0.0.1:7890"


def proxy_url() -> str:
    """代理出口。默认 127.0.0.1:7890（Clash）；容器里用 `NEWSPIPE_PROXY` 显式覆盖。"""
    return (os.environ.get("NEWSPIPE_PROXY") or "").strip() or DEFAULT_PROXY


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class ResponseLimitError(ValueError):
    """A source response exceeded an opt-in byte/redirect budget."""


class ResponseFormatError(ValueError):
    """A bounded source encoding or redirect cannot be interpreted safely."""


class _LimitedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, limit):
        self.limit = limit
        self.count = 0
        self.max_repeats = self.max_redirections = limit + 1

    def http_error_302(self, req, fp, code, msg, headers):
        try:
            target = headers.get("location") or headers.get("uri")
            if not target:
                raise ResponseFormatError("Source redirect has no location.")
            if self.count >= self.limit:
                raise ResponseLimitError("Source redirect limit exceeded.")
            target = _redirect_url(req.full_url, target)
            redirected = self.redirect_request(req, fp, code, msg, headers, target)
            if redirected is None:
                raise ResponseFormatError("Source redirect is unsupported.")
            if not _same_origin(req.full_url, target):
                redirected.headers = _public_redirect_headers(redirected.headers)
            self.count += 1
        finally:
            # The inherited handler drains arbitrary redirect bodies before following.
            fp.close()
        return self.parent.open(redirected, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _redirect_url(current, target):
    url = urljoin(current, quote(target, safe="/:?&=#%+@!$'()*,-._~;[]"))
    parts = urlsplit(url)
    if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None):
        raise ResponseFormatError("Source redirect scheme is unsupported.")
    return url


def _same_origin(left, right):
    def origin(url):
        parts = urlsplit(url)
        return parts.scheme.lower(), parts.hostname, parts.port or (443 if parts.scheme.lower() == "https" else 80)
    return origin(left) == origin(right)


def _public_redirect_headers(headers):
    return {key: value for key, value in headers.items() if key.lower() in {"user-agent", "accept", "accept-encoding"}}


def _bounded_body(chunks, encoding, limit):
    encoding = encoding.strip().lower() or "identity"
    if encoding not in {"identity", "gzip", "deflate"}:
        raise ResponseFormatError("Source content encoding is unsupported.")
    decoder = zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding != "identity" else None
    parts, size, wire = [], 0, 0
    try:
        for chunk in chunks:
            wire += len(chunk)
            if wire > limit:
                raise ResponseLimitError("Source wire byte limit exceeded.")
            remaining = chunk
            while remaining:
                if decoder is not None and decoder.eof:
                    if encoding != "gzip":
                        raise ResponseFormatError("Source encoding has trailing data.")
                    decoder = zlib.decompressobj(31)
                output = decoder.decompress(remaining, limit - size + 1) if decoder else remaining
                size += len(output)
                if size > limit:
                    raise ResponseLimitError("Source decoded byte limit exceeded.")
                parts.append(output)
                remaining = decoder.unused_data if decoder else b""
        if decoder is not None and not decoder.eof:
            raise ResponseFormatError("Source encoding is incomplete.")
    except zlib.error:
        raise ResponseFormatError("Source encoding is invalid.") from None
    return b"".join(parts)


def _http1_chunks(response, limit):
    wire = 0
    while True:
        chunk = response.read(min(65536, limit - wire + 1))
        if not chunk:
            return
        wire += len(chunk)
        yield chunk


def _request_h2(url: str, proxy: str | None, headers: dict, timeout: float,
                *, max_bytes=None, max_redirects=None) -> bytes:
    """httpx + h2（ALPN 协商 HTTP/2）。异常统一转成 urllib 风格，调用方按同一套处理。"""
    import httpx

    try:
        options = {"max_redirects": max_redirects} if max_redirects is not None else {}
        bounded = max_bytes is not None or max_redirects is not None
        with httpx.Client(proxy=proxy, http2=True, headers={} if bounded else headers,
                          timeout=timeout, follow_redirects=not bounded, trust_env=False, **options) as client:
            if not bounded:
                resp = client.get(url)
                body = resp.content
            else:
                current, redirects, current_headers = url, 0, headers
                while True:
                    with client.stream("GET", current, headers=current_headers) as resp:
                        if resp.status_code in {301, 302, 303, 307, 308}:
                            if redirects >= (max_redirects if max_redirects is not None else 20):
                                raise ResponseLimitError("Source redirect limit exceeded.")
                            target = resp.headers.get("location")
                            if not target:
                                raise ResponseFormatError("Source redirect has no location.")
                            next_url = _redirect_url(current, target)
                            if not _same_origin(current, next_url):
                                current_headers = _public_redirect_headers(current_headers)
                            current = next_url
                            redirects += 1
                            continue
                        if resp.status_code >= 400:
                            raise urllib.error.HTTPError(current, resp.status_code, f"HTTP {resp.status_code}", Message(), None)
                        body = (_bounded_body(resp.iter_raw(chunk_size=65536), resp.headers.get("content-encoding", "identity"), max_bytes)
                                if max_bytes is not None else resp.read())
                        break
    except (ResponseLimitError, ResponseFormatError, urllib.error.HTTPError):
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
    if max_bytes is not None:
        hdr["Accept-Encoding"] = "identity"
    last: Exception | None = None
    # 两次尝试：先直连（8s 快超时——被墙的域直连表现为悬挂而非快速失败，别陪它耗），
    # 失败/被拒再显式走 Clash（ProxyHandler 同时屏蔽网关 env 里的残留代理变量）。
    for proxy, tmo in ((None, min(timeout, 8.0)), (proxy_url(), timeout)):
        if http2:
            try:
                return _request_h2(url, proxy, hdr, tmo, max_bytes=max_bytes, max_redirects=max_redirects)
            except (ResponseLimitError, ResponseFormatError):
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
        if max_redirects is not None or max_bytes is not None:
            handlers.append(_LimitedRedirect(max_redirects if max_redirects is not None else 10))
        try:
            with urllib.request.build_opener(*handlers).open(
                urllib.request.Request(url, data=data, headers=hdr, method=method),
                timeout=tmo) as resp:
                body = (_bounded_body(_http1_chunks(resp, max_bytes), resp.headers.get("Content-Encoding", "identity"), max_bytes)
                        if max_bytes is not None else resp.read())
                return body
        except (ResponseLimitError, ResponseFormatError):
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
