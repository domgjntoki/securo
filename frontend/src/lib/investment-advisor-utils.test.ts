import { describe, expect, it } from 'vitest'

import { calculateInstrumentPortfolioPercentages, groupContributionAllocations, searchHoldingMatches, summarizeAdvisorWarnings } from './investment-advisor-utils'
import type { Asset, ContributionAllocation, ContributionPreview, InvestmentStrategy, StrategyClass, StrategyInstrument } from '../types'

function allocation(classId: string | null, className: string, instrumentName: string): ContributionAllocation {
  return {
    instrument_id: instrumentName,
    instrument_name: instrumentName,
    class_id: classId,
    class_name: className,
    current_value: 0,
    current_quantity: 0,
    unit_price: 10,
    strength: 1,
    target_percentage: null,
    suggested_value: 10,
    suggested_quantity: 1,
    after_percentage: 10,
    excluded: false,
  }
}

describe('summarizeAdvisorWarnings', () => {
  it('extracts valid price timestamps without exposing their internal warning codes', () => {
    const result = summarizeAdvisorWarnings([
      'ETF:price_as_of:2026-08-22T22:50:32.596465+00:00',
      'Equities:no_allocatable_instrument',
    ])

    expect(result.priceTimestamps).toHaveLength(1)
    expect(result.priceTimestamps[0].toISOString()).toBe('2026-08-22T22:50:32.596Z')
    expect(result.warnings).toEqual(['Equities:no_allocatable_instrument'])
  })

  it('retains malformed timestamp warnings for diagnosis', () => {
    expect(summarizeAdvisorWarnings(['ETF:price_as_of:not-a-date']).warnings).toEqual([
      'ETF:price_as_of:not-a-date',
    ])
  })
})

describe('groupContributionAllocations', () => {
  it('groups allocations in strategy class order', () => {
    const preview = {
      allocations: [
        allocation('b', 'National equities', 'WEGE3'),
        allocation('a', 'International equities', 'EIMI'),
        allocation('b', 'National equities', 'BBAS3'),
      ],
      class_snapshot: [
        { id: 'a', name: 'International equities' },
        { id: 'b', name: 'National equities' },
      ],
    } as ContributionPreview

    const groups = groupContributionAllocations(preview)
    expect(groups.map((group) => group.name)).toEqual(['International equities', 'National equities'])
    expect(groups[1].allocations.map((item) => item.instrument_name)).toEqual(['WEGE3', 'BBAS3'])
  })
})

describe('searchHoldingMatches', () => {
  const holding = (overrides: Partial<Asset>): Asset => ({
    id: 'asset-1',
    user_id: 'user-1',
    name: 'Fundo Imobiliário XPTO',
    type: 'investment',
    currency: 'BRL',
    units: 12,
    valuation_method: 'market_price',
    purchase_date: null,
    purchase_price: null,
    sell_date: null,
    sell_price: null,
    growth_type: null,
    growth_rate: null,
    growth_frequency: null,
    growth_start_date: null,
    is_archived: false,
    position: 0,
    current_value: 1200,
    current_value_primary: 1200,
    gain_loss: null,
    gain_loss_primary: null,
    value_count: 0,
    source: 'manual',
    connection_id: null,
    isin: 'BRXPTOCTF001',
    maturity_date: null,
    group_id: 'wallet-1',
    ticker: 'XPTO11',
    ticker_exchange: 'BVMF',
    last_price: 100,
    last_price_at: null,
    logo_url: null,
    average_price: null,
    total_invested: null,
    realized_gain: null,
    transaction_count: 0,
    ...overrides,
  })

  it('finds selected-wallet holdings by partial, accent-insensitive name or identifier', () => {
    const holdings = [
      holding({}),
      holding({ id: 'outside', name: 'XPTO outside', group_id: 'wallet-2' }),
      holding({ id: 'archived', name: 'XPTO archived', is_archived: true }),
    ]

    expect(searchHoldingMatches(holdings, ['wallet-1'], 'imobiliario xpto', []).map((item) => item.asset_id)).toEqual(['asset-1'])
    expect(searchHoldingMatches(holdings, ['wallet-1'], 'brxpto', []).map((item) => item.asset_id)).toEqual(['asset-1'])
  })

  it('keeps manually linked holdings visible when the search is empty', () => {
    const result = searchHoldingMatches([holding({})], ['wallet-1'], '', ['asset-1'])

    expect(result).toEqual([expect.objectContaining({ asset_id: 'asset-1', already_linked: true })])
  })
})

describe('calculateInstrumentPortfolioPercentages', () => {
  const strategyClass = (id: string, target: number, scoringMode: StrategyClass['scoring_mode']): StrategyClass => ({
    id,
    template_key: null,
    name: id,
    target_percentage: target,
    scoring_mode: scoringMode,
    purchase_mode: 'fractional_units',
    quantity_decimals: 4,
    question_bank_id: null,
    display_currency: null,
    position: 0,
    is_archived: false,
  })
  const instrument = (id: string, classId: string, currentValue: number, strength: number | null, target: number | null = null): StrategyInstrument => ({
    id,
    class_id: classId,
    name: id,
    ticker: null,
    exchange: null,
    currency: 'BRL',
    isin: null,
    manual_price: 1,
    cached_price_at: null,
    price_source: 'manual',
    manual_strength: strength,
    target_percentage: target,
    strength,
    allocatable: true,
    current_value: currentValue,
    current_quantity: currentValue,
    unit_price: 1,
    linked_asset_ids: [],
    yes_question_ids: [],
    warnings: [],
  })

  it('combines category targets with manual, percentage, and questionnaire weights', () => {
    const strategy = {
      classes: [
        strategyClass('manual', 50, 'manual'),
        strategyClass('percentage', 30, 'percentage'),
        strategyClass('questionnaire', 20, 'questionnaire'),
      ],
      instruments: [
        instrument('manual-3', 'manual', 300, 3),
        instrument('manual-1', 'manual', 100, 1),
        instrument('percentage-25', 'percentage', 100, null, 25),
        instrument('percentage-75', 'percentage', 300, null, 75),
        instrument('questionnaire-positive', 'questionnaire', 100, 2),
        instrument('questionnaire-negative', 'questionnaire', 100, -2),
      ],
    } as Pick<InvestmentStrategy, 'classes' | 'instruments'>

    const result = calculateInstrumentPortfolioPercentages(strategy)

    expect(result.get('manual-3')).toEqual({ current: 30, ideal: 37.5 })
    expect(result.get('manual-1')?.ideal).toBe(12.5)
    expect(result.get('percentage-25')?.ideal).toBe(7.5)
    expect(result.get('percentage-75')?.ideal).toBe(22.5)
    expect(result.get('questionnaire-positive')?.ideal).toBe(20)
    expect(result.get('questionnaire-negative')?.ideal).toBe(0)
  })

  it('uses current proportions when every manual score is zero', () => {
    const strategy = {
      classes: [strategyClass('manual', 100, 'manual')],
      instruments: [
        instrument('larger', 'manual', 300, 0),
        instrument('smaller', 'manual', 100, 0),
      ],
    } as Pick<InvestmentStrategy, 'classes' | 'instruments'>

    const result = calculateInstrumentPortfolioPercentages(strategy)

    expect(result.get('larger')).toEqual({ current: 75, ideal: 75 })
    expect(result.get('smaller')).toEqual({ current: 25, ideal: 25 })
  })
})
