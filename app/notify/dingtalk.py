"""钉钉机器人通知（移植自 chat-etf-analyzer 的 DingTalkNotifier，剥除 ETF 耦合）。

签名协议：timestamp\nsecret → HMAC-SHA256 → base64 → urlencode。
密钥一律来自 .env（NOTIFY_ENABLED / DINGTALK_TOKEN / DINGTALK_SECRET），默认关。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)

WEBHOOK = "https://oapi.dingtalk.com/robot/send"


def _sign(secret: str) -> "tuple[str, str]":
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    return timestamp, sign


class DingTalkNotifier:
    def __init__(self, access_token: str, secret: str, timeout: int = 10):
        self.token = access_token
        self.secret = secret
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        ts, sign = _sign(self.secret) if self.secret else ("", "")
        params = {"access_token": self.token}
        if sign:
            params.update({"timestamp": ts, "sign": sign})
        try:
            r = requests.post(WEBHOOK, params=params, json=payload, timeout=self.timeout)
            return r.json()
        except Exception as e:
            logger.warning("钉钉发送失败: %s", e)
            return {"errcode": -1, "errmsg": str(e)}

    def send_text(self, msg: str) -> dict:
        return self._post({"msgtype": "text", "text": {"content": msg[:20000]}})

    def send_markdown(self, title: str, content: str) -> dict:
        return self._post({"msgtype": "markdown", "markdown": {"title": title[:20],
                                                                "text": content[:18000]}})


def get_notifier() -> "DingTalkNotifier | None":
    """根据 .env 开关返回通知器；未开启返回 None。"""
    if not settings.notify_enabled:
        return None
    if not settings.dingtalk_token:
        logger.warning("NOTIFY_ENABLED 已开但 DINGTALK_TOKEN 为空")
        return None
    return DingTalkNotifier(settings.dingtalk_token,
                            settings.dingtalk_secret or "")
