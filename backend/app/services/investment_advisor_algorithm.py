"""Pure contribution-allocation engine used by Securo's investment advisor.

The engine deliberately knows nothing about HTTP, SQLAlchemy, providers, or
Pantaneiro's source code. It operates on immutable Decimal domain values so a
saved plan can reproduce the exact arithmetic that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

ALGORITHM_VERSION = "securo-advisor-v2"
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ClassRule:
    id: str
    name: str
    target_percentage: Decimal
    purchase_mode: str
    quantity_decimals: int
    scoring_mode: str = "manual"


@dataclass(frozen=True)
class InstrumentState:
    id: str
    class_id: str
    name: str
    current_value: Decimal
    current_quantity: Decimal
    unit_price: Decimal | None
    strength: int | None
    allocatable: bool
    target_percentage: Decimal | None = None


@dataclass(frozen=True)
class Allocation:
    instrument_id: str
    class_id: str
    instrument_name: str
    class_name: str
    current_value: Decimal
    current_quantity: Decimal
    unit_price: Decimal | None
    strength: int | None
    target_percentage: Decimal | None
    suggested_value: Decimal
    suggested_quantity: Decimal | None
    after_percentage: Decimal


@dataclass(frozen=True)
class Calculation:
    contribution: Decimal
    portfolio_total: Decimal
    new_total: Decimal
    residual: Decimal
    allocations: tuple[Allocation, ...]
    class_totals: dict[str, Decimal]


def money(value: Decimal) -> Decimal:
    return max(Decimal("0"), value).quantize(CENT, rounding=ROUND_DOWN)


def calculate(
    classes: list[ClassRule],
    instruments: list[InstrumentState],
    contribution: Decimal,
    exclude_ids: set[str] | None = None,
) -> Calculation:
    contribution = money(contribution)
    if contribution <= 0:
        raise ValueError("contribution must be positive")

    excluded = exclude_ids or set()
    by_class = {rule.id: rule for rule in classes if rule.target_percentage > 0}
    portfolio_total = money(sum((item.current_value for item in instruments), Decimal("0")))
    new_total = portfolio_total + contribution

    eligible_classes = {
        item.class_id
        for item in instruments
        if item.class_id in by_class
        and item.allocatable
        and item.id not in excluded
        and _can_purchase(by_class[item.class_id], item)
    }
    eligible_targets = {
        class_id: by_class[class_id].target_percentage for class_id in eligible_classes
    }
    target_total = sum(eligible_targets.values(), Decimal("0"))
    if target_total <= 0:
        return Calculation(
            contribution, portfolio_total, new_total, contribution, (), {}
        )

    current_by_class = {
        class_id: sum(
            (item.current_value for item in instruments if item.class_id == class_id),
            Decimal("0"),
        )
        for class_id in eligible_classes
    }
    gaps = {
        class_id: max(
            Decimal("0"),
            (target / HUNDRED) * new_total - current_by_class[class_id],
        )
        for class_id, target in eligible_targets.items()
    }
    gaps = {class_id: gap for class_id, gap in gaps.items() if gap > 0}
    class_shares = _allocate_across_classes(
        contribution, eligible_targets, gaps
    )

    raw: dict[str, Decimal] = {}
    for class_id, share in class_shares.items():
        candidates = [
            item
            for item in instruments
            if item.class_id == class_id
            and item.allocatable
            and item.id not in excluded
            and _can_purchase(by_class[class_id], item)
        ]
        if not candidates:
            continue
        rule = by_class[class_id]
        if rule.scoring_mode == "percentage":
            weights = {
                item.id: max(Decimal("0"), item.target_percentage or Decimal("0"))
                for item in candidates
            }
            weight_total = sum(weights.values(), Decimal("0"))
            if weight_total <= 0:
                continue
            class_after = (
                sum(
                    (item.current_value for item in instruments if item.class_id == class_id),
                    Decimal("0"),
                )
                + share
            )
            sub_gaps = {
                item.id: max(
                    Decimal("0"),
                    weights[item.id] / weight_total * class_after - item.current_value,
                )
                for item in candidates
            }
            percentage_shares = _allocate_across_classes(share, weights, sub_gaps)
            raw.update(percentage_shares)
            continue
        if any((item.strength or 0) > 0 for item in candidates):
            weights = {item.id: Decimal(max(0, item.strength or 0)) for item in candidates}
        else:
            value_total = sum((item.current_value for item in candidates), Decimal("0"))
            weights = (
                {item.id: item.current_value for item in candidates}
                if value_total > 0
                else {item.id: Decimal("1") for item in candidates}
            )
        weight_total = sum(weights.values(), Decimal("0"))
        class_after = sum((item.current_value for item in candidates), Decimal("0")) + share
        sub_gaps = {
            item.id: max(
                Decimal("0"),
                weights[item.id] / weight_total * class_after - item.current_value,
            )
            for item in candidates
        }
        sub_gap_total = sum(sub_gaps.values(), Decimal("0"))
        for item in candidates:
            if sub_gap_total > 0 and sub_gaps[item.id] > 0:
                raw[item.id] = share * sub_gaps[item.id] / sub_gap_total
            elif sub_gap_total == 0 and weights[item.id] > 0:
                raw[item.id] = share * weights[item.id] / weight_total

    states = {item.id: item for item in instruments}
    values: dict[str, Decimal] = {}
    quantities: dict[str, Decimal | None] = {}
    for instrument_id, raw_value in raw.items():
        item = states[instrument_id]
        value, quantity = _quantize(by_class[item.class_id], item, raw_value)
        if value > 0:
            values[instrument_id] = value
            quantities[instrument_id] = quantity

    residual = money(contribution - sum(values.values(), Decimal("0")))
    residual = _absorb_residual(
        residual,
        values,
        quantities,
        classes=by_class,
        instruments=instruments,
        excluded=excluded,
        contribution=contribution,
        new_total=new_total,
    )

    allocations: list[Allocation] = []
    class_totals: dict[str, Decimal] = {}
    for instrument_id, value in values.items():
        item = states[instrument_id]
        rule = by_class[item.class_id]
        value = money(value)
        if value <= 0:
            continue
        class_totals[item.class_id] = class_totals.get(item.class_id, Decimal("0")) + value
        allocations.append(
            Allocation(
                instrument_id=item.id,
                class_id=item.class_id,
                instrument_name=item.name,
                class_name=rule.name,
                current_value=money(item.current_value),
                current_quantity=item.current_quantity,
                unit_price=item.unit_price,
                strength=item.strength,
                target_percentage=item.target_percentage,
                suggested_value=value,
                suggested_quantity=quantities[item.id],
                after_percentage=(
                    ((item.current_value + value) / new_total) * HUNDRED
                ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN),
            )
        )

    allocations.sort(key=lambda allocation: (-allocation.suggested_value, allocation.instrument_name))
    return Calculation(
        contribution=contribution,
        portfolio_total=portfolio_total,
        new_total=new_total,
        residual=money(residual),
        allocations=tuple(allocations),
        class_totals={key: money(value) for key, value in class_totals.items()},
    )


def _allocate_across_classes(
    contribution: Decimal,
    targets: dict[str, Decimal],
    gaps: dict[str, Decimal],
) -> dict[str, Decimal]:
    """Water-fill post-contribution gaps without starving small gaps.

    Each pass offers the remaining contribution according to the normalized
    targets of classes that are still below target. Any gap smaller than its
    offer is closed first and removed from the next pass. Once every gap is
    closed, excess is spread by the normalized targets of all eligible
    classes. This is the three-stage behavior independently expressed in
    Decimal arithmetic.
    """
    shares = {class_id: Decimal("0") for class_id in targets}
    remaining = contribution
    open_gaps = dict(gaps)

    while remaining > 0 and open_gaps:
        open_target_total = sum(
            (targets[class_id] for class_id in open_gaps), Decimal("0")
        )
        offers = {
            class_id: remaining * targets[class_id] / open_target_total
            for class_id in open_gaps
        }
        closable = [
            class_id
            for class_id, gap in open_gaps.items()
            if gap <= offers[class_id]
        ]
        if not closable:
            for class_id, offer in offers.items():
                shares[class_id] += offer
            remaining = Decimal("0")
            break
        for class_id in closable:
            gap = open_gaps.pop(class_id)
            shares[class_id] += gap
            remaining -= gap

    if remaining > 0:
        target_total = sum(targets.values(), Decimal("0"))
        for class_id, target in targets.items():
            shares[class_id] += remaining * target / target_total

    return {class_id: share for class_id, share in shares.items() if share > 0}


def _can_purchase(rule: ClassRule, item: InstrumentState) -> bool:
    return rule.purchase_mode == "cash_amount" or (
        rule.purchase_mode == "fixed_income_hybrid" and item.unit_price is None
    ) or (item.unit_price is not None and item.unit_price > 0)


def _quantize(
    rule: ClassRule, item: InstrumentState, raw_value: Decimal
) -> tuple[Decimal, Decimal | None]:
    if rule.purchase_mode == "cash_amount" or (
        rule.purchase_mode == "fixed_income_hybrid" and item.unit_price is None
    ):
        return money(raw_value), None
    assert item.unit_price is not None
    decimals = 0 if rule.purchase_mode == "whole_units" else rule.quantity_decimals
    unit = Decimal("1").scaleb(-decimals)
    quantity = (raw_value / item.unit_price).quantize(unit, rounding=ROUND_DOWN)
    return money(quantity * item.unit_price), quantity


def _precision_tier(rule: ClassRule, item: InstrumentState) -> int:
    if rule.purchase_mode == "cash_amount" or (
        rule.purchase_mode == "fixed_income_hybrid" and item.unit_price is None
    ):
        return 0
    if rule.quantity_decimals >= 4:
        return 0
    if rule.quantity_decimals > 0 or rule.purchase_mode == "fixed_income_hybrid":
        return 1
    return 2


def _absorb_residual(
    residual: Decimal,
    values: dict[str, Decimal],
    quantities: dict[str, Decimal | None],
    *,
    classes: dict[str, ClassRule],
    instruments: list[InstrumentState],
    excluded: set[str],
    contribution: Decimal,
    new_total: Decimal,
) -> Decimal:
    del contribution
    candidates = [
        item
        for item in instruments
        if item.id not in excluded
        and item.allocatable
        and item.class_id in classes
        and _can_purchase(classes[item.class_id], item)
    ]
    while residual >= CENT:
        fitting: list[tuple[int, int, Decimal, InstrumentState]] = []
        for item in candidates:
            rule = classes[item.class_id]
            if rule.purchase_mode == "cash_amount" or (
                rule.purchase_mode == "fixed_income_hybrid" and item.unit_price is None
            ):
                step_value = CENT
            else:
                assert item.unit_price is not None
                decimals = 0 if rule.purchase_mode == "whole_units" else rule.quantity_decimals
                step_value = item.unit_price * Decimal("1").scaleb(-decimals)
            if step_value <= residual + Decimal("0.0000001"):
                after = (item.current_value + values.get(item.id, Decimal("0"))) / new_total
                fitting.append((_precision_tier(rule, item), -(item.strength or 0), after, item))
        if not fitting:
            break
        _, _, _, item = min(fitting, key=lambda row: (row[0], row[1], row[2], row[3].name))
        rule = classes[item.class_id]
        if rule.purchase_mode == "cash_amount" or (
            rule.purchase_mode == "fixed_income_hybrid" and item.unit_price is None
        ):
            add_value = residual
            add_quantity = None
        else:
            assert item.unit_price is not None
            decimals = 0 if rule.purchase_mode == "whole_units" else rule.quantity_decimals
            unit = Decimal("1").scaleb(-decimals)
            add_quantity = (residual / item.unit_price).quantize(unit, rounding=ROUND_DOWN)
            add_value = money(add_quantity * item.unit_price)
        if add_value <= 0:
            break
        values[item.id] = values.get(item.id, Decimal("0")) + add_value
        if add_quantity is not None:
            quantities[item.id] = (quantities.get(item.id) or Decimal("0")) + add_quantity
        else:
            quantities[item.id] = None
        residual = money(residual - add_value)
    return residual
