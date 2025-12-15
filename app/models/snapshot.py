from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.session import Base


class Snapshot(Base):
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    params = Column(JSONB, nullable=False, default=dict)

    videos = relationship("SnapshotVideo", back_populates="snapshot", cascade="all, delete-orphan")


class SnapshotVideo(Base):
    __tablename__ = "snapshot_videos"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False)
    video_id = Column(String(32), nullable=False)
    title = Column(Text, nullable=False)
    channel_title = Column(String(255), nullable=False)
    keyword = Column(String(255), nullable=False)
    published_at = Column(DateTime, nullable=False)
    duration_min = Column(Float, nullable=False)
    views = Column(Integer, nullable=False)
    views_per_day = Column(Float, nullable=False)
    engagement_pct = Column(Float, nullable=False)
    subs = Column(Integer, nullable=True)
    views_to_subs = Column(Float, nullable=True)
    score = Column(Float, nullable=False)
    rating = Column(Float, nullable=False)
    trend_score = Column(Float, nullable=False)
    url = Column(String(500), nullable=False)
    thumb_url = Column(String(500), nullable=True)
    extra = Column(JSONB, nullable=True)

    snapshot = relationship("Snapshot", back_populates="videos")
