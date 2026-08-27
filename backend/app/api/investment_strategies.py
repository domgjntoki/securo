import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.models.investment_advisor import InvestmentQuestionBank
from app.schemas.investment_advisor import (
    ContributionPlanRead,
    ContributionPreview,
    ExecutionUpdate,
    InstrumentCreate,
    InstrumentMatchCandidate,
    InstrumentMatchConfirm,
    InstrumentUpdate,
    InvestmentStrategyRead,
    PlanPriceRefreshRead,
    PreviewRequest,
    QuestionBankCreate,
    QuestionBankUpdate,
    QuestionCreate,
    QuestionUpdate,
    StrategyClassCreate,
    StrategyClassRead,
    StrategyClassUpdate,
    StrategyCreate,
    StrategyUpdate,
    TargetsUpdate,
)
from app.services import investment_advisor_service as service

router = APIRouter(prefix="/api/investment-strategies", tags=["investment-strategies"])


def _bad_configuration(exc: service.AdvisorConfigurationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "advisor_configuration", "errors": exc.errors},
    )


async def _strategy_or_404(
    session: AsyncSession, strategy_id: uuid.UUID, workspace_id: uuid.UUID
):
    strategy = await service.get_strategy(session, strategy_id, workspace_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Investment strategy not found")
    return strategy


def _require_active(strategy):
    if strategy.is_archived:
        raise HTTPException(status_code=409, detail="Investment strategy is archived")
    return strategy


@router.get("", response_model=list[InvestmentStrategyRead])
async def list_strategies(
    include_archived: bool = Query(False),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    rows = await service.list_strategies(
        session, ctx.workspace.id, include_archived=include_archived
    )
    return [await service.read_strategy(session, row) for row in rows]


@router.post("", response_model=InvestmentStrategyRead, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    data: StrategyCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    data = data.model_copy(
        update={
            "currency": data.currency or ctx.workspace.default_currency,
            "home_country": data.home_country
            or (ctx.workspace.tax_jurisdiction or "US")[:2].upper(),
        }
    )
    try:
        row = await service.create_strategy(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    return await service.read_strategy(session, row)


@router.get("/{strategy_id}", response_model=InvestmentStrategyRead)
async def get_strategy(
    strategy_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    row = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    return await service.read_strategy(session, row)


@router.patch("/{strategy_id}", response_model=InvestmentStrategyRead)
async def update_strategy(
    strategy_id: uuid.UUID,
    data: StrategyUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    row = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(row)
    try:
        row = await service.update_strategy(session, row, ctx.workspace.id, data)
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    return await service.read_strategy(session, row)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_strategy(
    strategy_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    row = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    row.is_archived = True
    await session.commit()


@router.delete(
    "/{strategy_id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_strategy_permanently(
    strategy_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    row = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    await service.delete_strategy(session, row)


@router.post(
    "/{strategy_id}/classes",
    response_model=StrategyClassRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_class(
    strategy_id: uuid.UUID,
    data: StrategyClassCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        row = await service.create_class(session, strategy, data)
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    return service._class_read(row)


@router.patch("/{strategy_id}/classes/{class_id}", response_model=StrategyClassRead)
async def update_class(
    strategy_id: uuid.UUID,
    class_id: uuid.UUID,
    data: StrategyClassUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        row = await service.update_class(session, strategy, class_id, data)
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy class not found")
    return service._class_read(row)


@router.put("/{strategy_id}/targets", status_code=status.HTTP_204_NO_CONTENT)
async def replace_targets(
    strategy_id: uuid.UUID,
    data: TargetsUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        await service.replace_targets(session, strategy, data)
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)


@router.post("/{strategy_id}/question-banks", status_code=status.HTTP_201_CREATED)
async def create_question_bank(
    strategy_id: uuid.UUID,
    data: QuestionBankCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    row = InvestmentQuestionBank(
        strategy_id=strategy.id,
        name=data.name,
        position=len(strategy.question_banks),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "name": row.name, "position": row.position, "questions": []}


@router.patch("/{strategy_id}/question-banks/{bank_id}")
async def update_question_bank(
    strategy_id: uuid.UUID,
    bank_id: uuid.UUID,
    data: QuestionBankUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = _require_active(
        await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    )
    row = await service.update_question_bank(session, strategy, bank_id, data)
    if row is None:
        raise HTTPException(status_code=404, detail="Question bank not found")
    fresh = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    return next(service._bank_read(item) for item in fresh.question_banks if item.id == bank_id)


@router.delete(
    "/{strategy_id}/question-banks/{bank_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_question_bank(
    strategy_id: uuid.UUID,
    bank_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = _require_active(
        await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    )
    try:
        deleted = await service.delete_question_bank(session, strategy, bank_id)
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    if not deleted:
        raise HTTPException(status_code=404, detail="Question bank not found")


@router.post(
    "/{strategy_id}/question-banks/{bank_id}/questions",
    status_code=status.HTTP_201_CREATED,
)
async def create_question(
    strategy_id: uuid.UUID,
    bank_id: uuid.UUID,
    data: QuestionCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    row = await service.create_question(session, strategy, bank_id, data)
    if row is None:
        raise HTTPException(status_code=404, detail="Question bank not found")
    return {"id": row.id, "label": row.label, "text": row.text, "position": row.position}


@router.patch("/{strategy_id}/questions/{question_id}")
async def update_question(
    strategy_id: uuid.UUID,
    question_id: uuid.UUID,
    data: QuestionUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = _require_active(
        await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    )
    row = await service.update_question(session, strategy, question_id, data)
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return {"id": row.id, "label": row.label, "text": row.text, "position": row.position}


@router.delete(
    "/{strategy_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_question(
    strategy_id: uuid.UUID,
    question_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    if not await service.delete_question(session, strategy, question_id):
        raise HTTPException(status_code=404, detail="Question not found")


@router.post(
    "/{strategy_id}/instruments", status_code=status.HTTP_201_CREATED
)
async def create_instrument(
    strategy_id: uuid.UUID,
    data: InstrumentCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        row = await service.create_instrument(
            session, strategy, ctx.workspace.id, data
        )
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    fresh = await service.get_strategy(session, strategy.id, ctx.workspace.id)
    assert fresh is not None
    read = await service.read_strategy(session, fresh)
    return next(item for item in read.instruments if item.id == row.id)


@router.patch("/{strategy_id}/instruments/{instrument_id}")
async def update_instrument(
    strategy_id: uuid.UUID,
    instrument_id: uuid.UUID,
    data: InstrumentUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        row = await service.update_instrument(
            session, strategy, ctx.workspace.id, instrument_id, data
        )
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy instrument not found")
    fresh = await service.get_strategy(session, strategy.id, ctx.workspace.id)
    assert fresh is not None
    read = await service.read_strategy(session, fresh)
    return next(item for item in read.instruments if item.id == instrument_id)


@router.delete(
    "/{strategy_id}/instruments/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_instrument(
    strategy_id: uuid.UUID,
    instrument_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    if not await service.delete_instrument(session, strategy, instrument_id):
        raise HTTPException(status_code=404, detail="Strategy instrument not found")


@router.get(
    "/{strategy_id}/instruments/{instrument_id}/matches",
    response_model=list[InstrumentMatchCandidate],
)
async def discover_instrument_matches(
    strategy_id: uuid.UUID,
    instrument_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    instrument = next(
        (item for item in strategy.instruments if item.id == instrument_id), None
    )
    if instrument is None:
        raise HTTPException(status_code=404, detail="Strategy instrument not found")
    return await service.discover_matches(
        session, strategy, instrument, ctx.workspace.id
    )


@router.post("/{strategy_id}/instruments/{instrument_id}/matches/confirm")
async def confirm_instrument_matches(
    strategy_id: uuid.UUID,
    instrument_id: uuid.UUID,
    data: InstrumentMatchConfirm,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = _require_active(
        await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    )
    instrument = next(
        (item for item in strategy.instruments if item.id == instrument_id), None
    )
    if instrument is None:
        raise HTTPException(status_code=404, detail="Strategy instrument not found")
    try:
        row = await service.update_instrument(
            session,
            strategy,
            ctx.workspace.id,
            instrument_id,
            InstrumentUpdate(asset_ids=data.asset_ids),
        )
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy instrument not found")
    fresh = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    read = await service.read_strategy(session, fresh)
    return next(item for item in read.instruments if item.id == instrument_id)


@router.post("/{strategy_id}/preview", response_model=ContributionPreview)
async def preview_contribution(
    strategy_id: uuid.UUID,
    data: PreviewRequest,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    # Deliberately read-gated: this POST is a calculation request and persists
    # nothing. Viewers can inspect the same advice as the strategy itself.
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        return await service.preview(
            session,
            strategy,
            data.amount,
            data.exclude_instrument_ids,
        )
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)


@router.post("/{strategy_id}/refresh-market-data")
async def refresh_market_data(
    strategy_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = _require_active(
        await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    )
    return {"warnings": await service.refresh_market_data(session, strategy)}


@router.post(
    "/{strategy_id}/plans",
    response_model=ContributionPlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def save_plan(
    strategy_id: uuid.UUID,
    data: PreviewRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    _require_active(strategy)
    try:
        plan = await service.save_plan(
            session, strategy, ctx.user_id, data.amount, data.exclude_instrument_ids
        )
    except service.AdvisorConfigurationError as exc:
        raise _bad_configuration(exc)
    return service.plan_read(plan)


@router.get("/{strategy_id}/plans", response_model=list[ContributionPlanRead])
async def list_plans(
    strategy_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    return [service.plan_read(row) for row in await service.list_plans(session, strategy.id)]


@router.get("/{strategy_id}/plans/{plan_id}", response_model=ContributionPlanRead)
async def get_plan(
    strategy_id: uuid.UUID,
    plan_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    plan = await service.get_plan(session, plan_id, strategy.id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Contribution plan not found")
    return service.plan_read(plan)


@router.delete(
    "/{strategy_id}/plans/{plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_plan(
    strategy_id: uuid.UUID,
    plan_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    plan = await service.get_plan(session, plan_id, strategy.id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Contribution plan not found")
    await service.delete_plan(session, plan)


@router.post(
    "/{strategy_id}/plans/{plan_id}/refresh-prices",
    response_model=PlanPriceRefreshRead,
)
async def refresh_plan_prices(
    strategy_id: uuid.UUID,
    plan_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    plan = await service.get_plan(session, plan_id, strategy.id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Contribution plan not found")
    return await service.refresh_plan_prices(session, strategy, plan)


@router.patch(
    "/{strategy_id}/plans/{plan_id}/allocations/{allocation_id}",
    response_model=ContributionPlanRead,
)
async def update_execution(
    strategy_id: uuid.UUID,
    plan_id: uuid.UUID,
    allocation_id: uuid.UUID,
    data: ExecutionUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    strategy = await _strategy_or_404(session, strategy_id, ctx.workspace.id)
    plan = await service.get_plan(session, plan_id, strategy.id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Contribution plan not found")
    row = await service.update_execution(session, plan, allocation_id, data)
    if row is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    fresh = await service.get_plan(session, plan.id, strategy.id)
    assert fresh is not None
    return service.plan_read(fresh)
