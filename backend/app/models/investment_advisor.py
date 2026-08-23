import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvestmentStrategy(Base):
    __tablename__ = "investment_strategies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    home_country: Mapped[str] = mapped_column(String(2))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    wallets: Mapped[list["InvestmentStrategyWallet"]] = relationship(
        cascade="all, delete-orphan", back_populates="strategy", lazy="selectin"
    )
    classes: Mapped[list["InvestmentStrategyClass"]] = relationship(
        cascade="all, delete-orphan", back_populates="strategy", lazy="selectin"
    )
    question_banks: Mapped[list["InvestmentQuestionBank"]] = relationship(
        cascade="all, delete-orphan", back_populates="strategy", lazy="selectin"
    )
    instruments: Mapped[list["InvestmentStrategyInstrument"]] = relationship(
        cascade="all, delete-orphan", back_populates="strategy", lazy="selectin"
    )


class InvestmentStrategyWallet(Base):
    __tablename__ = "investment_strategy_wallets"
    __table_args__ = (UniqueConstraint("strategy_id", "asset_group_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategies.id", ondelete="CASCADE"), index=True
    )
    asset_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_groups.id", ondelete="CASCADE"), index=True
    )
    strategy: Mapped[InvestmentStrategy] = relationship(back_populates="wallets")


class InvestmentQuestionBank(Base):
    __tablename__ = "investment_question_banks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategies.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    strategy: Mapped[InvestmentStrategy] = relationship(back_populates="question_banks")
    questions: Mapped[list["InvestmentQuestion"]] = relationship(
        cascade="all, delete-orphan", back_populates="bank", lazy="selectin"
    )


class InvestmentQuestion(Base):
    __tablename__ = "investment_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_question_banks.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100), default="")
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)
    bank: Mapped[InvestmentQuestionBank] = relationship(back_populates="questions")


class InvestmentStrategyClass(Base):
    __tablename__ = "investment_strategy_classes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategies.id", ondelete="CASCADE"), index=True
    )
    question_bank_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_question_banks.id", ondelete="SET NULL"), nullable=True
    )
    template_key: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    target_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("0"))
    scoring_mode: Mapped[str] = mapped_column(String(20))
    purchase_mode: Mapped[str] = mapped_column(String(30))
    quantity_decimals: Mapped[int] = mapped_column(Integer, default=0)
    display_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    strategy: Mapped[InvestmentStrategy] = relationship(back_populates="classes")
    question_bank: Mapped[Optional[InvestmentQuestionBank]] = relationship(lazy="selectin")


class InvestmentStrategyInstrument(Base):
    __tablename__ = "investment_strategy_instruments"
    __table_args__ = (UniqueConstraint("strategy_id", "canonical_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategies.id", ondelete="CASCADE"), index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategy_classes.id", ondelete="RESTRICT"), index=True
    )
    canonical_key: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    ticker: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    manual_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    cached_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    cached_price_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    price_source: Mapped[str] = mapped_column(
        String(20), default="manual", server_default="manual"
    )
    manual_strength: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 4), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    strategy: Mapped[InvestmentStrategy] = relationship(back_populates="instruments")
    strategy_class: Mapped[InvestmentStrategyClass] = relationship(lazy="selectin")
    links: Mapped[list["InvestmentInstrumentAssetLink"]] = relationship(
        cascade="all, delete-orphan", back_populates="instrument", lazy="selectin"
    )
    answers: Mapped[list["InvestmentInstrumentAnswer"]] = relationship(
        cascade="all, delete-orphan", back_populates="instrument", lazy="selectin"
    )


class InvestmentInstrumentAssetLink(Base):
    __tablename__ = "investment_instrument_asset_links"
    __table_args__ = (UniqueConstraint("instrument_id", "asset_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategy_instruments.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    instrument: Mapped[InvestmentStrategyInstrument] = relationship(back_populates="links")


class InvestmentInstrumentAnswer(Base):
    __tablename__ = "investment_instrument_answers"
    __table_args__ = (UniqueConstraint("instrument_id", "question_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategy_instruments.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_questions.id", ondelete="CASCADE"), index=True
    )
    instrument: Mapped[InvestmentStrategyInstrument] = relationship(back_populates="answers")


class InvestmentContributionPlan(Base):
    __tablename__ = "investment_contribution_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategies.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    contribution_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    portfolio_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    new_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    residual: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    algorithm_version: Mapped[str] = mapped_column(String(50))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    allocations: Mapped[list["InvestmentContributionAllocation"]] = relationship(
        cascade="all, delete-orphan", back_populates="plan", lazy="selectin"
    )


class InvestmentContributionAllocation(Base):
    __tablename__ = "investment_contribution_allocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_contribution_plans.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investment_strategy_instruments.id", ondelete="SET NULL"), nullable=True
    )
    instrument_name: Mapped[str] = mapped_column(String(255))
    class_name: Mapped[str] = mapped_column(String(100))
    current_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    current_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 6), nullable=True)
    strength: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(7, 4), nullable=True
    )
    suggested_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    suggested_quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8), nullable=True)
    after_percentage: Mapped[Decimal] = mapped_column(Numeric(9, 4))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    actual_quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(24, 8), nullable=True)
    execution_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    plan: Mapped[InvestmentContributionPlan] = relationship(back_populates="allocations")
