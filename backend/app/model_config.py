"""Local model settings. Secrets are write-only and never included in API responses."""
from dataclasses import replace
import json
import os
import tempfile
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .config import get_settings, model_config_path, stored_model_config
from . import llm

router = APIRouter(prefix="/api/model-config", tags=["model configuration"])
_save_lock = Lock()
_test_lock = Lock()


class ModelConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    clear_key: bool = False
    revision: str = Field(max_length=100)


def public_config() -> dict:
    saved = stored_model_config()
    settings = get_settings()
    return {"base_url": settings.llm_base_url, "model": settings.llm_model,
            "has_api_key": bool(settings.llm_api_key),
            "configured": bool(settings.llm_api_key and settings.llm_model),
            "revision": saved["revision"] if saved else "environment",
            "source": "backend" if saved else "environment"}


async def parse_input(request: Request) -> ModelConfigInput:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "请使用 JSON 提交模型配置。")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16000:
            raise HTTPException(413, "模型配置内容过长。")
    try:
        return ModelConfigInput.model_validate_json(bytes(body))
    except ValidationError:
        # FastAPI's default validation response includes submitted values; never echo keys.
        raise HTTPException(422, "配置格式无效，请检查 API 地址、模型名称和密钥字段。") from None


def candidate(data: ModelConfigInput):
    current = public_config()
    if data.revision != current["revision"]:
        raise HTTPException(409, "模型配置已被其他页面修改，请重新加载配置后再提交。")
    base = data.base_url.strip().rstrip("/")
    model = data.model.strip()
    key = data.api_key.get_secret_value().strip() if data.api_key else ""
    try:
        parsed = urlsplit(base)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                 and not any(char.isspace() or ord(char) < 32 for char in base))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "API 地址必须是无账号、查询参数或片段的 http(s) 基地址。")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(422, "远程模型地址请使用 HTTPS；HTTP 仅用于本机模型服务。")
    if base.endswith("/chat/completions"):
        raise HTTPException(422, "请填写 API 基地址（如 /v1），不要包含 /chat/completions。")
    if not model or any(ord(char) < 32 for char in model) or len(key) > 8192 or any(not 33 <= ord(char) <= 126 for char in key):
        raise HTTPException(422, "模型名称或密钥格式无效。")
    if data.clear_key and key:
        raise HTTPException(422, "清除密钥时请将密钥输入框留空。")
    settings = get_settings()
    if not key and not data.clear_key and settings.llm_api_key and base != settings.llm_base_url.rstrip("/"):
        raise HTTPException(422, "更换 API 地址时请重新填写对应密钥，或选择清除密钥；不会向新地址发送旧密钥。")
    return replace(settings, llm_base_url=base, llm_model=model,
                   llm_api_key="" if data.clear_key else key or settings.llm_api_key)


@router.get("")
def read_config():
    return public_config()


def save_config(data: ModelConfigInput) -> dict:
    with _save_lock:
        settings = candidate(data)
        path = model_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(prefix=".model-config-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"base_url": settings.llm_base_url, "model": settings.llm_model,
                           "api_key": settings.llm_api_key, "revision": str(uuid4())}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return public_config()


@router.put("")
async def update_config(request: Request):
    data = await parse_input(request)
    # File I/O is small; no remote call and the replace is atomic for the worker.
    return save_config(data)


def test_config(data: ModelConfigInput) -> dict:
    settings = candidate(data)
    if not settings.llm_api_key:
        raise HTTPException(422, "请填写密钥，或先保存后端模型密钥。")
    if not _test_lock.acquire(blocking=False):
        raise HTTPException(409, "已有连接测试正在执行，请等待结果。")
    try:
        output, _, usage = llm.request_json("Connection test. Return {\"ok\":true}.", {"test": True},
            "paperpilot-connection-" + str(uuid4()),
            settings=replace(settings, llm_timeout=20, llm_max_tokens=128))
        if output.get("ok") is not True:
            raise HTTPException(502, "服务已响应，但未遵守 JSON 测试输出要求，暂不能确认兼容。")
        return {"ok": True, "message": "连接成功：模型完成了真实 JSON 生成测试。配置尚未因此保存。", "usage": usage}
    except llm.ModelError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        _test_lock.release()


@router.post("/test")
async def connection_test(request: Request):
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(test_config, await parse_input(request))
