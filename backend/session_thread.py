"""会话 thread_id 解析：URL ?tid= 优先于本地 session。"""
from __future__ import annotations

import uuid
from typing import Optional, Tuple, Union


def normalize_tid(tid: Union[str, list, None]) -> Optional[str]:
    if tid is None:
        return None
    if isinstance(tid, list):
        tid = tid[0] if tid else None
    if tid is None:
        return None
    text = str(tid).strip()
    return text or None


def resolve_thread_id(
    current: Optional[str],
    url_tid: Union[str, list, None],
) -> Tuple[str, bool, bool]:
    """
    返回 (thread_id, should_restore_from_redis, should_sync_to_url)。

    规则:
      - URL 带 tid 时一律以 URL 为准；与 current 不同则触发 Redis 恢复
      - URL 无 tid 且已有 current：沿用 current，并写回 URL
      - 两者皆无：新建 uuid，并写回 URL
    """
    url = normalize_tid(url_tid)
    if url is not None:
        if current is None or current != url:
            return url, True, True
        return url, False, False
    if current:
        return current, False, True
    return str(uuid.uuid4()), False, True
