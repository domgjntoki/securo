import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import func, select

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.fx_rate import FxRate
from app.models.investment_advisor import InvestmentContributionPlan
from app.schemas.asset import MarketSymbolQuote


async def test_strategy_zero_position_preview_save_and_execution_tracking(
    client, auth_headers, session
) -> None:
    response = await client.post(
        "/api/investment-strategies",
        headers=auth_headers,
        json={"name": "Retirement", "currency": "BRL", "home_country": "BR", "wallet_ids": []},
    )
    assert response.status_code == 201, response.text
    strategy = response.json()
    assert len(strategy["classes"]) == 7
    assert len(strategy["question_banks"]) == 2

    crypto = next(row for row in strategy["classes"] if row["template_key"] == "cryptoassets")
    targets = [
        {"class_id": row["id"], "target_percentage": 100 if row["id"] == crypto["id"] else 0}
        for row in strategy["classes"]
    ]
    response = await client.put(
        f"/api/investment-strategies/{strategy['id']}/targets",
        headers=auth_headers,
        json={"targets": targets},
    )
    assert response.status_code == 204, response.text

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": crypto["id"],
            "name": "Bitcoin",
            "ticker": "BTC-BRL",
            "currency": "BRL",
            "current_price": 500000,
            "manual_strength": 0,
            "asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 1000, "exclude_instrument_ids": []},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["portfolio_total"] == "0.00"
    assert preview["residual"] == "0.00"
    assert len(preview["allocations"]) == 1

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 1000, "exclude_instrument_ids": []},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    allocation = plan["allocations"][0]

    before_assets = await session.scalar(select(func.count()).select_from(Asset))
    before_transactions = await session.scalar(
        select(func.count()).select_from(AssetTransaction)
    )
    response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}/plans/{plan['id']}/allocations/{allocation['id']}",
        headers=auth_headers,
        json={"executed": True, "actual_value": 999, "actual_quantity": 0.002},
    )
    assert response.status_code == 200, response.text
    assert response.json()["allocations"][0]["actual_value"] == "999.00"
    after_assets = await session.scalar(select(func.count()).select_from(Asset))
    after_transactions = await session.scalar(
        select(func.count()).select_from(AssetTransaction)
    )
    assert before_assets == after_assets == 0
    assert before_transactions == after_transactions == 0


async def test_saved_plan_refreshes_only_unfinished_prices_without_mutating_snapshot(
    client, auth_headers, viewer_auth_headers
) -> None:
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    instrument_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Market candidate",
            "ticker": "MARKET-BRL",
            "currency": "BRL",
            "current_price": 10,
            "price_source": "market",
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert instrument_response.status_code == 201, instrument_response.text
    plan_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert plan_response.status_code == 201, plan_response.text
    saved_plan = plan_response.json()
    saved_allocation = saved_plan["allocations"][0]
    assert Decimal(saved_allocation["unit_price"]) == Decimal("10")
    assert Decimal(saved_allocation["suggested_quantity"]) == Decimal("10")

    provider = Mock()
    provider.get_quotes = AsyncMock(
        return_value={
            "MARKET-BRL": MarketSymbolQuote(
                symbol="MARKET-BRL",
                name="Market candidate",
                currency="BRL",
                price=20,
            )
        }
    )
    with patch(
        "app.services.investment_advisor_service.get_market_price_provider",
        return_value=provider,
    ):
        refreshed_response = await client.post(
            f"/api/investment-strategies/{strategy['id']}/plans/{saved_plan['id']}/refresh-prices",
            headers=auth_headers,
        )
    assert refreshed_response.status_code == 200, refreshed_response.text
    refreshed = refreshed_response.json()
    assert refreshed["currency"] == "BRL"
    assert len(refreshed["allocations"]) == 1
    current = refreshed["allocations"][0]
    assert Decimal(current["unit_price"]) == Decimal("20")
    assert Decimal(current["estimated_quantity"]) == Decimal("5")
    assert Decimal(current["estimated_value"]) == Decimal("100")

    historical = (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/plans/{saved_plan['id']}",
            headers=auth_headers,
        )
    ).json()
    assert Decimal(historical["allocations"][0]["unit_price"]) == Decimal("10")
    assert Decimal(historical["allocations"][0]["suggested_quantity"]) == Decimal("10")
    assert historical["instrument_snapshot"] == saved_plan["instrument_snapshot"]

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans/{saved_plan['id']}/refresh-prices",
        headers=viewer_auth_headers,
    )
    assert response.status_code == 403

    execution_response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}/plans/{saved_plan['id']}/allocations/{saved_allocation['id']}",
        headers=auth_headers,
        json={
            "executed": True,
            "actual_value": current["estimated_value"],
            "actual_quantity": current["estimated_quantity"],
        },
    )
    assert execution_response.status_code == 200, execution_response.text
    with patch(
        "app.services.investment_advisor_service.get_market_price_provider",
        return_value=provider,
    ):
        finished_refresh = await client.post(
            f"/api/investment-strategies/{strategy['id']}/plans/{saved_plan['id']}/refresh-prices",
            headers=auth_headers,
        )
    assert finished_refresh.status_code == 200, finished_refresh.text
    assert finished_refresh.json()["allocations"] == []


async def test_saved_plan_delete_requires_write_access_and_does_not_touch_holdings(
    client, auth_headers, viewer_auth_headers, session
) -> None:
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    instrument = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Deletable plan candidate",
            "currency": "BRL",
            "current_price": 10,
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert instrument.status_code == 201, instrument.text
    plan_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    before_assets = await session.scalar(select(func.count()).select_from(Asset))
    before_transactions = await session.scalar(
        select(func.count()).select_from(AssetTransaction)
    )

    forbidden = await client.delete(
        f"/api/investment-strategies/{strategy['id']}/plans/{plan['id']}",
        headers=viewer_auth_headers,
    )
    assert forbidden.status_code == 403
    response = await client.delete(
        f"/api/investment-strategies/{strategy['id']}/plans/{plan['id']}",
        headers=auth_headers,
    )
    assert response.status_code == 204, response.text
    assert (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/plans/{plan['id']}",
            headers=auth_headers,
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/plans",
            headers=auth_headers,
        )
    ).json() == []
    assert await session.scalar(select(func.count()).select_from(Asset)) == before_assets
    assert (
        await session.scalar(select(func.count()).select_from(AssetTransaction))
        == before_transactions
    )


async def test_permanent_strategy_delete_requires_write_and_preserves_securo_holdings(
    client,
    auth_headers,
    viewer_auth_headers,
    session,
    test_user,
    test_workspace,
) -> None:
    wallet = AssetGroup(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Deletion safety wallet",
    )
    session.add(wallet)
    await session.flush()
    holding = Asset(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=wallet.id,
        name="Preserved holding",
        type="investment",
        currency="BRL",
        units=Decimal("2"),
        valuation_method="market_price",
        ticker="SAFE3.SA",
        ticker_exchange="BVMF",
        last_price=Decimal("10"),
    )
    session.add(holding)
    await session.commit()

    strategy = await _strategy(
        client, auth_headers, wallet_ids=[str(wallet.id)]
    )
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    instrument_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Linked strategy instrument",
            "ticker": "SAFE3.SA",
            "exchange": "BVMF",
            "currency": "BRL",
            "manual_strength": 1,
            "asset_ids": [str(holding.id)],
        },
    )
    assert instrument_response.status_code == 201, instrument_response.text
    plan_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert plan_response.status_code == 201, plan_response.text
    before_assets = await session.scalar(select(func.count()).select_from(Asset))
    before_transactions = await session.scalar(
        select(func.count()).select_from(AssetTransaction)
    )

    forbidden = await client.delete(
        f"/api/investment-strategies/{strategy['id']}/permanent",
        headers=viewer_auth_headers,
    )
    assert forbidden.status_code == 403
    response = await client.delete(
        f"/api/investment-strategies/{strategy['id']}/permanent",
        headers=auth_headers,
    )
    assert response.status_code == 204, response.text
    assert (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}", headers=auth_headers
        )
    ).status_code == 404
    assert await session.scalar(
        select(func.count()).select_from(InvestmentContributionPlan).where(
            InvestmentContributionPlan.strategy_id == uuid.UUID(strategy["id"])
        )
    ) == 0
    assert await session.scalar(select(func.count()).select_from(Asset)) == before_assets
    assert (
        await session.scalar(select(func.count()).select_from(AssetTransaction))
        == before_transactions
    )


async def test_viewer_cannot_create_strategy(client, viewer_auth_headers) -> None:
    response = await client.post(
        "/api/investment-strategies",
        headers=viewer_auth_headers,
        json={"name": "Blocked", "currency": "USD", "home_country": "US", "wallet_ids": []},
    )
    assert response.status_code == 403


async def test_viewer_can_read_preview_and_history(
    client, auth_headers, viewer_auth_headers
) -> None:
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    instrument = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Viewer-readable candidate",
            "currency": "BRL",
            "current_price": 10,
            "manual_strength": 0,
            "asset_ids": [],
        },
    )
    assert instrument.status_code == 201, instrument.text
    saved = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert saved.status_code == 201, saved.text
    assert (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}",
            headers=viewer_auth_headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/investment-strategies/{strategy['id']}/preview",
            headers=viewer_auth_headers,
            json={"amount": 100},
        )
    ).status_code == 200
    assert (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/plans",
            headers=viewer_auth_headers,
        )
    ).status_code == 200


async def _strategy(client, auth_headers, **overrides):
    payload = {
        "name": "Advisor test",
        "currency": "BRL",
        "home_country": "BR",
        "wallet_ids": [],
        **overrides,
    }
    response = await client.post(
        "/api/investment-strategies", headers=auth_headers, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _target_one_class(client, auth_headers, strategy, template_key="cryptoassets"):
    selected = next(
        row for row in strategy["classes"] if row["template_key"] == template_key
    )
    response = await client.put(
        f"/api/investment-strategies/{strategy['id']}/targets",
        headers=auth_headers,
        json={
            "targets": [
                {
                    "class_id": row["id"],
                    "target_percentage": 100 if row["id"] == selected["id"] else 0,
                }
                for row in strategy["classes"]
            ]
        },
    )
    assert response.status_code == 204, response.text
    return selected


async def test_matching_prefers_isin_aggregates_selected_wallets_and_requires_confirmation(
    client, auth_headers, session, test_user, test_workspace
) -> None:
    selected_a = AssetGroup(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Broker A",
    )
    selected_b = AssetGroup(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Broker B",
    )
    outside = AssetGroup(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Outside boundary",
    )
    session.add_all([selected_a, selected_b, outside])
    await session.flush()

    def holding(name, wallet, *, isin, ticker="FUND", exchange="NYSE", units="1"):
        return Asset(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            group_id=wallet.id,
            name=name,
            type="investment",
            currency="USD",
            units=Decimal(units),
            valuation_method="market_price",
            ticker=ticker,
            ticker_exchange=exchange,
            isin=isin,
            last_price=Decimal("10"),
        )

    first = holding("Exact A", selected_a, isin="US0000000001", units="2")
    second = holding("Exact B", selected_b, isin="US0000000001", units="3")
    weaker = holding("Ticker only", selected_a, isin="US9999999999", units="4")
    out_of_scope = holding("Outside", outside, isin="US0000000001", units="5")
    session.add_all([first, second, weaker, out_of_scope])
    await session.commit()

    strategy = await _strategy(
        client,
        auth_headers,
        currency="USD",
        home_country="US",
        wallet_ids=[str(selected_a.id), str(selected_b.id)],
    )
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Fund candidate",
            "ticker": "FUND",
            "exchange": "NYSE",
            "currency": "USD",
            "isin": "US0000000001",
            "current_price": 10,
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    instrument = response.json()
    assert instrument["linked_asset_ids"] == []
    assert instrument["current_value"] == "0.00"

    response = await client.get(
        f"/api/investment-strategies/{strategy['id']}/instruments/{instrument['id']}/matches",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    matches = response.json()
    assert {row["asset_id"] for row in matches} == {str(first.id), str(second.id)}
    assert {row["match_kind"] for row in matches} == {"isin"}

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments/{instrument['id']}/matches/confirm",
        headers=auth_headers,
        json={"asset_ids": [str(first.id), str(second.id)]},
    )
    assert response.status_code == 200, response.text
    linked = response.json()
    assert set(linked["linked_asset_ids"]) == {str(first.id), str(second.id)}
    assert linked["current_value"] == "50.00"
    assert Decimal(linked["current_quantity"]) == Decimal("5")


async def test_normalized_ticker_matching_is_ambiguous_until_subset_confirmed(
    client, auth_headers, session, test_user, test_workspace
) -> None:
    wallets = [
        AssetGroup(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            name=f"Wallet {number}",
        )
        for number in (1, 2)
    ]
    session.add_all(wallets)
    await session.flush()
    holdings = [
        Asset(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            group_id=wallet.id,
            name=f"PETR {number}",
            type="investment",
            currency="brl" if number == 1 else "BRL",
            units=Decimal("1"),
            valuation_method="market_price",
            ticker=" petr4.sa " if number == 1 else "PETR4.SA",
            ticker_exchange="bvmf" if number == 1 else "BVMF",
            last_price=Decimal("30"),
        )
        for number, wallet in enumerate(wallets, 1)
    ]
    session.add_all(holdings)
    await session.commit()
    strategy = await _strategy(
        client, auth_headers, wallet_ids=[str(wallet.id) for wallet in wallets]
    )
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Petrobras",
            "ticker": "PETR4.SA",
            "exchange": "BVMF",
            "currency": "BRL",
            "current_price": 30,
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    instrument = response.json()
    matches = (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/instruments/{instrument['id']}/matches",
            headers=auth_headers,
        )
    ).json()
    assert len(matches) == 2
    assert {row["match_kind"] for row in matches} == {"ticker_exchange_currency"}
    assert instrument["linked_asset_ids"] == []

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments/{instrument['id']}/matches/confirm",
        headers=auth_headers,
        json={"asset_ids": [str(holdings[0].id)]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["linked_asset_ids"] == [str(holdings[0].id)]
    response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}",
        headers=auth_headers,
        json={"wallet_ids": [str(wallets[1].id)]},
    )
    assert response.status_code == 422
    assert "Unlink strategy holdings" in str(response.json())


async def test_required_unit_price_is_a_blocking_configuration_error(
    client, auth_headers
) -> None:
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Unpriced candidate",
            "currency": "BRL",
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert response.status_code == 422
    assert "required unit price is missing" in str(response.json())


async def test_custom_cash_class_allows_zero_score_and_rejects_negative_score(
    client, auth_headers
) -> None:
    strategy = await _strategy(client, auth_headers)
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/classes",
        headers=auth_headers,
        json={
            "name": "Private credit",
            "target_percentage": 0,
            "scoring_mode": "manual",
            "purchase_mode": "cash_amount",
            "quantity_decimals": 0,
            "position": 10,
        },
    )
    assert response.status_code == 201, response.text
    custom = response.json()
    strategy = (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}", headers=auth_headers
        )
    ).json()
    response = await client.put(
        f"/api/investment-strategies/{strategy['id']}/targets",
        headers=auth_headers,
        json={
            "targets": [
                {
                    "class_id": row["id"],
                    "target_percentage": 100 if row["id"] == custom["id"] else 0,
                }
                for row in strategy["classes"]
            ]
        },
    )
    assert response.status_code == 204, response.text
    instruments = []
    for name, score in (("Eligible zero", 0), ("Ineligible negative", -1)):
        response = await client.post(
            f"/api/investment-strategies/{strategy['id']}/instruments",
            headers=auth_headers,
            json={
                "class_id": custom["id"],
                "name": name,
                "currency": "BRL",
                "manual_strength": score,
                "asset_ids": [],
            },
        )
        assert response.status_code == 201, response.text
        instruments.append(response.json())
    assert instruments[0]["allocatable"] is True
    assert instruments[1]["allocatable"] is False
    preview = (
        await client.post(
            f"/api/investment-strategies/{strategy['id']}/preview",
            headers=auth_headers,
            json={"amount": 100},
        )
    ).json()
    assert [row["instrument_name"] for row in preview["allocations"]] == [
        "Eligible zero"
    ]
    assert preview["allocations"][0]["suggested_value"] == "100.00"


async def test_cross_currency_preview_blocks_missing_rates_and_plan_keeps_fx_snapshot(
    client, auth_headers, session
) -> None:
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date.today(),
            rate=Decimal("5"),
            source="test",
        )
    )
    await session.commit()
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "USD candidate",
            "ticker": "USD-FUND",
            "currency": "USD",
            "current_price": 10,
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    before_plans = await session.scalar(
        select(func.count()).select_from(InvestmentContributionPlan)
    )
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["fx_rates"]["USD"] == "5.0000000000"
    assert Decimal(preview["allocations"][0]["unit_price"]) == Decimal("50")
    assert preview["allocations"][0]["suggested_quantity"] == "2.0000"
    after_preview_plans = await session.scalar(
        select(func.count()).select_from(InvestmentContributionPlan)
    )
    assert before_plans == after_preview_plans

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert response.status_code == 201, response.text
    saved = response.json()
    rate = await session.scalar(
        select(FxRate).where(FxRate.quote_currency == "BRL")
    )
    rate.rate = Decimal("6")
    await session.commit()
    historical = (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/plans/{saved['id']}",
            headers=auth_headers,
        )
    ).json()
    assert historical["fx_rates"]["USD"] == "5.0000000000"
    assert historical["class_snapshot"] == saved["class_snapshot"]
    assert historical["instrument_snapshot"] == saved["instrument_snapshot"]

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "Missing EUR rate",
            "ticker": "EUR-FUND",
            "currency": "EUR",
            "current_price": 10,
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert response.status_code == 422
    assert "no FX rate for EUR/BRL" in str(response.json())


async def test_class_display_currency_is_snapshotted_without_changing_plan_currency(
    client, auth_headers, session
) -> None:
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=date.today(),
            rate=Decimal("5"),
            source="test",
        )
    )
    await session.commit()
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(client, auth_headers, strategy)
    response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}/classes/{strategy_class['id']}",
        headers=auth_headers,
        json={"display_currency": "usd"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_currency"] == "USD"

    response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/instruments",
        headers=auth_headers,
        json={
            "class_id": strategy_class["id"],
            "name": "BRL candidate",
            "currency": "BRL",
            "current_price": 10,
            "manual_strength": 1,
            "asset_ids": [],
        },
    )
    assert response.status_code == 201, response.text

    preview_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["currency"] == "BRL"
    assert preview["fx_rates"]["USD"] == "5.0000000000"
    preview_class = next(
        row for row in preview["class_snapshot"] if row["id"] == strategy_class["id"]
    )
    assert preview_class["display_currency"] == "USD"

    saved_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert saved_response.status_code == 201, saved_response.text
    saved = saved_response.json()
    assert saved["currency"] == "BRL"
    assert saved["fx_rates"]["USD"] == "5.0000000000"
    saved_class = next(
        row for row in saved["class_snapshot"] if row["id"] == strategy_class["id"]
    )
    assert saved_class["display_currency"] == "USD"

    response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}/classes/{strategy_class['id']}",
        headers=auth_headers,
        json={"display_currency": "EUR"},
    )
    assert response.status_code == 200, response.text
    missing_rate_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 100},
    )
    assert missing_rate_response.status_code == 422
    assert "missing FX rate EUR/BRL" in str(missing_rate_response.json())


async def test_percentage_class_validates_targets_and_snapshots_allocations(
    client, auth_headers
) -> None:
    strategy = await _strategy(client, auth_headers)
    strategy_class = await _target_one_class(
        client, auth_headers, strategy, template_key="international_equities"
    )
    response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}/classes/{strategy_class['id']}",
        headers=auth_headers,
        json={"scoring_mode": "percentage"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scoring_mode"] == "percentage"

    instruments = []
    for name, ticker, target, price in (
        ("S&P 500 ETF", "SPY", 50, 1),
        ("Emerging markets ETF", "EIMI", 30, 1),
        ("Developed markets ETF", "IWDA", 20, 1),
        ("No-allocation candidate", "LATER", 0, None),
    ):
        response = await client.post(
            f"/api/investment-strategies/{strategy['id']}/instruments",
            headers=auth_headers,
            json={
                "class_id": strategy_class["id"],
                "name": name,
                "ticker": ticker,
                "currency": "BRL",
                "current_price": price,
                "target_percentage": target,
                "asset_ids": [],
            },
        )
        assert response.status_code == 201, response.text
        instruments.append(response.json())
    assert instruments[-1]["allocatable"] is False
    assert instruments[-1]["target_percentage"] == "0.0000"

    preview_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 1000},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["algorithm_version"] == "securo-advisor-v2"
    assert {
        row["instrument_name"]: row["suggested_value"]
        for row in preview["allocations"]
    } == {
        "S&P 500 ETF": "500.00",
        "Emerging markets ETF": "300.00",
        "Developed markets ETF": "200.00",
    }
    assert {row["target_percentage"] for row in preview["allocations"]} == {
        "50.0000",
        "30.0000",
        "20.0000",
    }
    saved_response = await client.post(
        f"/api/investment-strategies/{strategy['id']}/plans",
        headers=auth_headers,
        json={"amount": 1000},
    )
    assert saved_response.status_code == 201, saved_response.text
    saved = saved_response.json()
    assert {row["target_percentage"] for row in saved["instrument_snapshot"]} == {
        "0.0000",
        "20.0000",
        "30.0000",
        "50.0000",
    }

    response = await client.patch(
        f"/api/investment-strategies/{strategy['id']}/instruments/{instruments[0]['id']}",
        headers=auth_headers,
        json={"target_percentage": 49},
    )
    assert response.status_code == 200, response.text
    invalid = await client.post(
        f"/api/investment-strategies/{strategy['id']}/preview",
        headers=auth_headers,
        json={"amount": 1000},
    )
    assert invalid.status_code == 422
    assert "must total exactly 100" in str(invalid.json())
    historical = await client.get(
        f"/api/investment-strategies/{strategy['id']}/plans/{saved['id']}",
        headers=auth_headers,
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["instrument_snapshot"] == saved["instrument_snapshot"]


async def test_questionnaire_eligibility_target_validation_and_archived_history(
    client, auth_headers
) -> None:
    strategy = await _strategy(client, auth_headers)
    equity = next(
        row
        for row in strategy["classes"]
        if row["template_key"] == "national_equities"
    )
    invalid = await client.put(
        f"/api/investment-strategies/{strategy['id']}/targets",
        headers=auth_headers,
        json={"targets": [{"class_id": equity["id"], "target_percentage": 99}]},
    )
    assert invalid.status_code == 422
    equity = await _target_one_class(
        client, auth_headers, strategy, template_key="national_equities"
    )
    bank = next(
        item for item in strategy["question_banks"] if item["id"] == equity["question_bank_id"]
    )
    blank_category = await client.post(
        f"/api/investment-strategies/{strategy['id']}/question-banks/{bank['id']}/questions",
        headers=auth_headers,
        json={"label": "   ", "text": "A question without a category"},
    )
    assert blank_category.status_code == 422
    questions = []
    for index, text in enumerate(("Criterion one", "Criterion two", "Criterion three"), 1):
        response = await client.post(
            f"/api/investment-strategies/{strategy['id']}/question-banks/{bank['id']}/questions",
            headers=auth_headers,
            json={"label": f"Category {index}", "text": text},
        )
        assert response.status_code == 201, response.text
        assert response.json()["label"] == f"Category {index}"
        questions.append(response.json())
    instrument = (
        await client.post(
            f"/api/investment-strategies/{strategy['id']}/instruments",
            headers=auth_headers,
            json={
                "class_id": equity["id"],
                "name": "Questionnaire stock",
                "currency": "BRL",
                "current_price": 10,
                "yes_question_ids": [questions[0]["id"], questions[1]["id"]],
                "asset_ids": [],
            },
        )
    ).json()
    assert set(instrument["yes_question_ids"]) == {questions[0]["id"], questions[1]["id"]}
    assert instrument["strength"] == 1
    assert instrument["allocatable"] is True

    plan = (
        await client.post(
            f"/api/investment-strategies/{strategy['id']}/plans",
            headers=auth_headers,
            json={"amount": 100},
        )
    ).json()
    response = await client.delete(
        f"/api/investment-strategies/{strategy['id']}", headers=auth_headers
    )
    assert response.status_code == 204
    assert (
        await client.get(
            "/api/investment-strategies", headers=auth_headers
        )
    ).json() == []
    archived = await client.get(
        "/api/investment-strategies?include_archived=true", headers=auth_headers
    )
    assert archived.status_code == 200
    assert archived.json()[0]["is_archived"] is True
    assert (
        await client.get(
            f"/api/investment-strategies/{strategy['id']}/plans/{plan['id']}",
            headers=auth_headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/investment-strategies/{strategy['id']}/preview",
            headers=auth_headers,
            json={"amount": 100},
        )
    ).status_code == 409
