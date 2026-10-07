"""课程包同步：课程详情 -> 解析真 packageId/masterId -> 拉视频、pagesize 时长、入库。"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from database import SessionLocal
from models import Course, Video
from services.ccb_api import CcbClient

logger = logging.getLogger(__name__)


def parse_pagesize(ps) -> float:
    """'00:24:18'/'24:18' -> 秒；纯数字原样。"""
    if ps is None:
        return 0.0
    s = str(ps).strip()
    if not s:
        return 0.0
    if ":" in s:
        try:
            parts = [float(x) for x in s.split(":")]
        except ValueError:
            return 0.0
        sec = 0.0
        for p in parts:
            sec = sec * 60 + p
        return sec
    try:
        return float(s)
    except ValueError:
        return 0.0


async def sync_course(client: CcbClient, course_id: str, title: str = "",
                      owner_ccb: int | None = None,
                      kind: str = "course") -> tuple[str, int]:
    """course_id 为详情页 UUID。自动识别课程包 / 独立视频。

    返回 (packageId 或课程UUID, 视频数)。kind: course/case。
    """
    detail = await client.fetch_course_detail(course_id)
    package_id = str(detail.get("sourceId") or "")
    master_id = str(detail.get("masterId") or "")
    api_title = str(detail.get("title") or "")
    course_title = api_title or title or course_id

    ktype = str(detail.get("knowledgeType") or "")
    ftype = str(detail.get("fileType") or "")
    is_video = ("Video" in ktype or "Video" in ftype) and not package_id

    # ---- 独立单视频：自己构成一个"课程+单视频" ----
    if is_video:
        duration = parse_pagesize(detail.get("pagesize"))
        if duration <= 0:
            try:
                duration = float(detail.get("standardStudyHours") or 0) * 60
            except (TypeError, ValueError):
                duration = 0
        learned = _to_float(detail.get("studyschedule"))

        # 调独立学习初始化，拿 userKnowledgeId（记账钥匙）与真实进度/状态
        single = {}
        try:
            single = await client.init_single_study(course_id)
        except Exception:  # noqa: BLE001
            logger.warning("init_single_study 失败，继续以详情信息兜底")
        ukid = str(single.get("userKnowledgeId") or "")
        single_sched = _to_float(single.get("studyschedule"))
        single_status = str(single.get("status") or "")
        # 独立视频 studyschedule 真机返回的是百分比(0-100)，换算成秒
        single_seconds = single_sched * duration / 100.0 if (duration and single_sched <= 100) else single_sched
        learned = max(learned, single_seconds)

        async with SessionLocal() as session:
            course = (await session.execute(
                select(Course).where(Course.package_id == course_id,
                                     Course.owner_ccb == owner_ccb)
            )).scalar_one_or_none()
            if course is None:
                course = Course(package_id=course_id, title=course_title,
                                owner_ccb=owner_ccb, kind=kind)
                session.add(course)
            else:
                course.title = course_title
                course.owner_ccb = owner_ccb
                course.kind = kind
            await session.flush()

            video = (await session.execute(
                select(Video).where(Video.knowledge_id == course_id,
                                    Video.owner_ccb == owner_ccb)
            )).scalar_one_or_none()
            if video is None:
                video = Video(course_id=course.id, knowledge_id=course_id,
                              package_id="", owner_ccb=owner_ccb)
                session.add(video)

            video.user_knowledge_id = ukid
            video.master_id = ""
            video.parent_id = course_id
            video.title = course_title
            video.file_id = str(detail.get("fileid") or detail.get("fileId") or "")
            if duration > 0:
                video.duration = duration
            if learned > 0:
                video.learned_seconds = learned
            # 独立视频完成判据：以真实学时为准。
            # 平台 status 可能在 0 进度时谎报完成，故有时长必须学时达标；
            # 仅在拿不到时长时才用 status 兜底。
            if duration > 0:
                is_done = learned >= duration - 1
            else:
                is_done = single_status in (
                    "Approved", "Completed", "Finish", "已完成"
                )
            if is_done:
                video.status = "done"
            course.total_videos = 1
            await session.commit()
        return course_id, 1

    if not package_id:
        raise RuntimeError("无法识别的内容类型（既非课程包也非视频）")

    items = await client.fetch_videos(package_id, master_id)

    async with SessionLocal() as session:
        result = await session.execute(select(Course).where(
            Course.package_id == package_id,
            Course.owner_ccb == owner_ccb,
        ))
        course = result.scalar_one_or_none()
        if course is None:
            course = Course(package_id=package_id, title=course_title,
                            owner_ccb=owner_ccb, kind=kind)
            session.add(course)
        else:
            course.title = course_title
            course.owner_ccb = owner_ccb
            course.kind = kind
        await session.flush()

        existing = {
            v.knowledge_id: v
            for v in (
                await session.execute(select(Video).where(
                    Video.package_id == package_id,
                    Video.owner_ccb == owner_ccb,
                ))
            ).scalars()
        }

        count = 0
        for raw in items:
            kid = str(raw.get("id") or raw.get("knowledgeId") or "")
            if not kid:
                continue

            learned = _to_float(raw.get("studySchedule"))
            status_raw = str(raw.get("status") or "")
            status_done = status_raw in (
                "Completed", "Finish", "StudyCompleted", "已完成",
            )

            video = existing.get(kid)
            if video is None:
                video = Video(course_id=course.id, knowledge_id=kid,
                              package_id=package_id, owner_ccb=owner_ccb)
                session.add(video)

            duration = parse_pagesize(raw.get("pagesize"))
            video.user_knowledge_id = str(raw.get("userKnowledgeId") or "")
            video.master_id = master_id
            video.parent_id = course_id
            video.title = str(raw.get("title") or kid)
            video.file_id = str(raw.get("fileid") or raw.get("fileId") or "")
            video.is_must = 1 if raw.get("ismuststudy") in (True, 1, "1", "true") else 0
            if duration > 0:
                video.duration = duration
            if learned > 0:
                video.learned_seconds = learned
            # 完成判据以真实学时(秒)为准：有时长必须达标，防止 status 谎报；
            # 无时长信息才用 status 兜底。百分比子站走独立视频路径处理。
            if duration > 0:
                is_done = learned >= duration - 1
            else:
                is_done = status_done
            if is_done:
                video.status = "done"
            count += 1

        course.total_videos = count
        await session.commit()
    return package_id, count


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def sync_workshop(
    client: CcbClient, workshop_id: str, title: str = "",
    owner_ccb: int | None = None
) -> tuple[str, int]:
    """专题班：建父记录，contentList 各项复用 sync_course 并挂到父记录。

    返回 (workshop_id, 子项数)。
    """
    detail = await client.fetch_workshop_detail(workshop_id)
    name = str(detail.get("name") or title or workshop_id)
    content = detail.get("contentList") or []

    # 顶层摘要
    meta = {
        "courseCount": detail.get("courseCount"),
        "compulsoryCount": detail.get("compulsoryCount"),
        "electiveCount": detail.get("electiveCount"),
        "totalHours": detail.get("totalHours"),
        "progress": detail.get("progress"),
        "totalScore": detail.get("totalScore"),
    }

    async with SessionLocal() as session:
        parent = (await session.execute(
            select(Course).where(Course.package_id == workshop_id,
                                 Course.owner_ccb == owner_ccb)
        )).scalar_one_or_none()
        if parent is None:
            parent = Course(package_id=workshop_id, title=name,
                            owner_ccb=owner_ccb)
            session.add(parent)
        parent.title = name
        parent.owner_ccb = owner_ccb
        parent.kind = "workshop"
        parent.workshop_meta = json.dumps(meta, ensure_ascii=False)
        await session.flush()
        parent_pk = parent.id
        await session.commit()

    # 逐项展开（复用已有逻辑：视频项→单视频课程，包项→多视频课程）
    child_count = 0
    for item in content:
        kid = str(item.get("knowledgeId") or "")
        if not kid:
            continue
        item_name = str(item.get("knowledgeName") or kid)
        await sync_course(client, kid, item_name, owner_ccb=owner_ccb)
        child_count += 1

        # 把刚同步的子课程挂到专题班
        async with SessionLocal() as session:
            child = (await session.execute(
                select(Course).where(Course.package_id == str(item.get("sourceid") or kid),
                                     Course.owner_ccb == owner_ccb)
            )).scalar_one_or_none()
            # 课程包 sync_course 后记录的 package_id 是其 sourceId；视频项是 kid
            if child is None:
                child = (await session.execute(
                    select(Course).where(Course.package_id == kid,
                                         Course.owner_ccb == owner_ccb)
                )).scalar_one_or_none()
            if child is not None:
                child.parent_course_id = parent_pk
                await session.commit()

    return workshop_id, child_count
