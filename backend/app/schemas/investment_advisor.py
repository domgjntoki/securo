import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ScoringMode = Literal["questionnaire", "manual", "percentage"]
PurchaseMode = Literal[
    "whole_units", "fractional_units", "cash_amount", "fixed_income_hybrid"
]
PriceSource = Literal["manual", "market"]
MatchKind = Literal["isin", "ticker_exchange_currency"]


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    home_country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    wallet_ids: list[uuid.UUID] = []

    @field_validator("currency", "home_country")
    @classmethod
    def uppercase_codes(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class StrategyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    home_country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    wallet_ids: Optional[list[uuid.UUID]] = None

    @field_validator("currency", "home_country")
    @classmethod
    def uppercase_optional_codes(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class QuestionCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)
    position: int = 0

    @field_validator("label", "text")
    @classmethod
    def strip_required_question_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class QuestionRead(BaseModel):
    id: uuid.UUID
    label: str
    text: str
    position: int


class QuestionBankCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class QuestionBankUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    position: Optional[int] = None


class QuestionUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=100)
    text: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    position: Optional[int] = None

    @field_validator("label", "text")
    @classmethod
    def strip_optional_question_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class QuestionBankRead(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    questions: list[QuestionRead]


class StrategyClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    scoring_mode: ScoringMode
    purchase_mode: PurchaseMode
    quantity_decimals: int = Field(default=0, ge=0, le=8)
    question_bank_id: Optional[uuid.UUID] = None
    display_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    position: int = 0

    @field_validator("display_currency")
    @classmethod
    def uppercase_display_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class StrategyClassUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    target_percentage: Optional[Decimal] = Field(default=None, ge=0, le=100)
    scoring_mode: Optional[ScoringMode] = None
    purchase_mode: Optional[PurchaseMode] = None
    quantity_decimals: Optional[int] = Field(default=None, ge=0, le=8)
    question_bank_id: Optional[uuid.UUID] = None
    display_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    position: Optional[int] = None
    is_archived: Optional[bool] = None

    @field_validator("display_currency")
    @classmethod
    def uppercase_optional_display_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class TargetEntry(BaseModel):
    class_id: uuid.UUID
    target_percentage: Decimal = Field(ge=0, le=100)


class TargetsUpdate(BaseModel):
    targets: list[TargetEntry]


class StrategyClassRead(BaseModel):
    id: uuid.UUID
    template_key: Optional[str]
    name: str
    target_percentage: Decimal
    scoring_mode: ScoringMode
    purchase_mode: PurchaseMode
    quantity_decimals: int
    question_bank_id: Optional[uuid.UUID]
    display_currency: Optional[str]
    position: int
    is_archived: bool


class InstrumentCreate(BaseModel):
    class_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    ticker: Optional[str] = Field(default=None, max_length=32)
    exchange: Optional[str] = Field(default=None, max_length=64)
    currency: str = Field(min_length=3, max_length=3)
    isin: Optional[str] = Field(default=None, max_length=20)
    current_price: Optional[Decimal] = Field(default=None, gt=0)
    price_source: PriceSource = "manual"
    manual_strength: Optional[int] = None
    target_percentage: Optional[Decimal] = Field(default=None, ge=0, le=100)
    yes_question_ids: list[uuid.UUID] = []
    asset_ids: list[uuid.UUID] = []

    @field_validator("ticker", "currency", "isin")
    @classmethod
    def uppercase_identity(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class InstrumentUpdate(BaseModel):
    class_id: Optional[uuid.UUID] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    ticker: Optional[str] = Field(default=None, max_length=32)
    exchange: Optional[str] = Field(default=None, max_length=64)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    isin: Optional[str] = Field(default=None, max_length=20)
    manual_price: Optional[Decimal] = Field(default=None, gt=0)
    price_source: Optional[PriceSource] = None
    manual_strength: Optional[int] = None
    target_percentage: Optional[Decimal] = Field(default=None, ge=0, le=100)
    yes_question_ids: Optional[list[uuid.UUID]] = None
    asset_ids: Optional[list[uuid.UUID]] = None

    @field_validator("ticker", "currency", "isin")
    @classmethod
    def uppercase_identity(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class InstrumentRead(BaseModel):
    id: uuid.UUID
    class_id: uuid.UUID
    name: str
    ticker: Optional[str]
    exchange: Optional[str]
    currency: str
    isin: Optional[str]
    manual_price: Optional[Decimal]
    cached_price_at: Optional[datetime]
    price_source: PriceSource
    manual_strength: Optional[int]
    target_percentage: Optional[Decimal]
    strength: Optional[int]
    allocatable: bool
    current_value: Decimal
    current_quantity: Decimal
    unit_price: Optional[Decimal]
    linked_asset_ids: list[uuid.UUID]
    yes_question_ids: list[uuid.UUID]
    warnings: list[str] = []


class InstrumentMatchCandidate(BaseModel):
    asset_id: uuid.UUID
    asset_name: str
    wallet_id: uuid.UUID
    match_kind: MatchKind
    ticker: Optional[str]
    exchange: Optional[str]
    currency: str
    isin: Optional[str]
    current_value: Optional[Decimal]
    current_quantity: Decimal
    already_linked: bool


class InstrumentMatchConfirm(BaseModel):
    asset_ids: list[uuid.UUID]


class InvestmentStrategyRead(BaseModel):
    id: uuid.UUID
    name: str
    currency: str
    home_country: str
    wallet_ids: list[uuid.UUID]
    is_archived: bool
    classes: list[StrategyClassRead]
    question_banks: list[QuestionBankRead]
    instruments: list[InstrumentRead] = []
    created_at: datetime


class PreviewRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    exclude_instrument_ids: list[uuid.UUID] = []
    refresh_prices: bool = False


class ContributionAllocationRead(BaseModel):
    id: Optional[uuid.UUID] = None
    instrument_id: Optional[uuid.UUID]
    instrument_name: str
    class_id: Optional[uuid.UUID] = None
    class_name: str
    current_value: Decimal
    current_quantity: Decimal
    unit_price: Optional[Decimal]
    strength: Optional[int]
    target_percentage: Optional[Decimal]
    suggested_value: Decimal
    suggested_quantity: Optional[Decimal]
    after_percentage: Decimal
    excluded: bool = False
    executed_at: Optional[datetime] = None
    actual_value: Optional[Decimal] = None
    actual_quantity: Optional[Decimal] = None
    execution_note: Optional[str] = None


class StrategyClassSnapshot(BaseModel):
    id: uuid.UUID
    name: str
    target_percentage: Decimal
    scoring_mode: ScoringMode
    purchase_mode: PurchaseMode
    quantity_decimals: int
    question_bank_id: Optional[uuid.UUID]
    display_currency: Optional[str] = None


class StrategyInstrumentSnapshot(BaseModel):
    id: uuid.UUID
    class_id: uuid.UUID
    name: str
    currency: str
    current_value: Decimal
    current_quantity: Decimal
    unit_price: Optional[Decimal]
    price_timestamp: Optional[datetime]
    strength: Optional[int]
    target_percentage: Optional[Decimal]
    allocatable: bool
    linked_asset_ids: list[uuid.UUID]


class ContributionPreview(BaseModel):
    strategy_id: uuid.UUID
    currency: str
    amount: Decimal
    portfolio_total: Decimal
    new_total: Decimal
    residual: Decimal
    algorithm_version: str
    calculated_at: datetime
    allocations: list[ContributionAllocationRead]
    class_totals: dict[str, Decimal]
    excluded_instrument_ids: list[uuid.UUID]
    warnings: list[str]
    fx_rates: dict[str, Decimal]
    class_snapshot: list[StrategyClassSnapshot]
    instrument_snapshot: list[StrategyInstrumentSnapshot]


class ContributionPlanRead(ContributionPreview):
    id: uuid.UUID
    created_at: datetime


class PlanAllocationPriceRead(BaseModel):
    allocation_id: uuid.UUID
    instrument_id: Optional[uuid.UUID]
    unit_price: Optional[Decimal]
    estimated_value: Decimal
    estimated_quantity: Optional[Decimal]
    price_as_of: Optional[datetime]
    available: bool


class PlanPriceRefreshRead(BaseModel):
    plan_id: uuid.UUID
    currency: str
    refreshed_at: datetime
    allocations: list[PlanAllocationPriceRead]
    warnings: list[str]
    fx_rates: dict[str, Decimal]


class ExecutionUpdate(BaseModel):
    executed: bool
    actual_value: Optional[Decimal] = Field(default=None, ge=0)
    actual_quantity: Optional[Decimal] = Field(default=None, ge=0)
    note: Optional[str] = Field(default=None, max_length=500)
