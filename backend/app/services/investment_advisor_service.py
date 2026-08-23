from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Optional, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.investment_advisor import (
    InvestmentContributionAllocation,
    InvestmentContributionPlan,
    InvestmentInstrumentAnswer,
    InvestmentInstrumentAssetLink,
    InvestmentQuestion,
    InvestmentQuestionBank,
    InvestmentStrategy,
    InvestmentStrategyClass,
    InvestmentStrategyInstrument,
    InvestmentStrategyWallet,
)
from app.schemas.investment_advisor import (
    ContributionAllocationRead,
    ContributionPlanRead,
    ContributionPreview,
    ExecutionUpdate,
    InstrumentCreate,
    InstrumentMatchCandidate,
    InstrumentRead,
    InstrumentUpdate,
    InvestmentStrategyRead,
    PlanAllocationPriceRead,
    PlanPriceRefreshRead,
    QuestionBankRead,
    QuestionBankUpdate,
    QuestionCreate,
    QuestionRead,
    QuestionUpdate,
    StrategyClassCreate,
    StrategyClassRead,
    StrategyClassUpdate,
    StrategyClassSnapshot,
    StrategyCreate,
    StrategyInstrumentSnapshot,
    StrategyUpdate,
    TargetsUpdate,
    PurchaseMode,
    ScoringMode,
)
from app.services import asset_service
from app.services.fx_rate_service import _resolve_rate
from app.services.investment_advisor_algorithm import (
    ALGORITHM_VERSION,
    ClassRule,
    InstrumentState,
    calculate,
)
from app.providers.market_price import get_market_price_provider


class AdvisorConfigurationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


STANDARD_CLASSES = (
    ("national_equities", "National Equities", "questionnaire", "whole_units", 0, "equities"),
    ("international_equities", "International Equities", "questionnaire", "fractional_units", 4, "equities"),
    ("national_real_estate", "National Real Estate Funds", "questionnaire", "whole_units", 0, "real_estate"),
    ("international_real_estate", "International Real Estate Funds", "questionnaire", "fractional_units", 4, "real_estate"),
    ("cryptoassets", "Cryptoassets", "manual", "fractional_units", 4, None),
    ("national_fixed_income", "National Fixed Income", "manual", "fixed_income_hybrid", 2, None),
    ("international_fixed_income", "International Fixed Income", "manual", "fixed_income_hybrid", 2, None),
)


def _strategy_options():
    return (
        selectinload(InvestmentStrategy.wallets),
        selectinload(InvestmentStrategy.classes).selectinload(
            InvestmentStrategyClass.question_bank
        ).selectinload(InvestmentQuestionBank.questions),
        selectinload(InvestmentStrategy.question_banks).selectinload(
            InvestmentQuestionBank.questions
        ),
        selectinload(InvestmentStrategy.instruments).selectinload(
            InvestmentStrategyInstrument.links
        ),
        selectinload(InvestmentStrategy.instruments).selectinload(
            InvestmentStrategyInstrument.answers
        ),
        selectinload(InvestmentStrategy.instruments).selectinload(
            InvestmentStrategyInstrument.strategy_class
        ),
    )


async def get_strategy(
    session: AsyncSession, strategy_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[InvestmentStrategy]:
    return (
        await session.execute(
            select(InvestmentStrategy)
            .options(*_strategy_options())
            .execution_options(populate_existing=True)
            .where(
                InvestmentStrategy.id == strategy_id,
                InvestmentStrategy.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()


async def list_strategies(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[InvestmentStrategy]:
    conditions = [InvestmentStrategy.workspace_id == workspace_id]
    if not include_archived:
        conditions.append(InvestmentStrategy.is_archived == False)
    return list(
        (
            await session.execute(
                select(InvestmentStrategy)
                .options(*_strategy_options())
                .where(*conditions)
                .order_by(InvestmentStrategy.name)
            )
        )
        .scalars()
        .unique()
        .all()
    )


async def create_strategy(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: StrategyCreate,
) -> InvestmentStrategy:
    strategy = InvestmentStrategy(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=data.name.strip(),
        currency=data.currency or "USD",
        home_country=data.home_country or "US",
    )
    session.add(strategy)
    await session.flush()
    banks = {
        "equities": InvestmentQuestionBank(strategy_id=strategy.id, name="Equities", position=0),
        "real_estate": InvestmentQuestionBank(
            strategy_id=strategy.id, name="Real Estate Securities", position=1
        ),
    }
    session.add_all(banks.values())
    await session.flush()
    for position, (key, name, scoring, purchase, decimals, bank_key) in enumerate(
        STANDARD_CLASSES
    ):
        session.add(
            InvestmentStrategyClass(
                strategy_id=strategy.id,
                question_bank_id=banks[bank_key].id if bank_key else None,
                template_key=key,
                name=name,
                target_percentage=Decimal("0"),
                scoring_mode=scoring,
                purchase_mode=purchase,
                quantity_decimals=decimals,
                position=position,
            )
        )
    await _replace_wallets(session, strategy, workspace_id, data.wallet_ids)
    await session.commit()
    fresh = await get_strategy(session, strategy.id, workspace_id)
    assert fresh is not None
    return fresh


async def delete_strategy(
    session: AsyncSession, strategy: InvestmentStrategy
) -> None:
    """Permanently remove advisor-owned data without touching Securo holdings."""
    plan_ids = select(InvestmentContributionPlan.id).where(
        InvestmentContributionPlan.strategy_id == strategy.id
    )
    instrument_ids = select(InvestmentStrategyInstrument.id).where(
        InvestmentStrategyInstrument.strategy_id == strategy.id
    )
    bank_ids = select(InvestmentQuestionBank.id).where(
        InvestmentQuestionBank.strategy_id == strategy.id
    )
    await session.execute(
        delete(InvestmentContributionAllocation).where(
            InvestmentContributionAllocation.plan_id.in_(plan_ids)
        )
    )
    await session.execute(
        delete(InvestmentContributionPlan).where(
            InvestmentContributionPlan.strategy_id == strategy.id
        )
    )
    await session.execute(
        delete(InvestmentInstrumentAnswer).where(
            InvestmentInstrumentAnswer.instrument_id.in_(instrument_ids)
        )
    )
    await session.execute(
        delete(InvestmentInstrumentAssetLink).where(
            InvestmentInstrumentAssetLink.instrument_id.in_(instrument_ids)
        )
    )
    await session.execute(
        delete(InvestmentStrategyInstrument).where(
            InvestmentStrategyInstrument.strategy_id == strategy.id
        )
    )
    await session.execute(
        delete(InvestmentStrategyClass).where(
            InvestmentStrategyClass.strategy_id == strategy.id
        )
    )
    await session.execute(
        delete(InvestmentQuestion).where(
            InvestmentQuestion.bank_id.in_(bank_ids)
        )
    )
    await session.execute(
        delete(InvestmentQuestionBank).where(
            InvestmentQuestionBank.strategy_id == strategy.id
        )
    )
    await session.execute(
        delete(InvestmentStrategyWallet).where(
            InvestmentStrategyWallet.strategy_id == strategy.id
        )
    )
    await session.execute(
        delete(InvestmentStrategy).where(InvestmentStrategy.id == strategy.id)
    )
    await session.commit()


async def update_strategy(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    workspace_id: uuid.UUID,
    data: StrategyUpdate,
) -> InvestmentStrategy:
    updates = data.model_dump(exclude_unset=True, exclude={"wallet_ids"})
    for key, value in updates.items():
        setattr(strategy, key, value.strip() if key == "name" else value)
    if data.wallet_ids is not None:
        await _replace_wallets(session, strategy, workspace_id, data.wallet_ids)
    await session.commit()
    fresh = await get_strategy(session, strategy.id, workspace_id)
    assert fresh is not None
    return fresh


async def _replace_wallets(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    workspace_id: uuid.UUID,
    wallet_ids: list[uuid.UUID],
) -> None:
    unique_ids = list(dict.fromkeys(wallet_ids))
    if unique_ids:
        found = set(
            (
                await session.execute(
                    select(AssetGroup.id).where(
                        AssetGroup.workspace_id == workspace_id,
                        AssetGroup.id.in_(unique_ids),
                    )
                )
            ).scalars()
        )
        if found != set(unique_ids):
            raise AdvisorConfigurationError(["One or more wallets are not in this workspace"])
    linked_wallet_ids = set(
        (
            await session.execute(
                select(Asset.group_id)
                .join(
                    InvestmentInstrumentAssetLink,
                    InvestmentInstrumentAssetLink.asset_id == Asset.id,
                )
                .join(
                    InvestmentStrategyInstrument,
                    InvestmentStrategyInstrument.id
                    == InvestmentInstrumentAssetLink.instrument_id,
                )
                .where(InvestmentStrategyInstrument.strategy_id == strategy.id)
            )
        ).scalars()
    )
    if not linked_wallet_ids.issubset(set(unique_ids)):
        raise AdvisorConfigurationError(
            ["Unlink strategy holdings before removing their discovery wallet"]
        )
    await session.execute(
        delete(InvestmentStrategyWallet).where(
            InvestmentStrategyWallet.strategy_id == strategy.id
        )
    )
    session.add_all(
        [
            InvestmentStrategyWallet(strategy_id=strategy.id, asset_group_id=wallet_id)
            for wallet_id in unique_ids
        ]
    )


def _class_read(row: InvestmentStrategyClass) -> StrategyClassRead:
    return StrategyClassRead(
        id=row.id,
        template_key=row.template_key,
        name=row.name,
        target_percentage=row.target_percentage,
        scoring_mode=cast(ScoringMode, row.scoring_mode),
        purchase_mode=cast(PurchaseMode, row.purchase_mode),
        quantity_decimals=row.quantity_decimals,
        question_bank_id=row.question_bank_id,
        display_currency=row.display_currency,
        position=row.position,
        is_archived=row.is_archived,
    )


def _bank_read(row: InvestmentQuestionBank) -> QuestionBankRead:
    return QuestionBankRead(
        id=row.id,
        name=row.name,
        position=row.position,
        questions=[
            QuestionRead(id=q.id, label=q.label, text=q.text, position=q.position)
            for q in sorted(row.questions, key=lambda item: (item.position, item.text))
        ],
    )


async def read_strategy(
    session: AsyncSession, strategy: InvestmentStrategy
) -> InvestmentStrategyRead:
    instrument_reads, _, _, _ = await _load_states(session, strategy, strict=False)
    return InvestmentStrategyRead(
        id=strategy.id,
        name=strategy.name,
        currency=strategy.currency,
        home_country=strategy.home_country,
        wallet_ids=[wallet.asset_group_id for wallet in strategy.wallets],
        is_archived=strategy.is_archived,
        classes=[
            _class_read(row)
            for row in sorted(strategy.classes, key=lambda item: (item.position, item.name))
        ],
        question_banks=[
            _bank_read(row)
            for row in sorted(strategy.question_banks, key=lambda item: (item.position, item.name))
        ],
        instruments=instrument_reads,
        created_at=strategy.created_at,
    )


async def create_class(
    session: AsyncSession, strategy: InvestmentStrategy, data: StrategyClassCreate
) -> InvestmentStrategyClass:
    await _validate_bank(session, strategy.id, data.question_bank_id, data.scoring_mode)
    row = InvestmentStrategyClass(strategy_id=strategy.id, **data.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_class(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    class_id: uuid.UUID,
    data: StrategyClassUpdate,
) -> Optional[InvestmentStrategyClass]:
    row = next((item for item in strategy.classes if item.id == class_id), None)
    if row is None:
        return None
    updates = data.model_dump(exclude_unset=True)
    if updates.get("is_archived") is True:
        if any(item.class_id == class_id for item in strategy.instruments):
            raise AdvisorConfigurationError(
                ["Move or remove class instruments before archiving the class"]
            )
        updates["target_percentage"] = Decimal("0")
    scoring = updates.get("scoring_mode", row.scoring_mode)
    bank_id = updates.get("question_bank_id", row.question_bank_id)
    await _validate_bank(session, strategy.id, bank_id, scoring)
    for key, value in updates.items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return row


async def _validate_bank(
    session: AsyncSession,
    strategy_id: uuid.UUID,
    bank_id: Optional[uuid.UUID],
    scoring_mode: str,
) -> None:
    if scoring_mode == "questionnaire" and bank_id is None:
        raise AdvisorConfigurationError(["Questionnaire classes require a question bank"])
    if bank_id is not None:
        exists = await session.scalar(
            select(InvestmentQuestionBank.id).where(
                InvestmentQuestionBank.id == bank_id,
                InvestmentQuestionBank.strategy_id == strategy_id,
            )
        )
        if exists is None:
            raise AdvisorConfigurationError(["Question bank not found in this strategy"])


async def replace_targets(
    session: AsyncSession, strategy: InvestmentStrategy, data: TargetsUpdate
) -> None:
    active = {row.id: row for row in strategy.classes if not row.is_archived}
    supplied = {item.class_id: item.target_percentage for item in data.targets}
    if not set(supplied).issubset(active):
        raise AdvisorConfigurationError(["Target contains an unknown class"])
    total = sum(supplied.values(), Decimal("0"))
    if total != Decimal("100"):
        raise AdvisorConfigurationError(["Active class targets must total exactly 100"])
    for class_id, row in active.items():
        row.target_percentage = supplied.get(class_id, Decimal("0"))
    await session.commit()


async def create_question(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    bank_id: uuid.UUID,
    data: QuestionCreate,
) -> Optional[InvestmentQuestion]:
    bank = next((item for item in strategy.question_banks if item.id == bank_id), None)
    if bank is None:
        return None
    question = InvestmentQuestion(bank_id=bank.id, **data.model_dump())
    session.add(question)
    await session.commit()
    await session.refresh(question)
    return question


async def update_question_bank(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    bank_id: uuid.UUID,
    data: QuestionBankUpdate,
) -> Optional[InvestmentQuestionBank]:
    bank = next((item for item in strategy.question_banks if item.id == bank_id), None)
    if bank is None:
        return None
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(bank, key, value.strip() if key == "name" else value)
    await session.commit()
    await session.refresh(bank)
    return bank


async def delete_question_bank(
    session: AsyncSession, strategy: InvestmentStrategy, bank_id: uuid.UUID
) -> bool:
    bank = next((item for item in strategy.question_banks if item.id == bank_id), None)
    if bank is None:
        return False
    if any(row.question_bank_id == bank_id and not row.is_archived for row in strategy.classes):
        raise AdvisorConfigurationError(
            ["Question bank is still assigned to an active strategy class"]
        )
    await session.delete(bank)
    await session.commit()
    return True


async def update_question(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    question_id: uuid.UUID,
    data: QuestionUpdate,
) -> Optional[InvestmentQuestion]:
    bank_ids = {bank.id for bank in strategy.question_banks}
    question = await session.get(InvestmentQuestion, question_id)
    if question is None or question.bank_id not in bank_ids:
        return None
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(question, key, value)
    await session.commit()
    await session.refresh(question)
    return question


async def delete_question(
    session: AsyncSession, strategy: InvestmentStrategy, question_id: uuid.UUID
) -> bool:
    bank_ids = {bank.id for bank in strategy.question_banks}
    question = await session.get(InvestmentQuestion, question_id)
    if question is None or question.bank_id not in bank_ids:
        return False
    await session.delete(question)
    await session.commit()
    return True


def _canonical_key(data: InstrumentCreate) -> str:
    if data.isin:
        return f"isin:{data.isin.upper()}"
    if data.ticker:
        return ":".join(
            ["ticker", data.ticker.upper(), (data.exchange or "").upper(), data.currency.upper()]
        )
    return f"manual:{data.name.strip().lower()}:{data.currency.upper()}"


def _normalized(value: Optional[str]) -> str:
    return (value or "").strip().upper()


def _instrument_canonical_key(instrument: InvestmentStrategyInstrument) -> str:
    if instrument.isin:
        return f"isin:{_normalized(instrument.isin)}"
    if instrument.ticker:
        return ":".join(
            [
                "ticker",
                _normalized(instrument.ticker),
                _normalized(instrument.exchange),
                _normalized(instrument.currency),
            ]
        )
    return f"manual:{instrument.name.strip().lower()}:{_normalized(instrument.currency)}"


async def create_instrument(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    workspace_id: uuid.UUID,
    data: InstrumentCreate,
) -> InvestmentStrategyInstrument:
    strategy_class = next(
        (row for row in strategy.classes if row.id == data.class_id and not row.is_archived), None
    )
    if strategy_class is None:
        raise AdvisorConfigurationError(["Class not found in this strategy"])
    row = InvestmentStrategyInstrument(
        strategy_id=strategy.id,
        class_id=data.class_id,
        canonical_key=_canonical_key(data),
        name=data.name.strip(),
        ticker=data.ticker,
        exchange=data.exchange,
        currency=data.currency,
        isin=data.isin,
        manual_price=data.current_price if data.price_source == "manual" else None,
        cached_price=data.current_price,
        cached_price_at=datetime.now(timezone.utc) if data.current_price else None,
        price_source=data.price_source,
        manual_strength=data.manual_strength,
        target_percentage=data.target_percentage,
        position=len(strategy.instruments),
    )
    session.add(row)
    await session.flush()
    # Links are always explicit. Match discovery is a separate read operation,
    # so a newly synced holding can never alter strategy membership silently.
    await _replace_links(session, strategy, row, workspace_id, data.asset_ids)
    await _replace_answers(session, strategy, row, data.yes_question_ids)
    await session.commit()
    fresh = await session.get(
        InvestmentStrategyInstrument,
        row.id,
        options=(
            selectinload(InvestmentStrategyInstrument.links),
            selectinload(InvestmentStrategyInstrument.answers),
            selectinload(InvestmentStrategyInstrument.strategy_class),
        ),
    )
    assert fresh is not None
    return fresh


async def update_instrument(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    workspace_id: uuid.UUID,
    instrument_id: uuid.UUID,
    data: InstrumentUpdate,
) -> Optional[InvestmentStrategyInstrument]:
    row = next((item for item in strategy.instruments if item.id == instrument_id), None)
    if row is None:
        return None
    updates = data.model_dump(
        exclude_unset=True, exclude={"asset_ids", "yes_question_ids"}
    )
    if "class_id" in updates and not any(
        item.id == updates["class_id"] and not item.is_archived for item in strategy.classes
    ):
        raise AdvisorConfigurationError(["Class not found in this strategy"])
    for key, value in updates.items():
        if key == "manual_price":
            row.manual_price = value
            row.cached_price = value
            row.cached_price_at = datetime.now(timezone.utc) if value else None
        else:
            setattr(row, key, value)
    if {"name", "ticker", "exchange", "currency", "isin"} & set(updates):
        row.canonical_key = _instrument_canonical_key(row)
    if data.asset_ids is not None:
        await _replace_links(session, strategy, row, workspace_id, data.asset_ids)
    if data.yes_question_ids is not None:
        await _replace_answers(session, strategy, row, data.yes_question_ids)
    await session.commit()
    return row


async def discover_matches(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    instrument: InvestmentStrategyInstrument,
    workspace_id: uuid.UUID,
) -> list[InstrumentMatchCandidate]:
    wallet_ids = [wallet.asset_group_id for wallet in strategy.wallets]
    if not wallet_ids:
        return []
    assets = list(
        (
            await session.execute(
                select(Asset).where(
                    Asset.workspace_id == workspace_id,
                    Asset.group_id.in_(wallet_ids),
                    Asset.is_archived == False,
                )
            )
        ).scalars()
    )
    exact_isin = [
        row
        for row in assets
        if instrument.isin and _normalized(row.isin) == _normalized(instrument.isin)
    ]
    if exact_isin:
        matched = [(row, "isin") for row in exact_isin]
    elif instrument.ticker and instrument.exchange:
        matched = [
            (row, "ticker_exchange_currency")
            for row in assets
            if _normalized(row.ticker) == _normalized(instrument.ticker)
            and _normalized(row.ticker_exchange) == _normalized(instrument.exchange)
            and _normalized(row.currency) == _normalized(instrument.currency)
        ]
    else:
        matched = []

    linked_ids = {link.asset_id for link in instrument.links}
    candidates: list[InstrumentMatchCandidate] = []
    for asset, match_kind in matched:
        asset_read = await asset_service.get_asset(session, asset.id, workspace_id)
        candidates.append(
            InstrumentMatchCandidate(
                asset_id=asset.id,
                asset_name=asset.name,
                wallet_id=cast(uuid.UUID, asset.group_id),
                match_kind=cast(Any, match_kind),
                ticker=asset.ticker,
                exchange=asset.ticker_exchange,
                currency=asset.currency,
                isin=asset.isin,
                current_value=(
                    Decimal(str(asset_read.current_value))
                    if asset_read and asset_read.current_value is not None
                    else None
                ),
                current_quantity=Decimal(str(asset.units or 0)),
                already_linked=asset.id in linked_ids,
            )
        )
    return candidates


async def _replace_links(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    instrument: InvestmentStrategyInstrument,
    workspace_id: uuid.UUID,
    asset_ids: list[uuid.UUID],
) -> None:
    unique_ids = list(dict.fromkeys(asset_ids))
    wallet_ids = [wallet.asset_group_id for wallet in strategy.wallets]
    if unique_ids:
        if not wallet_ids:
            raise AdvisorConfigurationError(
                ["Select a discovery wallet before linking a Securo holding"]
            )
        found = set(
            (
                await session.execute(
                    select(Asset.id).where(
                        Asset.id.in_(unique_ids),
                        Asset.workspace_id == workspace_id,
                        Asset.group_id.in_(wallet_ids),
                    )
                )
            ).scalars()
        )
        if found != set(unique_ids):
            raise AdvisorConfigurationError(
                ["Linked assets must belong to a selected wallet in this workspace"]
            )
    await session.execute(
        delete(InvestmentInstrumentAssetLink).where(
            InvestmentInstrumentAssetLink.instrument_id == instrument.id
        )
    )
    session.add_all(
        [
            InvestmentInstrumentAssetLink(instrument_id=instrument.id, asset_id=asset_id)
            for asset_id in unique_ids
        ]
    )


async def _replace_answers(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    instrument: InvestmentStrategyInstrument,
    question_ids: list[uuid.UUID],
) -> None:
    strategy_class = next(item for item in strategy.classes if item.id == instrument.class_id)
    bank = next(
        (item for item in strategy.question_banks if item.id == strategy_class.question_bank_id),
        None,
    )
    allowed = {question.id for question in bank.questions} if bank else set()
    if not set(question_ids).issubset(allowed):
        raise AdvisorConfigurationError(["Answer contains a question outside the class bank"])
    await session.execute(
        delete(InvestmentInstrumentAnswer).where(
            InvestmentInstrumentAnswer.instrument_id == instrument.id
        )
    )
    session.add_all(
        [
            InvestmentInstrumentAnswer(instrument_id=instrument.id, question_id=question_id)
            for question_id in dict.fromkeys(question_ids)
        ]
    )


async def delete_instrument(
    session: AsyncSession, strategy: InvestmentStrategy, instrument_id: uuid.UUID
) -> bool:
    row = next((item for item in strategy.instruments if item.id == instrument_id), None)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def _convert_real(
    session: AsyncSession,
    amount: Decimal,
    source: str,
    target: str,
    fx_rates: dict[str, Decimal],
    *,
    allow_fetch: bool,
) -> Optional[Decimal]:
    if source == target:
        fx_rates[source] = Decimal("1")
        return amount
    rate = await _resolve_rate(session, source, target, allow_fetch=allow_fetch)
    if rate is None:
        return None
    fx_rates[source] = rate
    return amount * rate


async def _load_states(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    *,
    strict: bool,
    allow_fx_fetch: bool = False,
) -> tuple[list[InstrumentRead], list[InstrumentState], dict[str, Decimal], list[str]]:
    classes = {row.id: row for row in strategy.classes if not row.is_archived}
    banks = {row.id: row for row in strategy.question_banks}
    reads: list[InstrumentRead] = []
    states: list[InstrumentState] = []
    fx_rates: dict[str, Decimal] = {strategy.currency: Decimal("1")}
    global_warnings: list[str] = []
    errors: list[str] = []

    for instrument in sorted(strategy.instruments, key=lambda item: (item.position, item.name)):
        rule = classes.get(instrument.class_id)
        if rule is None:
            errors.append(f"{instrument.name}: class is archived or missing")
            continue
        warnings: list[str] = []
        current_value = Decimal("0")
        current_quantity = Decimal("0")
        for link in instrument.links:
            asset_row = await session.get(Asset, link.asset_id)
            if asset_row is None or asset_row.workspace_id != strategy.workspace_id:
                warnings.append("linked_asset_missing")
                continue
            asset_read = await asset_service.get_asset(
                session, asset_row.id, strategy.workspace_id
            )
            if asset_read is None or asset_read.current_value is None:
                errors.append(f"{instrument.name}: linked holding has no current value")
                continue
            converted = await _convert_real(
                session,
                Decimal(str(asset_read.current_value)),
                asset_row.currency,
                strategy.currency,
                fx_rates,
                allow_fetch=allow_fx_fetch,
            )
            if converted is None:
                errors.append(
                    f"{instrument.name}: no FX rate for {asset_row.currency}/{strategy.currency}"
                )
                continue
            current_value += converted
            current_quantity += Decimal(str(asset_read.units or 0))

        price_native = instrument.manual_price or instrument.cached_price
        unit_price: Optional[Decimal] = None
        if current_quantity > 0 and current_value > 0:
            unit_price = current_value / current_quantity
        elif price_native is not None:
            unit_price = await _convert_real(
                session,
                Decimal(str(price_native)),
                instrument.currency,
                strategy.currency,
                fx_rates,
                allow_fetch=allow_fx_fetch,
            )
            if unit_price is None:
                errors.append(
                    f"{instrument.name}: no FX rate for {instrument.currency}/{strategy.currency}"
                )

        yes_ids = {answer.question_id for answer in instrument.answers}
        strength: Optional[int]
        if rule.scoring_mode == "manual":
            strength = instrument.manual_strength
            allocatable = strength is not None and strength >= 0
            if strength is None:
                warnings.append("manual_strength_required")
        elif rule.scoring_mode == "percentage":
            strength = None
            allocatable = (
                instrument.target_percentage is not None
                and instrument.target_percentage > 0
            )
            if instrument.target_percentage is None:
                warnings.append("target_percentage_required")
        else:
            bank = banks.get(rule.question_bank_id) if rule.question_bank_id else None
            questions = bank.questions if bank else []
            if not questions:
                strength = None
                allocatable = False
                warnings.append("question_bank_empty")
            else:
                valid_ids = {question.id for question in questions}
                strength = 2 * len(yes_ids & valid_ids) - len(questions)
                allocatable = strength > 0

        requires_unit_price = (
            rule.scoring_mode != "percentage"
            or (instrument.target_percentage or Decimal("0")) > 0
        )
        if (
            rule.purchase_mode not in {"cash_amount", "fixed_income_hybrid"}
            and unit_price is None
            and requires_unit_price
        ):
            allocatable = False
            warnings.append("unit_price_required")
            errors.append(f"{instrument.name}: required unit price is missing")
        if rule.purchase_mode == "fixed_income_hybrid" and unit_price is None:
            # This is the intended cash-value branch, not a missing-price warning.
            pass

        read = InstrumentRead(
            id=instrument.id,
            class_id=instrument.class_id,
            name=instrument.name,
            ticker=instrument.ticker,
            exchange=instrument.exchange,
            currency=instrument.currency,
            isin=instrument.isin,
            manual_price=instrument.manual_price,
            cached_price_at=instrument.cached_price_at,
            price_source=cast(Any, instrument.price_source),
            manual_strength=instrument.manual_strength,
            target_percentage=instrument.target_percentage,
            strength=strength,
            allocatable=allocatable,
            current_value=current_value.quantize(Decimal("0.01")),
            current_quantity=current_quantity,
            unit_price=unit_price,
            linked_asset_ids=[link.asset_id for link in instrument.links],
            yes_question_ids=sorted(yes_ids, key=str),
            warnings=warnings,
        )
        reads.append(read)
        states.append(
            InstrumentState(
                id=str(instrument.id),
                class_id=str(instrument.class_id),
                name=instrument.name,
                current_value=current_value,
                current_quantity=current_quantity,
                unit_price=unit_price,
                strength=strength,
                allocatable=allocatable,
                target_percentage=(
                    instrument.target_percentage
                    if rule.scoring_mode == "percentage"
                    else None
                ),
            )
        )
        global_warnings.extend(f"{instrument.name}:{warning}" for warning in warnings)
        if instrument.cached_price_at is not None and not instrument.links:
            global_warnings.append(
                f"{instrument.name}:price_as_of:{instrument.cached_price_at.isoformat()}"
            )

    if strict and errors:
        raise AdvisorConfigurationError(errors)
    global_warnings.extend(errors)
    return reads, states, fx_rates, global_warnings


async def preview(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    amount: Decimal,
    exclude_ids: list[uuid.UUID],
) -> ContributionPreview:
    active_classes = [row for row in strategy.classes if not row.is_archived]
    if sum((row.target_percentage for row in active_classes), Decimal("0")) != Decimal("100"):
        raise AdvisorConfigurationError(["Active class targets must total exactly 100"])
    percentage_errors: list[str] = []
    for row in active_classes:
        if row.scoring_mode != "percentage" or row.target_percentage <= 0:
            continue
        members = [item for item in strategy.instruments if item.class_id == row.id]
        if any(item.target_percentage is None for item in members):
            percentage_errors.append(
                f"{row.name}: set a percentage target for every instrument"
            )
            continue
        target_total = sum(
            (item.target_percentage or Decimal("0") for item in members),
            Decimal("0"),
        )
        if target_total != Decimal("100"):
            percentage_errors.append(
                f"{row.name}: instrument percentage targets must total exactly 100"
            )
    if percentage_errors:
        raise AdvisorConfigurationError(percentage_errors)
    reads, states, fx_rates, warnings = await _load_states(
        session,
        strategy,
        strict=True,
        allow_fx_fetch=False,
    )
    display_currency_errors: list[str] = []
    for row in active_classes:
        currency = row.display_currency
        if not currency or currency == strategy.currency:
            continue
        rate = await _resolve_rate(
            session, currency, strategy.currency, allow_fetch=False
        )
        if rate is None:
            display_currency_errors.append(
                f"{row.name}: missing FX rate {currency}/{strategy.currency}"
            )
            continue
        fx_rates[currency] = rate
    if display_currency_errors:
        raise AdvisorConfigurationError(display_currency_errors)
    rules = [
        ClassRule(
            id=str(row.id),
            name=row.name,
            target_percentage=Decimal(str(row.target_percentage)),
            purchase_mode=row.purchase_mode,
            quantity_decimals=row.quantity_decimals,
            scoring_mode=row.scoring_mode,
        )
        for row in active_classes
    ]
    result = calculate(rules, states, Decimal(str(amount)), {str(item) for item in exclude_ids})
    allocation_reads = [
        ContributionAllocationRead(
            instrument_id=uuid.UUID(item.instrument_id),
            instrument_name=item.instrument_name,
            class_id=uuid.UUID(item.class_id),
            class_name=item.class_name,
            current_value=item.current_value,
            current_quantity=item.current_quantity,
            unit_price=item.unit_price,
            strength=item.strength,
            target_percentage=item.target_percentage,
            suggested_value=item.suggested_value,
            suggested_quantity=item.suggested_quantity,
            after_percentage=item.after_percentage,
        )
        for item in result.allocations
    ]
    allocatable_class_ids = {state.class_id for state in states if state.allocatable}
    for row in active_classes:
        if row.target_percentage > 0 and str(row.id) not in allocatable_class_ids:
            warnings.append(f"{row.name}:no_allocatable_instrument")
    suggested_by_class = {
        class_id: sum(
            (item.suggested_value for item in allocation_reads if item.class_id == class_id),
            Decimal("0"),
        )
        for class_id in {row.id for row in active_classes}
    }
    current_by_class = {
        class_id: sum(
            (item.current_value for item in reads if item.class_id == class_id),
            Decimal("0"),
        )
        for class_id in {row.id for row in active_classes}
    }
    return ContributionPreview(
        strategy_id=strategy.id,
        currency=strategy.currency,
        amount=result.contribution,
        portfolio_total=result.portfolio_total,
        new_total=result.new_total,
        residual=result.residual,
        algorithm_version=ALGORITHM_VERSION,
        calculated_at=datetime.now(timezone.utc),
        allocations=allocation_reads,
        class_totals={
            str(row.id): money_total
            for row in active_classes
            if (
                money_total := (
                    current_by_class.get(row.id, Decimal("0"))
                    + suggested_by_class.get(row.id, Decimal("0"))
                ).quantize(Decimal("0.01"))
            )
            > 0
        },
        excluded_instrument_ids=exclude_ids,
        warnings=warnings,
        fx_rates=fx_rates,
        class_snapshot=[
            StrategyClassSnapshot(
                id=row.id,
                name=row.name,
                target_percentage=row.target_percentage,
                scoring_mode=cast(Any, row.scoring_mode),
                purchase_mode=cast(Any, row.purchase_mode),
                quantity_decimals=row.quantity_decimals,
                question_bank_id=row.question_bank_id,
                display_currency=row.display_currency,
            )
            for row in sorted(active_classes, key=lambda item: (item.position, item.name))
        ],
        instrument_snapshot=[
            StrategyInstrumentSnapshot(
                id=item.id,
                class_id=item.class_id,
                name=item.name,
                currency=item.currency,
                current_value=item.current_value,
                current_quantity=item.current_quantity,
                unit_price=item.unit_price,
                price_timestamp=item.cached_price_at,
                strength=item.strength,
                target_percentage=item.target_percentage,
                allocatable=item.allocatable,
                linked_asset_ids=item.linked_asset_ids,
            )
            for item in reads
        ],
    )


async def _refresh_market_prices(
    session: AsyncSession, strategy: InvestmentStrategy
) -> list[str]:
    instruments = [
        item
        for item in strategy.instruments
        if item.price_source == "market" and item.ticker and not item.links
    ]
    if not instruments:
        return []
    warnings: list[str] = []
    provider = get_market_price_provider()
    try:
        quotes = await provider.get_quotes([cast(str, item.ticker) for item in instruments])
    except Exception:
        return ["market_price_refresh_failed"]
    refreshed_at = datetime.now(timezone.utc)
    for item in instruments:
        quote = quotes.get(cast(str, item.ticker))
        if quote is None or quote.price is None:
            warnings.append(f"{item.name}:market_price_unavailable")
            continue
        if _normalized(quote.currency) != _normalized(item.currency):
            warnings.append(f"{item.name}:market_price_currency_mismatch")
            continue
        item.cached_price = Decimal(str(quote.price))
        item.cached_price_at = refreshed_at
    await session.commit()
    return warnings


async def refresh_market_data(
    session: AsyncSession, strategy: InvestmentStrategy
) -> list[str]:
    """Warm market-price and real-FX caches from a write-gated endpoint."""
    warnings = await _refresh_market_prices(session, strategy)
    currencies = {item.currency for item in strategy.instruments}
    currencies.update(
        row.display_currency
        for row in strategy.classes
        if not row.is_archived and row.display_currency
    )
    linked_asset_ids = [
        link.asset_id for item in strategy.instruments for link in item.links
    ]
    if linked_asset_ids:
        currencies.update(
            (
                await session.execute(
                    select(Asset.currency).where(
                        Asset.id.in_(linked_asset_ids),
                        Asset.workspace_id == strategy.workspace_id,
                    )
                )
            ).scalars()
        )
        provider = get_market_price_provider()
        for asset_id in dict.fromkeys(linked_asset_ids):
            asset = await session.get(Asset, asset_id)
            if (
                asset is None
                or asset.workspace_id != strategy.workspace_id
                or asset.valuation_method != "market_price"
            ):
                continue
            try:
                refreshed = await asset_service.refresh_market_price_asset(
                    session, asset, market_provider=provider
                )
            except Exception:
                refreshed = False
            if not refreshed:
                warnings.append(f"{asset.name}:market_price_unavailable")
        await session.commit()
    for currency in sorted(currencies):
        if currency == strategy.currency:
            continue
        rate = await _resolve_rate(
            session, currency, strategy.currency, allow_fetch=True
        )
        if rate is None:
            warnings.append(
                f"missing_fx_rate:{currency}/{strategy.currency}"
            )
    return warnings


async def _current_plan_unit_price(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    instrument: InvestmentStrategyInstrument,
    plan_currency: str,
    fx_rates: dict[str, Decimal],
    warnings: list[str],
) -> tuple[Optional[Decimal], Optional[datetime]]:
    current_value = Decimal("0")
    current_quantity = Decimal("0")
    linked_prices: list[Decimal] = []
    price_timestamps: list[datetime] = []

    for link in instrument.links:
        asset = await session.get(Asset, link.asset_id)
        if asset is None or asset.workspace_id != strategy.workspace_id:
            warnings.append(f"{instrument.name}:linked_asset_missing")
            continue
        asset_read = await asset_service.get_asset(session, asset.id, strategy.workspace_id)
        if asset_read is None:
            warnings.append(f"{instrument.name}:current_price_unavailable")
            continue
        if asset.last_price is not None:
            converted_price = await _convert_real(
                session,
                Decimal(str(asset.last_price)),
                asset.currency,
                plan_currency,
                fx_rates,
                allow_fetch=True,
            )
            if converted_price is not None:
                linked_prices.append(converted_price)
        if asset.last_price_at is not None:
            price_timestamps.append(asset.last_price_at)
        if asset_read.current_value is None or not asset_read.units:
            continue
        converted_value = await _convert_real(
            session,
            Decimal(str(asset_read.current_value)),
            asset.currency,
            plan_currency,
            fx_rates,
            allow_fetch=True,
        )
        if converted_value is None:
            warnings.append(
                f"{instrument.name}:missing_fx_rate:{asset.currency}/{plan_currency}"
            )
            continue
        current_value += converted_value
        current_quantity += Decimal(str(asset_read.units))

    price_as_of = max(price_timestamps) if price_timestamps else None
    if current_quantity > 0 and current_value > 0:
        return current_value / current_quantity, price_as_of
    if linked_prices:
        return linked_prices[0], price_as_of

    native_price = instrument.manual_price or instrument.cached_price
    if native_price is None:
        return None, instrument.cached_price_at
    converted_price = await _convert_real(
        session,
        Decimal(str(native_price)),
        instrument.currency,
        plan_currency,
        fx_rates,
        allow_fetch=True,
    )
    if converted_price is None:
        warnings.append(
            f"{instrument.name}:missing_fx_rate:{instrument.currency}/{plan_currency}"
        )
    return converted_price, instrument.cached_price_at


async def refresh_plan_prices(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    plan: InvestmentContributionPlan,
) -> PlanPriceRefreshRead:
    """Refresh current quotes and reprice only the unfinished plan allocations.

    The saved plan and its immutable calculation snapshot are never modified.
    """
    warnings = await refresh_market_data(session, strategy)
    refreshed_at = datetime.now(timezone.utc)
    fx_rates: dict[str, Decimal] = {plan.currency: Decimal("1")}
    instruments = {item.id: item for item in strategy.instruments}
    snapshot = plan.snapshot or {}
    allocation_snapshots = {
        item.get("instrument_id"): item
        for item in snapshot.get("allocations", [])
        if item.get("instrument_id")
    }
    class_snapshots = {
        item.get("id"): item
        for item in snapshot.get("class_snapshot", [])
        if item.get("id")
    }
    for class_snapshot in class_snapshots.values():
        display_currency = class_snapshot.get("display_currency")
        if not display_currency or display_currency == plan.currency:
            continue
        rate = await _resolve_rate(
            session, display_currency, plan.currency, allow_fetch=True
        )
        if rate is None:
            warnings.append(
                f"missing_fx_rate:{display_currency}/{plan.currency}"
            )
            continue
        fx_rates[display_currency] = rate
    repriced: list[PlanAllocationPriceRead] = []

    for allocation in plan.allocations:
        if allocation.executed_at is not None:
            continue
        instrument_id = allocation.instrument_id
        allocation_snapshot = allocation_snapshots.get(str(instrument_id), {})
        class_snapshot = class_snapshots.get(allocation_snapshot.get("class_id"), {})
        purchase_mode = class_snapshot.get("purchase_mode", "fractional_units")
        quantity_decimals = int(class_snapshot.get("quantity_decimals", 8))
        planned_value = Decimal(str(allocation.suggested_value))
        instrument = instruments.get(instrument_id) if instrument_id else None

        if purchase_mode == "cash_amount":
            repriced.append(
                PlanAllocationPriceRead(
                    allocation_id=allocation.id,
                    instrument_id=instrument_id,
                    unit_price=None,
                    estimated_value=planned_value,
                    estimated_quantity=None,
                    price_as_of=None,
                    available=True,
                )
            )
            continue

        if instrument is None:
            warnings.append(f"{allocation.instrument_name}:plan_instrument_unavailable")
            repriced.append(
                PlanAllocationPriceRead(
                    allocation_id=allocation.id,
                    instrument_id=instrument_id,
                    unit_price=None,
                    estimated_value=planned_value,
                    estimated_quantity=allocation.suggested_quantity,
                    price_as_of=None,
                    available=purchase_mode == "fixed_income_hybrid",
                )
            )
            continue

        unit_price, price_as_of = await _current_plan_unit_price(
            session, strategy, instrument, plan.currency, fx_rates, warnings
        )
        if unit_price is None or unit_price <= 0:
            cash_fallback = purchase_mode == "fixed_income_hybrid"
            if not cash_fallback:
                warnings.append(f"{allocation.instrument_name}:current_price_unavailable")
            repriced.append(
                PlanAllocationPriceRead(
                    allocation_id=allocation.id,
                    instrument_id=instrument_id,
                    unit_price=None,
                    estimated_value=planned_value,
                    estimated_quantity=None if cash_fallback else allocation.suggested_quantity,
                    price_as_of=price_as_of,
                    available=cash_fallback,
                )
            )
            continue

        quantity_step = Decimal("1").scaleb(-quantity_decimals)
        estimated_quantity = (planned_value / unit_price).quantize(
            quantity_step, rounding=ROUND_DOWN
        )
        estimated_value = (estimated_quantity * unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        repriced.append(
            PlanAllocationPriceRead(
                allocation_id=allocation.id,
                instrument_id=instrument_id,
                unit_price=unit_price,
                estimated_value=estimated_value,
                estimated_quantity=estimated_quantity,
                price_as_of=price_as_of,
                available=True,
            )
        )

    return PlanPriceRefreshRead(
        plan_id=plan.id,
        currency=plan.currency,
        refreshed_at=refreshed_at,
        allocations=repriced,
        warnings=list(dict.fromkeys(warnings)),
        fx_rates=fx_rates,
    )


def _jsonable_snapshot(preview_data: ContributionPreview) -> dict[str, Any]:
    return preview_data.model_dump(mode="json")


async def save_plan(
    session: AsyncSession,
    strategy: InvestmentStrategy,
    user_id: uuid.UUID,
    amount: Decimal,
    exclude_ids: list[uuid.UUID],
) -> InvestmentContributionPlan:
    result = await preview(session, strategy, amount, exclude_ids)
    plan = InvestmentContributionPlan(
        strategy_id=strategy.id,
        created_by_user_id=user_id,
        currency=result.currency,
        contribution_amount=result.amount,
        portfolio_total=result.portfolio_total,
        new_total=result.new_total,
        residual=result.residual,
        algorithm_version=result.algorithm_version,
        snapshot=_jsonable_snapshot(result),
    )
    session.add(plan)
    await session.flush()
    for item in result.allocations:
        session.add(
            InvestmentContributionAllocation(
                plan_id=plan.id,
                instrument_id=item.instrument_id,
                instrument_name=item.instrument_name,
                class_name=item.class_name,
                current_value=item.current_value,
                current_quantity=item.current_quantity,
                unit_price=item.unit_price,
                strength=item.strength,
                target_percentage=item.target_percentage,
                suggested_value=item.suggested_value,
                suggested_quantity=item.suggested_quantity,
                after_percentage=item.after_percentage,
                excluded=item.excluded,
            )
        )
    await session.commit()
    fresh = await get_plan(session, plan.id, strategy.id)
    assert fresh is not None
    return fresh


async def get_plan(
    session: AsyncSession, plan_id: uuid.UUID, strategy_id: uuid.UUID
) -> Optional[InvestmentContributionPlan]:
    return (
        await session.execute(
            select(InvestmentContributionPlan)
            .options(selectinload(InvestmentContributionPlan.allocations))
            .where(
                InvestmentContributionPlan.id == plan_id,
                InvestmentContributionPlan.strategy_id == strategy_id,
            )
        )
    ).scalar_one_or_none()


async def list_plans(
    session: AsyncSession, strategy_id: uuid.UUID
) -> list[InvestmentContributionPlan]:
    return list(
        (
            await session.execute(
                select(InvestmentContributionPlan)
                .options(selectinload(InvestmentContributionPlan.allocations))
                .where(InvestmentContributionPlan.strategy_id == strategy_id)
                .order_by(InvestmentContributionPlan.created_at.desc())
            )
        ).scalars()
    )


async def delete_plan(
    session: AsyncSession, plan: InvestmentContributionPlan
) -> None:
    await session.delete(plan)
    await session.commit()


def plan_read(plan: InvestmentContributionPlan) -> ContributionPlanRead:
    snapshot = plan.snapshot or {}
    excluded = [uuid.UUID(item) for item in snapshot.get("excluded_instrument_ids", [])]
    fx_rates = {key: Decimal(str(value)) for key, value in snapshot.get("fx_rates", {}).items()}
    class_ids = {
        item.get("instrument_id"): item.get("class_id")
        for item in snapshot.get("allocations", [])
    }
    return ContributionPlanRead(
        id=plan.id,
        strategy_id=plan.strategy_id,
        currency=plan.currency,
        amount=plan.contribution_amount,
        portfolio_total=plan.portfolio_total,
        new_total=plan.new_total,
        residual=plan.residual,
        algorithm_version=plan.algorithm_version,
        calculated_at=plan.created_at,
        created_at=plan.created_at,
        allocations=[
            ContributionAllocationRead(
                id=row.id,
                instrument_id=row.instrument_id,
                instrument_name=row.instrument_name,
                class_id=(
                    uuid.UUID(class_ids[str(row.instrument_id)])
                    if row.instrument_id and class_ids.get(str(row.instrument_id))
                    else None
                ),
                class_name=row.class_name,
                current_value=row.current_value,
                current_quantity=row.current_quantity,
                unit_price=row.unit_price,
                strength=row.strength,
                target_percentage=row.target_percentage,
                suggested_value=row.suggested_value,
                suggested_quantity=row.suggested_quantity,
                after_percentage=row.after_percentage,
                excluded=row.excluded,
                executed_at=row.executed_at,
                actual_value=row.actual_value,
                actual_quantity=row.actual_quantity,
                execution_note=row.execution_note,
            )
            for row in plan.allocations
        ],
        class_totals={
            key: Decimal(str(value)) for key, value in snapshot.get("class_totals", {}).items()
        },
        excluded_instrument_ids=excluded,
        warnings=snapshot.get("warnings", []),
        fx_rates=fx_rates,
        class_snapshot=snapshot.get("class_snapshot", []),
        instrument_snapshot=snapshot.get("instrument_snapshot", []),
    )


async def update_execution(
    session: AsyncSession,
    plan: InvestmentContributionPlan,
    allocation_id: uuid.UUID,
    data: ExecutionUpdate,
) -> Optional[InvestmentContributionAllocation]:
    row = next((item for item in plan.allocations if item.id == allocation_id), None)
    if row is None:
        return None
    if data.executed:
        row.executed_at = datetime.now(timezone.utc)
        row.actual_value = data.actual_value if data.actual_value is not None else row.suggested_value
        row.actual_quantity = (
            data.actual_quantity
            if data.actual_quantity is not None
            else row.suggested_quantity
        )
        row.execution_note = data.note
    else:
        row.executed_at = None
        row.actual_value = None
        row.actual_quantity = None
        row.execution_note = None
    await session.commit()
    await session.refresh(row)
    return row
