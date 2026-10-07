"""建行 Mac 签名复现：纯 SM3（无密钥），用经过验证的 gmssl 库。

前端 gmmac.min.js:
  function saltText(t,n){  // 按 t 长度把 n 插入特定位置
    t.length<=4 ? t+n :
    t.length<=8 ? t.substring(0,4)+n+t.substring(4) :
    t.length<=16? t.substring(0,8)+n+t.substring(8) :
    t.length<=32? t.substring(0,16)+n+t.substring(16) :
                  t.substring(0,32)+n+t.substring(32)}
  Mac(t,n,e,g,s):
    e = saltText(t+e+s+n+g, t)
    return SM3(utf8(e))
"""
from __future__ import annotations

from gmssl import sm3 as _sm3, func


def sm3_hex(data: bytes) -> str:
    return _sm3.sm3_hash(func.bytes_to_list(data))


def salt_text(t: str, n: str) -> str:
    L = len(t)
    if L <= 4:
        return t + n
    if L <= 8:
        return t[:4] + n + t[4:]
    if L <= 16:
        return t[:8] + n + t[8:]
    if L <= 32:
        return t[:16] + n + t[16:]
    return t[:32] + n + t[32:]


def mac(t: str, n: str, e: str, g: str, s: str) -> str:
    """对应前端 Mac(t=token, n=dataHash, e=nonce, g=s4, s=s5)。

    注意前端参数顺序 Mac(a,h,t,d,f)：a=token,h=dataHash,t=nonce,d=s4,f=s5，
    内部拼接 t+e+s+n+g = token+nonce+s5+dataHash+s4。
    """
    joined = t + e + s + n + g
    salted = salt_text(joined, t)
    return sm3_hex(salted.encode("utf-8"))
