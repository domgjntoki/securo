import { describe, expect, it } from 'vitest'

import { groupContributionAllocations, summarizeAdvisorWarnings } from './investment-advisor-utils'
import type { ContributionAllocation, ContributionPreview } from '../types'

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
