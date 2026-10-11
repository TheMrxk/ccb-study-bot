"""数据模型：Account(建行账号) / Course / Video / StudyItem / TaskLog。

身份设计：不设独立面板账号，直接以建行用户名作为唯一身份。
课程、视频按 owner_ccb（建行用户名）归属，不同建行账号数据互相隔离。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Account(Base):
    """建行账号：票据按建行用户名存取。"""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    token: Mapped[str] = mapped_column(String(512), default="")
    user_id: Mapped[str] = mapped_column(String(128), default="")
    org_id: Mapped[str] = mapped_column(String(128), default="")
    extra: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="inactive")
    token_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("owner_ccb", "package_id", name="uq_course_owner_pkg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_ccb: Mapped[str] = mapped_column(String(128), index=True)
    package_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(256), default="")
    total_videos: Mapped[int] = mapped_column(Integer, default=0)
    # kind: course=课程包/视频(可刷) live=直播 workshop=专题班
    kind: Mapped[str] = mapped_column(String(16), default="course")
    parent_course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id"), nullable=True, index=True
    )
    live_sessions: Mapped[str] = mapped_column(Text, default="")
    workshop_meta: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    videos: Mapped[list["Video"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_ccb: Mapped[str] = mapped_column(String(128), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    knowledge_id: Mapped[str] = mapped_column(String(128), index=True)
    package_id: Mapped[str] = mapped_column(String(128), index=True)
    user_knowledge_id: Mapped[str] = mapped_column(String(128), default="")
    master_id: Mapped[str] = mapped_column(String(128), default="")
    parent_id: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    learned_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    # pending / queued / running / done / failed / paused
    status: Mapped[str] = mapped_column(String(32), default="pending")
    is_must: Mapped[int] = mapped_column(Integer, default=0)
    file_id: Mapped[str] = mapped_column(String(128), default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    error: Mapped[str] = mapped_column(Text, default="")

    course: Mapped[Course] = relationship(back_populates="videos")


class StudyItem(Base):
    """扫描层：专题班/训练营扁平记录（辅助）。"""
    __tablename__ = "study_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_type: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(16), index=True, default="central")
    ref_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    available_hours: Mapped[float] = mapped_column(Float, default=0.0)
    obtained_hours: Mapped[float] = mapped_column(Float, default=0.0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    action: Mapped[str] = mapped_column(String(16), default="ready")
    raw: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    view_schedule: Mapped[float] = mapped_column(Float, default=0.0)
    time_span: Mapped[float] = mapped_column(Float, default=0.0)
    times: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(String(32), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Announcement(Base):
    """公告已读状态：单行记录。seen_version=已弹过的版本，seen=0未弹/1已弹。

    seen_version 与当前 APP_VERSION 不一致时自动视为未弹（发版无需手动重置）。
    """
    __tablename__ = "announcement"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seen_version: Mapped[str] = mapped_column(String(32), default="")
    seen: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
