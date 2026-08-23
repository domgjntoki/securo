from decimal import Decimal

from app.services.investment_advisor_algorithm import ClassRule, InstrumentState, calculate


def rule(
    id: str,
    target: str,
    mode: str = "fractional_units",
    decimals: int = 4,
    scoring: str = "manual",
) -> ClassRule:
    return ClassRule(
        id=id,
        name=id,
        target_percentage=Decimal(target),
        purchase_mode=mode,
        quantity_decimals=decimals,
        scoring_mode=scoring,
    )


def instrument(
    id: str,
    class_id: str,
    value: str,
    price: str | None,
    strength: int | None = 1,
    allocatable: bool = True,
    target: str | None = None,
) -> InstrumentState:
    unit_price = Decimal(price) if price is not None else None
    quantity = Decimal(value) / unit_price if unit_price else Decimal("0")
    return InstrumentState(
        id=id,
        class_id=class_id,
        name=id,
        current_value=Decimal(value),
        current_quantity=quantity,
        unit_price=unit_price,
        strength=strength,
        allocatable=allocatable,
        target_percentage=Decimal(target) if target is not None else None,
    )


def test_gap_distribution_and_cash_residual_use_full_contribution() -> None:
    result = calculate(
        [rule("equity", "60", "whole_units", 0), rule("cash", "40", "cash_amount", 0)],
        [instrument("stock", "equity", "100", "30", 5), instrument("bond", "cash", "900", None, 0)],
        Decimal("500"),
    )
    assert sum(item.suggested_value for item in result.allocations) == Decimal("500.00")
    assert result.residual == Decimal("0.00")
    assert all(item.suggested_value >= 0 for item in result.allocations)


def test_excluded_instrument_keeps_its_value_but_receives_no_money() -> None:
    items = [
        instrument("first", "crypto", "500", "100", 3),
        instrument("second", "crypto", "500", "100", 2),
    ]
    result = calculate([rule("crypto", "100")], items, Decimal("250"), {"first"})
    assert result.portfolio_total == Decimal("1000.00")
    assert {item.instrument_id for item in result.allocations} == {"second"}
    assert result.allocations[0].suggested_value == Decimal("250.00")


def test_zero_position_candidate_can_receive_allocation() -> None:
    result = calculate(
        [rule("international", "100")],
        [instrument("candidate", "international", "0", "125", 4)],
        Decimal("1000"),
    )
    assert result.residual == Decimal("0.00")
    assert result.allocations[0].suggested_quantity == Decimal("8.0000")
    assert result.allocations[0].suggested_value == Decimal("1000.00")


def test_whole_units_never_overspend_and_report_unavoidable_residual() -> None:
    result = calculate(
        [rule("equity", "100", "whole_units", 0)],
        [instrument("stock", "equity", "1000", "90", 5)],
        Decimal("250"),
    )
    assert sum(item.suggested_value for item in result.allocations) == Decimal("180.00")
    assert result.residual == Decimal("70.00")
    assert sum(item.suggested_value for item in result.allocations) <= result.contribution


def test_smaller_class_gap_is_closed_before_remaining_gap_is_filled() -> None:
    result = calculate(
        [
            rule("overweight", "50", "cash_amount", 0),
            rule("large-gap", "30", "cash_amount", 0),
            rule("small-gap", "20", "cash_amount", 0),
        ],
        [
            instrument("over", "overweight", "690", None),
            instrument("large", "large-gap", "100", None),
            instrument("small", "small-gap", "210", None),
        ],
        Decimal("100"),
    )
    suggested = {item.instrument_id: item.suggested_value for item in result.allocations}
    assert suggested == {"large": Decimal("90.00"), "small": Decimal("10.00")}


def test_skipped_class_target_is_normalized_into_remaining_class() -> None:
    result = calculate(
        [rule("eligible", "50", "cash_amount", 0), rule("missing", "50")],
        [instrument("cash", "eligible", "0", None, 0)],
        Decimal("100"),
    )
    assert result.allocations[0].suggested_value == Decimal("100.00")
    assert result.residual == Decimal("0.00")


def test_instrument_weighting_uses_strength_then_value_then_equal() -> None:
    strong = calculate(
        [rule("class", "100", "cash_amount", 0)],
        [
            instrument("three", "class", "0", None, 3),
            instrument("one", "class", "0", None, 1),
        ],
        Decimal("100"),
    )
    assert {row.instrument_id: row.suggested_value for row in strong.allocations} == {
        "three": Decimal("75.00"),
        "one": Decimal("25.00"),
    }

    by_value = calculate(
        [rule("class", "100", "cash_amount", 0)],
        [
            instrument("three", "class", "300", None, 0),
            instrument("one", "class", "100", None, 0),
        ],
        Decimal("100"),
    )
    assert {row.instrument_id: row.suggested_value for row in by_value.allocations} == {
        "three": Decimal("75.00"),
        "one": Decimal("25.00"),
    }

    equal = calculate(
        [rule("class", "100", "cash_amount", 0)],
        [
            instrument("a", "class", "0", None, 0),
            instrument("b", "class", "0", None, 0),
        ],
        Decimal("100"),
    )
    assert {row.suggested_value for row in equal.allocations} == {Decimal("50.00")}


def test_purchase_modes_quantize_to_configured_precision() -> None:
    fractional = calculate(
        [rule("fractional", "100", "fractional_units", 4)],
        [instrument("fund", "fractional", "0", "3")],
        Decimal("10"),
    )
    assert fractional.allocations[0].suggested_quantity == Decimal("3.3333")
    assert fractional.residual == Decimal("0.01")

    fixed_priced = calculate(
        [rule("fixed", "100", "fixed_income_hybrid", 2)],
        [instrument("bond", "fixed", "0", "3", 0)],
        Decimal("10"),
    )
    assert fixed_priced.allocations[0].suggested_quantity == Decimal("3.33")

    fixed_cash = calculate(
        [rule("fixed", "100", "fixed_income_hybrid", 2)],
        [instrument("bond", "fixed", "0", None, 0)],
        Decimal("10"),
    )
    assert fixed_cash.allocations[0].suggested_quantity is None
    assert fixed_cash.allocations[0].suggested_value == Decimal("10.00")


def test_high_precision_instrument_absorbs_residual_before_whole_units() -> None:
    result = calculate(
        [
            rule("fractional", "50", "fractional_units", 4),
            rule("whole", "50", "whole_units", 0),
        ],
        [
            instrument("fractional", "fractional", "0", "3", 1),
            instrument("whole", "whole", "0", "7", 1),
        ],
        Decimal("10"),
    )
    assert {row.instrument_id for row in result.allocations} == {"fractional"}
    assert result.allocations[0].suggested_value == Decimal("10.00")
    assert result.residual == Decimal("0.00")


def test_allocation_never_exceeds_contribution_across_precisions() -> None:
    for contribution in ("0.01", "1.23", "99.99", "1000"):
        result = calculate(
            [
                rule("whole", "25", "whole_units", 0),
                rule("fractional", "25", "fractional_units", 4),
                rule("fixed", "25", "fixed_income_hybrid", 2),
                rule("cash", "25", "cash_amount", 0),
            ],
            [
                instrument("stock", "whole", "100", "37"),
                instrument("fund", "fractional", "100", "11.19"),
                instrument("bond", "fixed", "100", "3.17", 0),
                instrument("deposit", "cash", "100", None, 0),
            ],
            Decimal(contribution),
        )
        assert sum(row.suggested_value for row in result.allocations) <= result.contribution
        assert sum(row.suggested_value for row in result.allocations) + result.residual <= result.contribution


def test_percentage_mode_allocates_to_instrument_targets() -> None:
    result = calculate(
        [rule("international-etfs", "100", scoring="percentage")],
        [
            instrument("sp500", "international-etfs", "0", "1", target="50"),
            instrument("eimi", "international-etfs", "0", "1", target="30"),
            instrument("other", "international-etfs", "0", "1", target="20"),
        ],
        Decimal("1000"),
    )

    assert {row.instrument_id: row.suggested_value for row in result.allocations} == {
        "sp500": Decimal("500.00"),
        "eimi": Decimal("300.00"),
        "other": Decimal("200.00"),
    }
    assert result.residual == Decimal("0.00")


def test_percentage_mode_prioritizes_underweight_instruments() -> None:
    result = calculate(
        [rule("international-etfs", "100", scoring="percentage")],
        [
            instrument("sp500", "international-etfs", "700", "1", target="50"),
            instrument("eimi", "international-etfs", "100", "1", target="30"),
            instrument("other", "international-etfs", "200", "1", target="20"),
        ],
        Decimal("200"),
    )

    assert {row.instrument_id: row.suggested_value for row in result.allocations} == {
        "eimi": Decimal("160.00"),
        "other": Decimal("40.00"),
    }
    assert result.portfolio_total == Decimal("1000.00")


def test_percentage_mode_exclusion_keeps_existing_value_and_normalizes_targets() -> None:
    result = calculate(
        [rule("international-etfs", "100", "cash_amount", 0, "percentage")],
        [
            instrument("sp500", "international-etfs", "0", None, target="50"),
            instrument("eimi", "international-etfs", "300", None, target="30"),
            instrument("other", "international-etfs", "0", None, target="20"),
        ],
        Decimal("70"),
        {"eimi"},
    )

    assert result.portfolio_total == Decimal("300.00")
    assert {row.instrument_id for row in result.allocations} == {"sp500", "other"}
    assert sum(row.suggested_value for row in result.allocations) == Decimal("70.00")
