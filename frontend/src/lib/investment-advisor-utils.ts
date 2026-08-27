import type { Asset, ContributionAllocation, ContributionPreview, InstrumentMatchCandidate, InvestmentStrategy, StrategyInstrument } from '../types'

export interface AdvisorWarningSummary {
  priceTimestamps: Date[]
  warnings: string[]
}

export interface ContributionAllocationGroup {
  id: string
  name: string
  allocations: ContributionAllocation[]
}

export interface InstrumentPortfolioPercentage {
  current: number
  ideal: number
}

function positiveValue(value: number | null | undefined): number {
  const numeric = Number(value ?? 0)
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0
}

export function calculateInstrumentPortfolioPercentages(
  strategy: Pick<InvestmentStrategy, 'classes' | 'instruments'>,
): Map<string, InstrumentPortfolioPercentage> {
  const result = new Map<string, InstrumentPortfolioPercentage>()
  const portfolioTotal = strategy.instruments.reduce(
    (total, instrument) => total + positiveValue(instrument.current_value),
    0,
  )

  for (const instrument of strategy.instruments) {
    result.set(instrument.id, {
      current: portfolioTotal > 0 ? positiveValue(instrument.current_value) / portfolioTotal * 100 : 0,
      ideal: 0,
    })
  }

  for (const strategyClass of strategy.classes.filter((item) => !item.is_archived)) {
    const members = strategy.instruments.filter((item) => item.class_id === strategyClass.id)
    if (!members.length || strategyClass.target_percentage <= 0) continue

    let weightedMembers: Array<{ instrument: StrategyInstrument; weight: number }> = []
    if (strategyClass.scoring_mode === 'percentage') {
      weightedMembers = members.map((instrument) => ({
        instrument,
        weight: positiveValue(instrument.target_percentage),
      }))
    } else {
      const eligible = members.filter((instrument) => (
        strategyClass.scoring_mode === 'questionnaire'
          ? positiveValue(instrument.strength) > 0
          : instrument.strength != null && Number(instrument.strength) >= 0
      ))
      const strengthTotal = eligible.reduce(
        (total, instrument) => total + positiveValue(instrument.strength),
        0,
      )
      if (strengthTotal > 0) {
        weightedMembers = eligible.map((instrument) => ({
          instrument,
          weight: positiveValue(instrument.strength),
        }))
      } else if (strategyClass.scoring_mode === 'manual' && eligible.length) {
        const currentValueTotal = eligible.reduce(
          (total, instrument) => total + positiveValue(instrument.current_value),
          0,
        )
        weightedMembers = eligible.map((instrument) => ({
          instrument,
          weight: currentValueTotal > 0 ? positiveValue(instrument.current_value) : 1,
        }))
      }
    }

    const weightTotal = weightedMembers.reduce((total, item) => total + item.weight, 0)
    if (weightTotal <= 0) continue
    for (const { instrument, weight } of weightedMembers) {
      const percentages = result.get(instrument.id)
      if (percentages) percentages.ideal = positiveValue(strategyClass.target_percentage) * weight / weightTotal
    }
  }

  return result
}

function normalizedSearchText(value: string | null | undefined): string {
  return (value ?? '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase()
}

export function searchHoldingMatches(
  holdings: Asset[],
  walletIds: string[],
  query: string,
  linkedAssetIds: string[],
): InstrumentMatchCandidate[] {
  const allowedWalletIds = new Set(walletIds)
  const linkedIds = new Set(linkedAssetIds)
  const terms = normalizedSearchText(query).trim().split(/\s+/).filter(Boolean)

  return holdings
    .filter((holding) => {
      if (!holding.group_id || !allowedWalletIds.has(holding.group_id) || holding.is_archived) return false
      if (!terms.length) return linkedIds.has(holding.id)
      const searchable = normalizedSearchText([
        holding.name,
        holding.ticker,
        holding.ticker_exchange,
        holding.isin,
        holding.currency,
      ].filter(Boolean).join(' '))
      return terms.every((term) => searchable.includes(term))
    })
    .sort((left, right) => {
      const linkedDifference = Number(linkedIds.has(right.id)) - Number(linkedIds.has(left.id))
      return linkedDifference || left.name.localeCompare(right.name)
    })
    .map((holding) => ({
      asset_id: holding.id,
      asset_name: holding.name,
      wallet_id: holding.group_id!,
      match_kind: 'manual_search',
      ticker: holding.ticker,
      exchange: holding.ticker_exchange,
      currency: holding.currency,
      isin: holding.isin,
      current_value: holding.current_value,
      current_quantity: Number(holding.units ?? 0),
      already_linked: linkedIds.has(holding.id),
    }))
}

const PRICE_AS_OF_WARNING = /^(.*):price_as_of:(.+)$/

export function summarizeAdvisorWarnings(warnings: string[]): AdvisorWarningSummary {
  const summary: AdvisorWarningSummary = { priceTimestamps: [], warnings: [] }

  for (const warning of warnings) {
    const match = PRICE_AS_OF_WARNING.exec(warning)
    if (!match) {
      summary.warnings.push(warning)
      continue
    }

    const timestamp = new Date(match[2])
    if (Number.isNaN(timestamp.getTime())) {
      summary.warnings.push(warning)
      continue
    }
    summary.priceTimestamps.push(timestamp)
  }

  return summary
}

export function groupContributionAllocations(preview: ContributionPreview): ContributionAllocationGroup[] {
  const allocationsByClass = new Map<string, ContributionAllocation[]>()
  for (const allocation of preview.allocations) {
    const key = allocation.class_id ?? `name:${allocation.class_name}`
    const group = allocationsByClass.get(key) ?? []
    group.push(allocation)
    allocationsByClass.set(key, group)
  }

  const groups: ContributionAllocationGroup[] = []
  for (const strategyClass of preview.class_snapshot) {
    const allocations = allocationsByClass.get(strategyClass.id)
    if (!allocations?.length) continue
    groups.push({ id: strategyClass.id, name: strategyClass.name, allocations })
    allocationsByClass.delete(strategyClass.id)
  }

  for (const [id, allocations] of allocationsByClass) {
    groups.push({ id, name: allocations[0].class_name, allocations })
  }
  return groups
}
