"""统一扫描层：扫描专题班/训练营，写入 StudyItem。

后续 v1.8 自动报名、v1.9 自动刷、v2.0 一键补齐都基于此表。
本阶段只做只读扫描，不触发报名/刷课。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from database import SessionLocal
from models import StudyItem
from services.ccb_api import CcbClient

logger = logging.getLogger(__name__)


def _to_float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _camp_action(rec: dict) -> str:
    """训练营动作判定。"""
    camp_status = rec.get("campStatus")
    join_flag = rec.get("joinFlag")
    approve_flag = rec.get("approveFlag")
    # 已结束
    if camp_status == 3:
        return "closed"
    # 需审批/无权限 → 跳过
    if approve_flag == 1:
        return "blocked"
    # 已报名且进行中 → 可学
    if join_flag == 1:
        return "ready"
    # 未报名 → 待报名
    return "join"


def _workshop_action(rec: dict) -> str:
    """专题班动作判定。

    专题班字段：studying(是否在学)、theStatus、openStatus、status。
    """
    status = rec.get("status")
    the_status = rec.get("theStatus")
    studying = rec.get("studying")
    open_status = rec.get("openStatus")

    # 已结束/未开放
    if status not in (1, "1") and open_status != 1:
        return "closed"
    if studying:
        return "ready"
    # 专题班通常需报名加入
    return "join"


async def _upsert(
    item_type: str, ref_id: str, title: str,
    available: float, obtained: float, progress: float,
    action: str, raw: dict
) -> None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(StudyItem).where(
                StudyItem.item_type == item_type,
                StudyItem.ref_id == ref_id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = StudyItem(item_type=item_type, ref_id=ref_id)
            session.add(item)
        item.category = "central"
        item.title = title
        item.available_hours = available
        item.obtained_hours = obtained
        item.progress = progress
        item.action = action
        item.raw = json.dumps(raw, ensure_ascii=False)
        await session.commit()


async def scan_all(client: CcbClient) -> dict:
    """扫描全部集中培训栏目，返回统计。"""
    stats = {"camp": 0, "workshop": 0,
             "ready": 0, "join": 0, "blocked": 0, "closed": 0}

    # 训练营
    try:
        camps = await client.fetch_camp_page()
    except Exception:
        logger.warning("训练营扫描失败", exc_info=True)
        camps = []
    for rec in camps:
        action = _camp_action(rec)
        await _upsert(
            "camp", str(rec.get("id")), str(rec.get("title")),
            _to_float(rec.get("knowledgeHours")),
            _to_float(rec.get("userKnowledgeHours")),
            _to_float(rec.get("process")),
            action, rec,
        )
        stats["camp"] += 1
        stats[action] = stats.get(action, 0) + 1

    # 专题班
    try:
        workshops = await client.fetch_workshop_home()
    except Exception:
        logger.warning("专题班扫描失败", exc_info=True)
        workshops = []
    for rec in workshops:
        action = _workshop_action(rec)
        await _upsert(
            "workshop", str(rec.get("id")), str(rec.get("name")),
            _to_float(rec.get("totalHours")),
            _to_float(rec.get("knowledgeHours")),
            _to_float(rec.get("progress")),
            action, rec,
        )
        stats["workshop"] += 1
        stats[action] = stats.get(action, 0) + 1

    return stats
