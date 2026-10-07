"""构造建行请求签名头（复现 encryptionAjaxParams）。"""
from __future__ import annotations

import hashlib
import random
import time as _time

from services import sm3


def _rand_str() -> str:
    return (str(random.random())[2:12] + str(random.random())[2:13]
            + str(random.random())[2:13])[:30]


def _data_hash(data: object) -> str:
    """前端 s2 = JSON.stringify(data).slice(0,32)（mvHQ模块就是JSON.stringify）。"""
    import json, random as _r
    if data in (None, "", {}):
        return _rand_str()
    # 前端用默认 JSON.stringify（带空格? JS默认无空格，逗号后无空格）
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return raw[:32]


async def signed_headers(
    client, token: str, data: object | None = None
) -> dict:
    """client: CcbClient（用其底层http取服务器时间）。返回需附加的签名headers。"""
    t = _rand_str()                 # s3 随机串（前端变量名t）
    nonce = t[: random.randint(1, 31)]   # n
    # 服务器时间
    resp = await client._client.get(
        "https://api.u.ccb.com/v1/udp/open/server/time"
    )
    srv_ms = _parse_ms(resp)
    now_ms = int(_time.time() * 1000)

    s1 = token
    s2 = _data_hash(data)
    s3 = t
    s4 = int(srv_ms / 1000)
    s5 = int(now_ms / 1000)

    signature = sm3.mac(s1, s2, nonce, str(s4), str(s5))
    return {
        "s1": s1, "s2": s2, "s3": s3, "s4": str(s4), "s5": str(s5),
        "token": token, "nonce": nonce,
        "x-ts": str(now_ms), "timestamp": str(srv_ms),
        "signature": signature,
    }


def _parse_ms(resp) -> int:
    """server/time 返回可能是毫秒数字或 {data:...}。"""
    try:
        j = resp.json()
    except Exception:  # noqa: BLE001
        j = None
    val = j
    if isinstance(j, dict):
        val = j.get("data", j.get("datas", j))
    try:
        n = int(float(val))
    except (TypeError, ValueError):
        n = int(_time.time() * 1000)
    # 若是秒级则转毫秒
    if n < 10_000_000_000:
        n *= 1000
    return n
