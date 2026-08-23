import type { ContributionAllocation, ContributionPreview } from '../types'

export interface AdvisorWarningSummary {
  priceTimestamps: Date[]
  warnings: string[]
}

export interface ContributionAllocationGroup {
  id: string
  name: string
  allocations: ContributionAllocation[]
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
