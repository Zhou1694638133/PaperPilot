"""Small OpenAI-compatible transport; deliberately no automatic paid retries."""
from __future__ import annotations

import json
import os
import socket
import ssl
from urllib.parse import urlparse

import httpx

from .config import Settings, get_settings


class ModelError(Exception):
    def __init__(self, message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


class ModelOutputError(ModelError):
    """A complete response was received but its output failed validation."""


def connection_diagnostic(exc: Exception, trust_env: bool) -> str:
    """Classify transport failures without exposing URLs, credentials or response bodies."""
    chain, current = [], exc
    while current is not None and len(chain) < 8 and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in chain):
        code, reason = "tls_certificate", "TLS 证书验证失败"
    elif any(isinstance(item, ssl.SSLError) for item in chain):
        code, reason = "tls_handshake", "TLS 握手失败"
    elif any(isinstance(item, socket.gaierror) for item in chain):
        code, reason = "dns", "连接链路中的主机名解析失败"
    elif any(isinstance(item, ConnectionRefusedError) or getattr(item, "winerror", None) == 10061 for item in chain):
        code, reason = "connection_refused", "连接被拒绝"
    elif isinstance(exc, httpx.ConnectTimeout):
        code, reason = "connect_timeout", "建立连接超时"
    elif isinstance(exc, httpx.ProxyError):
        code, reason = "proxy", "代理连接失败"
    else:
        code, reason = "connect", "建立连接失败"
    route = "继承环境代理设置" if trust_env else "不继承环境代理设置"
    return f"模型请求未能建立连接：{reason}（{code}；{route}）。这不代表模型服务持续不可用；已有检查点保留，可手动继续。"


def request_json(system: str, data: dict, request_id: str, *, settings: Settings | None = None) -> tuple[dict, str | None, dict]:
    settings = settings or get_settings()
    if not settings.llm_api_key or not settings.llm_model:
        raise ModelError("模型未配置：请设置后端 LLM_API_KEY 和 LLM_MODEL。")
    base = settings.llm_base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ModelError("LLM_BASE_URL 格式无效，应为 OpenAI 兼容 API 的 http(s) 基地址。")
    token_parameter = os.getenv("LLM_TOKEN_PARAMETER", "max_tokens")
    if token_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise ModelError("LLM_TOKEN_PARAMETER 只能是 max_tokens 或 max_completion_tokens。")
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system + "\nReturn exactly one JSON object, without markdown fences."},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        token_parameter: max(128, min(settings.llm_max_tokens, 8192)),
    }
    # DeepSeek Flash now defaults to high-effort thinking, sharing the output budget.
    # Explicitly use non-thinking mode for bounded extraction/translation. Other
    # compatible providers must not receive this vendor-specific parameter.
    if parsed.hostname == "api.deepseek.com" and settings.llm_model in {"deepseek-flash", "deepseek-v4-pro"}:
        thinking = os.getenv("LLM_DEEPSEEK_THINKING", "disabled").lower()
        if thinking not in {"enabled", "disabled"}:
            raise ModelError("LLM_DEEPSEEK_THINKING 只能是 enabled 或 disabled。")
        payload["thinking"] = {"type": thinking}
    # Providers differ: JSON mode and temperature are explicitly opt-in.
    if os.getenv("LLM_JSON_MODE", "false").lower() == "true":
        payload["response_format"] = {"type": "json_object"}
    if os.getenv("LLM_SEND_TEMPERATURE", "false").lower() == "true":
        payload["temperature"] = 0.1
    environment_mode = os.getenv("LLM_TRUST_ENV", "true").strip().lower()
    if environment_mode not in {"true", "false"}:
        raise ModelError("LLM_TRUST_ENV 只能是 true 或 false。")
    trust_env = environment_mode == "true"
    try:
        with httpx.Client(trust_env=trust_env, timeout=max(1, min(settings.llm_timeout, 300)), follow_redirects=False) as client:
            response = client.post(base + "/chat/completions", json=payload, headers={
                "Authorization": "Bearer " + settings.llm_api_key,
                "Content-Type": "application/json", "X-Request-ID": request_id,
            })
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
        raise ModelError(connection_diagnostic(exc, trust_env)) from exc
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        raise ModelError(f"模型请求结果不确定（{type(exc).__name__}）；请核对提供方记录后再决定是否重试，可能重复计费。", ambiguous=True) from exc
    if not response.is_success:
        ambiguous = response.status_code >= 500
        messages = {
            400: "模型服务不接受当前请求参数，请检查 LLM_TOKEN_PARAMETER、LLM_JSON_MODE 或模型名称。",
            401: "模型服务拒绝凭据，请检查后端 LLM_API_KEY。",
            403: "当前凭据没有该模型的访问权限。",
            404: "模型或兼容 API 路径不存在，请检查 LLM_BASE_URL 与 LLM_MODEL。",
            429: "模型服务限流或配额不足；请稍后手动重试。",
        }
        raise ModelError(messages.get(response.status_code, f"模型服务返回 HTTP {response.status_code}，未取得有效结果。"), ambiguous=ambiguous)
    try:
        if len(response.content) > 2_000_000:
            raise ValueError("response too large")
        body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter"}:
            raise ModelOutputError("模型输出被截断或过滤，未作为完整结果保存；请调整 token 上限后重试。")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("non-text response")
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        output = json.loads(cleaned)
        if not isinstance(output, dict):
            raise ValueError("non-object response")
        usage = body.get("usage") or {}
        usage = {key: value for key, value in usage.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)}
        return output, body.get("id") or response.headers.get("x-request-id"), usage
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ModelOutputError("模型返回的内容不是有效 JSON 对象，未生成完整产物。兼容服务需支持文本生成并遵守 JSON 输出要求。") from exc
