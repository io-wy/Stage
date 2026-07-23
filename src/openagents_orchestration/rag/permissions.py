"""RAG 权限 tag — 最小规则版。

权限 tag 只做检索前的安全分层,不是最终权限系统。真正接入聊天入口时,仍应由
调用方按用户身份传入 required_tags,并在生成侧做敏感信息脱敏。
"""

from __future__ import annotations

_PERMISSION_TAGS = {"perm:public", "perm:internal", "perm:sensitive"}
_SENSITIVE_TERMS = (
    "password",
    "passwd",
    "secretkey",
    "secret",
    "token",
    "api key",
    "apikey",
    "密码",
    "账号",
    "密钥",
    "登录指南",
    "服务器登录",
    "内网",
    "反代",
)


def apply_permission_tags(tags: list[str], source: str, text: str) -> list[str]:
    """返回带唯一权限 tag 的 tags。

    规则优先级: sensitive > public > internal。敏感内容即使位于 Public 空间也按
    sensitive 处理。
    """

    base_tags = [tag for tag in tags if tag not in _PERMISSION_TAGS]
    permission = _permission_for(source, text)
    return [*base_tags, permission]


def _permission_for(source: str, text: str) -> str:
    haystack = f"{source}\n{text}".lower()
    if any(term in haystack for term in _SENSITIVE_TERMS):
        return "perm:sensitive"
    if "public" in haystack or "公开" in haystack:
        return "perm:public"
    return "perm:internal"
