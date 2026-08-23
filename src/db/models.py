from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    corridor: Mapped[str | None] = mapped_column(String(255))
    commodity: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    features: Mapped[list["ExtractedFeature"]] = relationship(back_populates="signal")


class ExtractedFeature(Base):
    __tablename__ = "extracted_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_used: Mapped[str] = mapped_column(String(255), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    signal: Mapped["Signal"] = relationship(back_populates="features")


class RiskScore(Base):
    __tablename__ = "risk_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    corridor: Mapped[str] = mapped_column(String(255), nullable=False)
    commodity: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    contributing_signal_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list
    )

    alerts: Mapped[list["Alert"]] = relationship(back_populates="risk_score")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    risk_score_id: Mapped[int] = mapped_column(
        ForeignKey("risk_scores.id"), nullable=False
    )
    threshold_crossed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="open", server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    risk_score: Mapped["RiskScore"] = relationship(back_populates="alerts")
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="alert"
    )


class ReferenceRoute(Base):
    __tablename__ = "reference_routes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier: Mapped[str] = mapped_column(String(255), nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    base_lead_time: Mapped[float] = mapped_column(Float, nullable=False)
    base_price_index: Mapped[float] = mapped_column(Float, nullable=False)

    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="route"
    )


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), nullable=False)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("reference_routes.id"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    alert: Mapped["Alert"] = relationship(back_populates="recommendations")
    route: Mapped["ReferenceRoute"] = relationship(back_populates="recommendations")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "window_start",
            "window_end",
            name="uq_ingestion_jobs_source_window",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(64), nullable=False, default="pending", server_default="pending"
    )
    error: Mapped[str | None] = mapped_column(Text)
