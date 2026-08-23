import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Archive, ArrowLeft, Check, Compass, History, Link2, LoaderCircle, Plus, RefreshCw, Save, Search, Settings2, Sparkles, Trash2, X } from 'lucide-react'

import { PageHeader } from '@/components/page-header'
import { CurrencySelect } from '@/components/currency-select'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useWorkspace } from '@/contexts/workspace-context'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { assetGroups, assets, investmentStrategies } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { groupContributionAllocations, summarizeAdvisorWarnings } from '@/lib/investment-advisor-utils'
import type { AdvisorQuestionBank, AdvisorScoringMode, Asset, ContributionAllocation, ContributionPlan, ContributionPreview, InstrumentMatchCandidate, InvestmentStrategy, MarketSymbolMatch, PlanAllocationPrice, PlanPriceRefresh, StrategyClass, StrategyInstrument } from '@/types'

const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring'
const allocationColors = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']
const advisorCardHeaderClass = 'pt-6 pb-4'
type CreateInstrumentPayload = Parameters<typeof investmentStrategies.createInstrument>[1]
type QuestionnairePrompt =
  | { mode: 'create'; instrumentName: string; bank: AdvisorQuestionBank; payload: CreateInstrumentPayload }
  | { mode: 'edit'; instrument: StrategyInstrument; bank: AdvisorQuestionBank }
const standardClassLabels: Record<string, { english: string; key: string }> = {
  national_equities: { english: 'National Equities', key: 'defaultNationalEquities' },
  international_equities: { english: 'International Equities', key: 'defaultInternationalEquities' },
  national_real_estate: { english: 'National Real Estate Funds', key: 'defaultNationalRealEstate' },
  international_real_estate: { english: 'International Real Estate Funds', key: 'defaultInternationalRealEstate' },
  cryptoassets: { english: 'Cryptoassets', key: 'defaultCryptoassets' },
  national_fixed_income: { english: 'National Fixed Income', key: 'defaultNationalFixedIncome' },
  international_fixed_income: { english: 'International Fixed Income', key: 'defaultInternationalFixedIncome' },
}

function strategyClassLabel(strategyClass: Pick<StrategyClass, 'template_key' | 'name'>, t: TFunction): string {
  const standard = strategyClass.template_key ? standardClassLabels[strategyClass.template_key] : undefined
  return standard && strategyClass.name === standard.english
    ? t(`investmentAdvisor.${standard.key}`)
    : strategyClass.name
}

function snapshotClassLabel(name: string, t: TFunction): string {
  const standard = Object.values(standardClassLabels).find((item) => item.english === name)
  return standard ? t(`investmentAdvisor.${standard.key}`) : name
}

function secondaryCurrencyMoney(
  value: number,
  displayCurrency: string | null | undefined,
  baseCurrency: string,
  fxRates: Record<string, number>,
  locale: string,
  mask: (value: string) => string,
): string | null {
  if (!displayCurrency || displayCurrency === baseCurrency) return null
  const rate = Number(fxRates[displayCurrency])
  if (!Number.isFinite(rate) || rate <= 0) return null
  return mask(formatCurrency(value / rate, displayCurrency, locale))
}

function questionBankLabel(name: string, t: TFunction): string {
  if (name === 'Equities') return t('investmentAdvisor.defaultEquitiesBank')
  if (name === 'Real Estate Securities') return t('investmentAdvisor.defaultRealEstateBank')
  return name
}

function localizedAdvisorError(message: string, t: TFunction): string {
  const mappings: Array<[string, string]> = [
    [': set a percentage target for every instrument', 'percentageTargetsRequiredError'],
    [': instrument percentage targets must total exactly 100', 'percentageTargetsTotalError'],
  ]
  for (const [suffix, key] of mappings) {
    if (message.endsWith(suffix)) {
      return `${snapshotClassLabel(message.slice(0, -suffix.length), t)}: ${t(`investmentAdvisor.${key}`)}`
    }
  }
  return message
}

function apiError(error: unknown, fallback: string, t?: TFunction): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'errors' in detail) {
    const errors = (detail as { errors?: unknown }).errors
    if (Array.isArray(errors)) return errors.map((item) => (
      t && typeof item === 'string' ? localizedAdvisorError(item, t) : String(item)
    )).join(' · ')
  }
  return fallback
}

export default function InvestmentAdvisorPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const { canWrite, current: workspace } = useWorkspace()
  const { mask } = usePrivacyMode()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newCurrency, setNewCurrency] = useState(workspace?.default_currency ?? 'USD')
  const [newCountry, setNewCountry] = useState(workspace?.tax_jurisdiction?.slice(0, 2).toUpperCase() ?? 'US')

  const strategiesQuery = useQuery({ queryKey: ['investment-strategies'], queryFn: () => investmentStrategies.list() })
  const walletsQuery = useQuery({ queryKey: ['asset-groups'], queryFn: assetGroups.list })
  const assetsQuery = useQuery({ queryKey: ['assets'], queryFn: () => assets.list(false) })

  const activeStrategyId = strategiesQuery.data?.some((item) => item.id === selectedId)
    ? selectedId
    : strategiesQuery.data?.[0]?.id || ''
  const strategy = strategiesQuery.data?.find((item) => item.id === activeStrategyId)

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['investment-strategies'] })
    if (activeStrategyId) await queryClient.invalidateQueries({ queryKey: ['investment-plans', activeStrategyId] })
  }

  const createMutation = useMutation({
    mutationFn: () => investmentStrategies.create({ name: newName, currency: newCurrency, home_country: newCountry, wallet_ids: [] }),
    onSuccess: async (created) => {
      await refresh()
      setSelectedId(created.id)
      setCreating(false)
      setNewName('')
      toast.success(t('investmentAdvisor.strategyCreated'))
    },
    onError: (error) => toast.error(apiError(error, t('common.error'), t)),
  })

  if (strategiesQuery.isLoading) {
    return <div className="flex min-h-[40vh] items-center justify-center text-muted-foreground">{t('common.loading')}</div>
  }
  if (strategiesQuery.isError) {
    return <Card><CardContent className="py-12 text-center text-destructive">{t('common.error')}</CardContent></Card>
  }

  return (
    <div className="mx-auto max-w-6xl pb-10">
      <PageHeader
        section={t('assets.title')}
        title={t('investmentAdvisor.title')}
        action={<Button variant="outline" onClick={() => navigate('/assets')}><ArrowLeft />{t('investmentAdvisor.backToAssets')}</Button>}
      />

      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        {strategiesQuery.data?.length ? (
          <div className="w-full space-y-1.5 sm:max-w-xs">
            <Label htmlFor="advisor-strategy">{t('investmentAdvisor.strategy')}</Label>
            <select id="advisor-strategy" className={selectClass} value={activeStrategyId} onChange={(event) => setSelectedId(event.target.value)}>
              {strategiesQuery.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </div>
        ) : <p className="text-sm text-muted-foreground">{t('investmentAdvisor.noStrategies')}</p>}
        {canWrite && <Button variant="outline" onClick={() => setCreating((value) => !value)}><Plus />{t('investmentAdvisor.newStrategy')}</Button>}
      </div>

      {creating && (
        <Card className="mb-6">
          <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.newStrategy')}</CardTitle></CardHeader>
          <CardContent className="grid gap-4 pb-6 sm:grid-cols-4">
            <Field label={t('common.name')}><Input value={newName} onChange={(event) => setNewName(event.target.value)} /></Field>
            <Field label={t('investmentAdvisor.currency')}><Input maxLength={3} value={newCurrency} onChange={(event) => setNewCurrency(event.target.value.toUpperCase())} /></Field>
            <Field label={t('investmentAdvisor.homeCountry')}><Input maxLength={2} value={newCountry} onChange={(event) => setNewCountry(event.target.value.toUpperCase())} /></Field>
            <div className="flex items-end gap-2"><Button disabled={!newName.trim() || createMutation.isPending} onClick={() => createMutation.mutate()}>{t('common.create')}</Button><Button variant="ghost" onClick={() => setCreating(false)}>{t('common.cancel')}</Button></div>
          </CardContent>
        </Card>
      )}

      {!strategy && !creating ? (
        <Card><CardContent className="py-12 text-center"><Compass className="mx-auto mb-3 size-10 text-muted-foreground" /><p className="font-medium">{t('investmentAdvisor.emptyTitle')}</p><p className="mt-1 text-sm text-muted-foreground">{t('investmentAdvisor.emptyDescription')}</p></CardContent></Card>
      ) : strategy ? (
        <Tabs
          key={strategy.id}
          className="gap-0"
          defaultValue={strategy.instruments.length > 0 && Math.abs(strategy.classes.filter((item) => !item.is_archived).reduce((sum, item) => sum + item.target_percentage, 0) - 100) < 0.0001 ? 'contribution' : 'setup'}
        >
          <TabsList className="w-full justify-start overflow-x-auto sm:w-fit">
            <TabsTrigger value="contribution"><Sparkles />{t('investmentAdvisor.contribution')}</TabsTrigger>
            <TabsTrigger value="setup"><Settings2 />{t('investmentAdvisor.setup')}</TabsTrigger>
            <TabsTrigger value="history"><History />{t('investmentAdvisor.history')}</TabsTrigger>
          </TabsList>
          <TabsContent value="contribution" className="pt-6"><ContributionView key={strategy.id} strategy={strategy} canWrite={canWrite} locale={locale} mask={mask} refresh={refresh} /></TabsContent>
          <TabsContent value="setup" className="pt-6"><SetupView key={strategy.id} strategy={strategy} canWrite={canWrite} wallets={walletsQuery.data ?? []} holdings={assetsQuery.data ?? []} refresh={refresh} /></TabsContent>
          <TabsContent value="history" className="pt-6"><HistoryView key={strategy.id} strategy={strategy} canWrite={canWrite} locale={locale} mask={mask} /></TabsContent>
        </Tabs>
      ) : null}

      <p className="mt-8 rounded-lg border border-border bg-muted/40 px-4 py-3 text-xs text-muted-foreground">{t('investmentAdvisor.disclaimer')}</p>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="space-y-1.5"><Label>{label}</Label>{children}</div>
}

function QuestionnaireScoreSummary({ positive, total }: { positive: number; total: number }) {
  const { t } = useTranslation()
  const negative = total - positive
  const finalScore = positive - negative
  return <div className="grid grid-cols-3 gap-2" role="status" aria-live="polite" aria-label={t('investmentAdvisor.questionnaireScoreSummary')}>
    <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-center">
      <p className="text-[11px] leading-tight text-muted-foreground">{t('investmentAdvisor.positivePoints')}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">{positive}</p>
    </div>
    <div className="rounded-lg border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-center">
      <p className="text-[11px] leading-tight text-muted-foreground">{t('investmentAdvisor.negativePoints')}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-rose-600 dark:text-rose-400">{negative}</p>
    </div>
    <div className="rounded-lg border border-primary/20 bg-primary/10 px-3 py-2 text-center">
      <p className="text-[11px] leading-tight text-muted-foreground">{t('investmentAdvisor.finalScore')}</p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${finalScore > 0 ? 'text-primary' : 'text-amber-600 dark:text-amber-400'}`}>{finalScore}</p>
    </div>
  </div>
}

function QuestionnaireDialog({ prompt, canWrite, pending, onClose, onSave }: { prompt: QuestionnairePrompt; canWrite: boolean; pending: boolean; onClose: () => void; onSave: (yesQuestionIds: string[]) => void }) {
  const { t } = useTranslation()
  const initialAnswers = prompt.mode === 'edit' ? prompt.instrument.yes_question_ids : []
  const instrumentName = prompt.mode === 'edit' ? prompt.instrument.ticker || prompt.instrument.name : prompt.instrumentName
  const [selected, setSelected] = useState(() => new Set(initialAnswers))
  const toggle = (questionId: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(questionId)) next.delete(questionId)
      else next.add(questionId)
      return next
    })
  }
  const strength = 2 * selected.size - prompt.bank.questions.length

  return <Dialog open onOpenChange={(open) => { if (!open && !pending) onClose() }}>
    <DialogContent className="max-h-[min(48rem,calc(100vh-2rem))] grid-rows-[auto_auto_minmax(0,1fr)_auto] sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{t('investmentAdvisor.questionnaireDialogTitle', { instrument: instrumentName })}</DialogTitle>
        <DialogDescription>{t('investmentAdvisor.questionnaireDialogDescription')}</DialogDescription>
      </DialogHeader>
      <QuestionnaireScoreSummary positive={selected.size} total={prompt.bank.questions.length} />
      <div className="space-y-3 overflow-y-auto pr-1">
        {prompt.bank.questions.map((question) => {
          const answeredYes = selected.has(question.id)
          return <button
            key={question.id}
            type="button"
            aria-pressed={answeredYes}
            disabled={!canWrite || pending}
            className={`flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-colors ${answeredYes ? 'border-primary/40 bg-primary/10' : 'border-border hover:bg-muted/40'}`}
            onClick={() => toggle(question.id)}
          >
            <span className={`mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border ${answeredYes ? 'border-primary bg-primary text-primary-foreground' : 'border-input bg-background'}`}>
              {answeredYes && <Check className="size-4" />}
            </span>
            <span className="min-w-0">
              <span className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">{question.label}</span>
              <span className="mt-1 block text-sm font-medium leading-relaxed">{question.text}</span>
            </span>
          </button>
        })}
      </div>
      <DialogFooter className="items-center border-t border-border pt-4 sm:justify-between">
        <p className="text-xs text-muted-foreground">{t('investmentAdvisor.questionnaireProgress', { answered: selected.size, total: prompt.bank.questions.length, score: strength })}</p>
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button type="button" variant="outline" disabled={pending} onClick={onClose}>{canWrite ? t('common.cancel') : t('common.close')}</Button>
          {canWrite && <Button type="button" disabled={pending} onClick={() => onSave([...selected])}><Save />{pending ? t('common.saving') : t('investmentAdvisor.saveAnswers')}</Button>}
        </div>
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

function InlineQuestionnaireAnswers({ strategyId, instrument, bank, canWrite, refresh, onPositiveChange }: { strategyId: string; instrument: StrategyInstrument; bank: AdvisorQuestionBank; canWrite: boolean; refresh: () => Promise<void>; onPositiveChange?: (positive: number) => void }) {
  const { t } = useTranslation()
  const initialAnswers = new Set(instrument.yes_question_ids)
  const [selected, setSelected] = useState(initialAnswers)
  const selectedRef = useRef(initialAnswers)
  const confirmedRef = useRef(initialAnswers)
  const revisionRef = useRef(0)
  const answerMutation = useMutation({
    mutationFn: ({ ids }: { ids: string[]; revision: number }) => investmentStrategies.updateInstrument(strategyId, instrument.id, { yes_question_ids: ids }),
    scope: { id: `advisor-answers-${instrument.id}` },
    onSuccess: async (_, variables) => {
      confirmedRef.current = new Set(variables.ids)
      if (variables.revision === revisionRef.current) await refresh()
    },
    onError: async (error, variables) => {
      if (variables.revision !== revisionRef.current) return
      const confirmed = new Set(confirmedRef.current)
      selectedRef.current = confirmed
      setSelected(confirmed)
      onPositiveChange?.(confirmed.size)
      toast.error(apiError(error, t('common.error'), t))
      await refresh()
    },
  })
  const toggle = (questionId: string) => {
    const next = new Set(selectedRef.current)
    if (next.has(questionId)) next.delete(questionId)
    else next.add(questionId)
    selectedRef.current = next
    setSelected(next)
    onPositiveChange?.(next.size)
    answerMutation.mutate({ ids: [...next], revision: ++revisionRef.current })
  }

  return <div className="space-y-3">
    <QuestionnaireScoreSummary positive={selected.size} total={bank.questions.length} />
    <div className="space-y-2">
    {bank.questions.map((question) => <label key={question.id} className="flex cursor-pointer items-start gap-2 text-sm has-disabled:cursor-not-allowed has-disabled:opacity-70">
      <input
        type="checkbox"
        className="mt-1"
        disabled={!canWrite}
        checked={selected.has(question.id)}
        onChange={() => toggle(question.id)}
      />
      <span><strong>{question.label}</strong> — {question.text}</span>
    </label>)}
    </div>
    {answerMutation.isPending && <p className="pl-6 text-xs text-muted-foreground" role="status">{t('common.saving')}</p>}
  </div>
}

function AllocationTargetEditor({
  activeClasses,
  targets,
  canWrite,
  pending,
  onTargetChange,
  onRename,
  onArchive,
  onSave,
}: {
  activeClasses: StrategyClass[]
  targets: Record<string, string>
  canWrite: boolean
  pending: boolean
  onTargetChange: (classId: string, value: string) => void
  onRename: (strategyClass: StrategyClass, name: string) => void
  onArchive: (classId: string) => void
  onSave: () => void
}) {
  const { t } = useTranslation()
  const values = activeClasses.map((item) => Math.max(0, Math.min(100, Number(targets[item.id]) || 0)))
  const total = values.reduce((sum, value) => sum + value, 0)
  const ready = Math.abs(total - 100) < 0.0001
  const scale = total > 100 ? 100 / total : 1
  const distribution = values.reduce<{ segments: string[]; cursor: number }>((result, value, index) => {
    if (value <= 0) return result
    const end = result.cursor + value * scale
    return {
      cursor: end,
      segments: [...result.segments, `${allocationColors[index % allocationColors.length]} ${result.cursor}% ${end}%`],
    }
  }, { segments: [], cursor: 0 })
  const segments = distribution.cursor < 100
    ? [...distribution.segments, `var(--muted) ${distribution.cursor}% 100%`]
    : distribution.segments
  const donutBackground = `conic-gradient(${segments.length ? segments.join(', ') : 'var(--muted) 0% 100%'})`
  const formattedTotal = Number.isInteger(total) ? total.toFixed(0) : total.toFixed(2)
  const difference = Math.abs(100 - total)
  const formattedDifference = Number.isInteger(difference) ? difference.toFixed(0) : difference.toFixed(2)

  return (
    <Card className="overflow-hidden">
      <CardHeader className={`${advisorCardHeaderClass} border-b border-border/70 bg-muted/20 pb-5`}>
        <CardTitle>{t('investmentAdvisor.targets')}</CardTitle>
        <CardDescription>{t('investmentAdvisor.allocationGuide')}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 p-5 lg:grid-cols-[15rem_minmax(0,1fr)] lg:p-6">
        <div className="flex flex-col items-center rounded-xl border border-border bg-card p-5">
          <div
            className="relative flex size-40 items-center justify-center rounded-full shadow-sm"
            style={{ background: donutBackground }}
            role="img"
            aria-label={`${t('investmentAdvisor.total')}: ${formattedTotal}%`}
          >
            <div className="flex size-28 flex-col items-center justify-center rounded-full border border-border bg-card">
              <span className="text-2xl font-semibold tabular-nums">{formattedTotal}%</span>
              <span className="text-xs text-muted-foreground">{t('investmentAdvisor.total')}</span>
            </div>
          </div>
          <div className="mt-5 w-full space-y-2">
            {activeClasses.map((item, index) => (
              <div key={item.id} className="flex items-center justify-between gap-3 text-xs">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: allocationColors[index % allocationColors.length] }} />
                  <span className="truncate text-muted-foreground">{strategyClassLabel(item, t)}</span>
                </span>
                <span className="font-medium tabular-nums">{values[index].toLocaleString(undefined, { maximumFractionDigits: 2 })}%</span>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-3">
          {activeClasses.map((item, index) => {
            const value = values[index]
            const color = allocationColors[index % allocationColors.length]
            const displayName = strategyClassLabel(item, t)
            return (
              <div key={item.id} className="rounded-xl border border-border bg-background p-4 transition-colors hover:bg-muted/20">
                <div className="mb-3 flex items-center gap-2">
                  <span className="size-3 shrink-0 rounded-full" style={{ backgroundColor: color }} />
                  <Input
                    aria-label={`${displayName} ${t('common.name')}`}
                    className="h-8 min-w-0 flex-1 border-transparent bg-transparent px-1 font-medium shadow-none hover:border-input focus-visible:bg-background"
                    disabled={!canWrite}
                    defaultValue={displayName}
                    onBlur={(event) => onRename(item, event.target.value)}
                  />
                  <div className="relative w-24 shrink-0">
                    <Input
                      id={`target-${item.id}`}
                      aria-label={`${displayName} ${t('investmentAdvisor.targets')}`}
                      className="h-8 pr-7 text-right tabular-nums"
                      type="number"
                      min="0"
                      max="100"
                      step="0.01"
                      disabled={!canWrite}
                      value={targets[item.id] ?? '0'}
                      onChange={(event) => onTargetChange(item.id, event.target.value)}
                    />
                    <span className="pointer-events-none absolute right-2.5 top-1.5 text-sm text-muted-foreground">%</span>
                  </div>
                  {canWrite && (
                    <Button size="icon" variant="ghost" aria-label={t('investmentAdvisor.archiveClass')} onClick={() => onArchive(item.id)}>
                      <Archive />
                    </Button>
                  )}
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="0.5"
                  value={value}
                  disabled={!canWrite}
                  aria-label={`${displayName}: ${value}%`}
                  className="h-2 w-full cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-60"
                  style={{ accentColor: color }}
                  onChange={(event) => onTargetChange(item.id, event.target.value)}
                />
                <div className="mt-1 flex justify-between text-[11px] tabular-nums text-muted-foreground"><span>0%</span><span>100%</span></div>
              </div>
            )
          })}

          <div className={`flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between ${ready ? 'border-emerald-500/30 bg-emerald-500/10' : 'border-amber-500/30 bg-amber-500/10'}`}>
            <div className="flex items-center gap-3">
              <span className={`flex size-9 items-center justify-center rounded-full ${ready ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/15 text-amber-700 dark:text-amber-300'}`}>
                {ready ? <Check className="size-4" /> : <span className="text-sm font-semibold">{formattedTotal}</span>}
              </span>
              <div>
                <p className="text-sm font-medium">{ready ? t('investmentAdvisor.targetsReady') : total < 100 ? t('investmentAdvisor.targetsRemaining', { amount: formattedDifference }) : t('investmentAdvisor.targetsOver', { amount: formattedDifference })}</p>
                <p className="text-xs text-muted-foreground">{t('investmentAdvisor.total')}: {formattedTotal}% / 100%</p>
              </div>
            </div>
            {canWrite && <Button disabled={!ready || pending} onClick={onSave}><Save />{t('common.save')}</Button>}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function SetupView({ strategy, canWrite, wallets, holdings, refresh }: { strategy: InvestmentStrategy; canWrite: boolean; wallets: { id: string; name: string }[]; holdings: Asset[]; refresh: () => Promise<void> }) {
  const { t } = useTranslation()
  const [strategyName, setStrategyName] = useState(strategy.name)
  const [strategyCurrency, setStrategyCurrency] = useState(strategy.currency)
  const [strategyCountry, setStrategyCountry] = useState(strategy.home_country)
  const [deleteStrategyOpen, setDeleteStrategyOpen] = useState(false)
  const [deleteStrategyConfirmation, setDeleteStrategyConfirmation] = useState('')
  const [targets, setTargets] = useState<Record<string, string>>(() => Object.fromEntries(strategy.classes.filter((item) => !item.is_archived).map((item) => [item.id, String(item.target_percentage)])))
  const [classId, setClassId] = useState(strategy.classes.find((item) => !item.is_archived)?.id ?? '')
  const [source, setSource] = useState<'manual' | 'holding' | 'market'>('holding')
  const [holdingId, setHoldingId] = useState('')
  const [name, setName] = useState('')
  const [ticker, setTicker] = useState('')
  const [exchange, setExchange] = useState('')
  const [isin, setIsin] = useState('')
  const [currency, setCurrency] = useState(strategy.currency)
  const [price, setPrice] = useState('')
  const [strength, setStrength] = useState('0')
  const [instrumentTarget, setInstrumentTarget] = useState('0')
  const [marketQuery, setMarketQuery] = useState('')
  const [marketResults, setMarketResults] = useState<MarketSymbolMatch[]>([])
  const [selectedMarketSymbol, setSelectedMarketSymbol] = useState('')
  const [questionnairePrompt, setQuestionnairePrompt] = useState<QuestionnairePrompt | null>(null)
  const [questionCategory, setQuestionCategory] = useState('')
  const [questionText, setQuestionText] = useState('')
  const [questionBankId, setQuestionBankId] = useState(strategy.question_banks[0]?.id ?? '')
  const [newBankName, setNewBankName] = useState('')
  const [customClassName, setCustomClassName] = useState('')
  const [customScoring, setCustomScoring] = useState<AdvisorScoringMode>('manual')
  const [customPurchase, setCustomPurchase] = useState<'whole_units' | 'fractional_units' | 'cash_amount' | 'fixed_income_hybrid'>('fractional_units')
  const [portfolioClassId, setPortfolioClassId] = useState(
    strategy.instruments[0]?.class_id ?? strategy.classes.find((item) => !item.is_archived)?.id ?? '',
  )
  const [percentageTargets, setPercentageTargets] = useState<Record<string, string>>(() => (
    Object.fromEntries(strategy.instruments.map((item) => [item.id, String(item.target_percentage ?? 0)]))
  ))
  const [questionnairePositiveCounts, setQuestionnairePositiveCounts] = useState<Record<string, number>>(() => (
    Object.fromEntries(strategy.instruments.map((item) => [item.id, item.yes_question_ids.length]))
  ))

  const selectedHoldings = holdings.filter((asset) => asset.group_id && strategy.wallet_ids.includes(asset.group_id))
  const activeClasses = strategy.classes.filter((item) => !item.is_archived)
  const selectedClass = activeClasses.find((item) => item.id === classId)
  const manualPriceRequired = source === 'manual' && ['whole_units', 'fractional_units'].includes(selectedClass?.purchase_mode ?? '')
  const manualPriceSupported = source === 'manual' && (manualPriceRequired || selectedClass?.purchase_mode === 'fixed_income_hybrid')
  const portfolioClass = activeClasses.find((item) => item.id === portfolioClassId) ?? activeClasses[0]
  const portfolioInstruments = strategy.instruments.filter((item) => item.class_id === portfolioClass?.id)
  const percentageTotal = portfolioInstruments.reduce(
    (sum, item) => sum + (Number(percentageTargets[item.id] ?? item.target_percentage ?? 0) || 0),
    0,
  )
  const percentagesReady = Math.abs(percentageTotal - 100) < 0.0001 && portfolioInstruments.every((item) => {
    const value = Number(percentageTargets[item.id] ?? item.target_percentage ?? 0)
    return Number.isFinite(value) && value >= 0 && value <= 100
  })

  const clearInstrumentDraft = () => {
    setName('')
    setTicker('')
    setExchange('')
    setIsin('')
    setPrice('')
    setHoldingId('')
    setMarketQuery('')
    setMarketResults([])
    setSelectedMarketSymbol('')
    setStrength('0')
    setInstrumentTarget('0')
  }

  const mutation = useMutation({ mutationFn: async (fn: () => Promise<unknown>) => fn(), onSuccess: refresh, onError: (error) => toast.error(apiError(error, t('common.error'), t)) })
  const deleteStrategyMutation = useMutation({
    mutationFn: () => investmentStrategies.deletePermanently(strategy.id),
    onSuccess: async () => {
      setDeleteStrategyOpen(false)
      setDeleteStrategyConfirmation('')
      toast.success(t('investmentAdvisor.strategyDeleted'))
      await refresh()
    },
    onError: (error) => toast.error(apiError(error, t('investmentAdvisor.deleteStrategyError'), t)),
  })
  const marketSearchMutation = useMutation({ mutationFn: () => assets.marketSearch(marketQuery), onSuccess: setMarketResults, onError: (error) => toast.error(apiError(error, t('common.error'), t)) })
  const marketQuoteMutation = useMutation({ mutationFn: (symbol: string) => assets.marketQuote(symbol), onSuccess: (quote) => { setSelectedMarketSymbol(quote.symbol); setTicker(quote.symbol); setName(quote.name || quote.symbol); setExchange(quote.exchange || ''); setCurrency(quote.currency); setPrice(String(quote.price)); setMarketResults([]) }, onError: (error) => toast.error(apiError(error, t('common.error'), t)) })
  const addInstrumentMutation = useMutation({
    mutationFn: (payload: CreateInstrumentPayload) => investmentStrategies.createInstrument(strategy.id, payload),
    onSuccess: async () => { clearInstrumentDraft(); await refresh() },
    onError: (error) => toast.error(apiError(error, t('common.error'), t)),
  })
  const questionnaireMutation = useMutation({
    mutationFn: async ({ prompt, yesQuestionIds }: { prompt: QuestionnairePrompt; yesQuestionIds: string[] }) => {
      if (prompt.mode === 'create') {
        return investmentStrategies.createInstrument(strategy.id, { ...prompt.payload, yes_question_ids: yesQuestionIds })
      }
      return investmentStrategies.updateInstrument(strategy.id, prompt.instrument.id, { yes_question_ids: yesQuestionIds })
    },
    onSuccess: async (_, variables) => {
      if (variables.prompt.mode === 'create') clearInstrumentDraft()
      setQuestionnairePrompt(null)
      toast.success(t('investmentAdvisor.questionnaireAnswersSaved'))
      await refresh()
    },
    onError: (error) => toast.error(apiError(error, t('common.error'), t)),
  })

  const selectSource = (nextSource: 'manual' | 'holding' | 'market') => {
    setSource(nextSource)
    clearInstrumentDraft()
    setCurrency(strategy.currency)
  }

  const addInstrument = () => {
    const holding = holdings.find((item) => item.id === holdingId)
    const useHolding = source === 'holding' && holding
    const payload: CreateInstrumentPayload = {
      class_id: classId,
      name: useHolding ? holding.name : name,
      ticker: useHolding ? holding.ticker : ticker || null,
      exchange: useHolding ? holding.ticker_exchange : exchange || null,
      currency: useHolding ? holding.currency : currency,
      isin: useHolding ? holding.isin : isin || null,
      current_price: useHolding ? holding.last_price : (Number(price) || null),
      price_source: source === 'market' ? 'market' : 'manual',
      manual_strength: selectedClass?.scoring_mode === 'manual' ? Number(strength) : null,
      target_percentage: selectedClass?.scoring_mode === 'percentage' ? Number(instrumentTarget) || 0 : null,
      asset_ids: useHolding ? [holding.id] : [],
    }
    const bank = selectedClass?.scoring_mode === 'questionnaire'
      ? strategy.question_banks.find((item) => item.id === selectedClass.question_bank_id)
      : undefined
    if (bank?.questions.length) {
      setQuestionnairePrompt({ mode: 'create', instrumentName: payload.ticker || payload.name, bank, payload })
      return
    }
    addInstrumentMutation.mutate(payload)
  }

  return <Tabs defaultValue="allocation" className="gap-6">
    <div className="-mx-1 overflow-x-auto px-1">
      <TabsList variant="line" className="min-w-max">
        <TabsTrigger value="allocation">{t('investmentAdvisor.targets')}</TabsTrigger>
        <TabsTrigger value="portfolio">{t('investmentAdvisor.instruments')}</TabsTrigger>
        <TabsTrigger value="questions">{t('investmentAdvisor.questionBanks')}</TabsTrigger>
        <TabsTrigger value="settings">{t('investmentAdvisor.strategySettings')}</TabsTrigger>
      </TabsList>
    </div>

    <TabsContent value="allocation" className="mx-auto w-full max-w-5xl space-y-6">
      <AllocationTargetEditor
        activeClasses={activeClasses}
        targets={targets}
        canWrite={canWrite}
        pending={mutation.isPending}
        onTargetChange={(id, value) => setTargets((current) => ({ ...current, [id]: value }))}
        onRename={(item, value) => { if (value.trim() && value !== strategyClassLabel(item, t)) mutation.mutate(() => investmentStrategies.updateClass(strategy.id, item.id, { name: value.trim() })) }}
        onArchive={(id) => mutation.mutate(() => investmentStrategies.updateClass(strategy.id, id, { is_archived: true }))}
        onSave={() => mutation.mutate(() => investmentStrategies.updateTargets(
          strategy.id,
          Object.entries(targets).map(([id, value]) => ({ class_id: id, target_percentage: Number(value) || 0 })),
        ))}
      />

      {canWrite && <Card>
        <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.customClass')}</CardTitle></CardHeader>
        <CardContent className="grid gap-4 pb-6 md:grid-cols-2">
          <Field label={t('investmentAdvisor.className')}><Input value={customClassName} onChange={(event) => setCustomClassName(event.target.value)} /></Field>
          <Field label={t('investmentAdvisor.allocationMethod')}><select className={selectClass} value={customScoring} onChange={(event) => setCustomScoring(event.target.value as AdvisorScoringMode)}><option value="manual">{t('investmentAdvisor.manualScore')}</option><option value="questionnaire">{t('investmentAdvisor.questionnaire')}</option><option value="percentage">{t('investmentAdvisor.percentageAllocation')}</option></select></Field>
          <Field label={t('investmentAdvisor.purchaseMode')}><select className={selectClass} value={customPurchase} onChange={(event) => setCustomPurchase(event.target.value as typeof customPurchase)}><option value="whole_units">{t('investmentAdvisor.wholeUnits')}</option><option value="fractional_units">{t('investmentAdvisor.fractionalUnits')}</option><option value="cash_amount">{t('investmentAdvisor.cashAmount')}</option><option value="fixed_income_hybrid">{t('investmentAdvisor.fixedIncomeHybrid')}</option></select></Field>
          <div className="flex items-end"><Button disabled={!customClassName.trim()} onClick={() => mutation.mutate(() => investmentStrategies.createClass(strategy.id, { name: customClassName, target_percentage: 0, scoring_mode: customScoring, purchase_mode: customPurchase, quantity_decimals: customPurchase === 'fractional_units' ? 4 : customPurchase === 'fixed_income_hybrid' ? 2 : 0, question_bank_id: customScoring === 'questionnaire' ? strategy.question_banks[0]?.id ?? null : null, display_currency: null, position: strategy.classes.length }))}><Plus />{t('common.create')}</Button></div>
        </CardContent>
      </Card>}
    </TabsContent>

    <TabsContent value="portfolio" className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(20rem,0.7fr)]">
      <Card>
        <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.instruments')}</CardTitle><CardDescription>{t('investmentAdvisor.instrumentsDescription')}</CardDescription></CardHeader>
        <CardContent className="space-y-3 pb-6">
          <div className="grid gap-4 rounded-xl border border-border bg-muted/20 p-4 md:grid-cols-3">
            <Field label={t('investmentAdvisor.investmentCategory')}>
              <select className={selectClass} value={portfolioClass?.id ?? ''} onChange={(event) => { setPortfolioClassId(event.target.value); setClassId(event.target.value) }}>
                {activeClasses.map((item) => <option key={item.id} value={item.id}>{strategyClassLabel(item, t)}</option>)}
              </select>
            </Field>
            {portfolioClass && <Field label={t('investmentAdvisor.allocationMethod')}>
              <select
                className={selectClass}
                value={portfolioClass.scoring_mode}
                disabled={!canWrite || mutation.isPending}
                onChange={(event) => {
                  const scoring_mode = event.target.value as AdvisorScoringMode
                  mutation.mutate(() => investmentStrategies.updateClass(strategy.id, portfolioClass.id, {
                    scoring_mode,
                    ...(scoring_mode === 'questionnaire' && !portfolioClass.question_bank_id
                      ? { question_bank_id: strategy.question_banks[0]?.id ?? null }
                      : {}),
                  }))
                }}
              >
                <option value="questionnaire">{t('investmentAdvisor.questionnaire')}</option>
                <option value="manual">{t('investmentAdvisor.manualScore')}</option>
                <option value="percentage">{t('investmentAdvisor.percentageAllocation')}</option>
              </select>
            </Field>}
            {portfolioClass && <Field label={t('investmentAdvisor.investmentCurrency')}>
              <CurrencySelect
                value={portfolioClass.display_currency ?? strategy.currency}
                disabled={!canWrite || mutation.isPending}
                onChange={(currency) => mutation.mutate(() => investmentStrategies.updateClass(strategy.id, portfolioClass.id, {
                  display_currency: currency === strategy.currency ? null : currency,
                }))}
              />
            </Field>}
            {portfolioClass?.scoring_mode === 'questionnaire' && <Field label={t('investmentAdvisor.questionBanks')}>
              <select
                className={selectClass}
                value={portfolioClass.question_bank_id ?? ''}
                disabled={!canWrite || mutation.isPending}
                onChange={(event) => mutation.mutate(() => investmentStrategies.updateClass(strategy.id, portfolioClass.id, { question_bank_id: event.target.value }))}
              >
                {strategy.question_banks.map((bank) => <option key={bank.id} value={bank.id}>{questionBankLabel(bank.name, t)}</option>)}
              </select>
            </Field>}
            <p className="text-sm text-muted-foreground md:col-span-full">{t('investmentAdvisor.investmentCurrencyDescription', { currency: strategy.currency })}</p>
            {portfolioClass?.scoring_mode === 'percentage' && <p className="self-end text-sm text-muted-foreground md:col-span-full">{t('investmentAdvisor.percentageAllocationDescription')}</p>}
          </div>

          {portfolioInstruments.map((item) => {
            const strategyClass = strategy.classes.find((row) => row.id === item.class_id)
            const bank = strategy.question_banks.find((row) => row.id === strategyClass?.question_bank_id)
            const positiveAnswers = questionnairePositiveCounts[item.id] ?? item.yes_question_ids.length
            const negativeAnswers = Math.max((bank?.questions.length ?? 0) - positiveAnswers, 0)
            const finalScore = positiveAnswers - negativeAnswers
            return <div key={item.id} className="rounded-lg border border-border p-4">
              <div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="font-medium">{item.ticker || item.name}</p><p className="mt-0.5 text-xs text-muted-foreground">{strategyClass ? strategyClassLabel(strategyClass, t) : ''} · {item.linked_asset_ids.length ? t('investmentAdvisor.linkedHoldings', { count: item.linked_asset_ids.length }) : t('investmentAdvisor.zeroPosition')}</p>{strategyClass?.scoring_mode === 'questionnaire' && bank?.questions.length ? <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs"><span className="text-emerald-600 dark:text-emerald-400">{t('investmentAdvisor.positivePoints')}: <strong className="tabular-nums">{positiveAnswers}</strong></span><span className="text-rose-600 dark:text-rose-400">{t('investmentAdvisor.negativePoints')}: <strong className="tabular-nums">{negativeAnswers}</strong></span><span className="rounded-md bg-primary/10 px-2 py-1 font-medium text-primary">{t('investmentAdvisor.finalScore')}: <strong className="tabular-nums">{finalScore}</strong></span></div> : null}</div><div className="flex shrink-0 items-center gap-2"><Badge variant={item.allocatable ? 'default' : 'secondary'}>{strategyClass?.scoring_mode === 'percentage' && Number(item.target_percentage) === 0 ? t('investmentAdvisor.noNewAllocation') : item.allocatable ? t('investmentAdvisor.eligible') : t('investmentAdvisor.needsReview')}</Badge>{canWrite && <Button size="icon" variant="ghost" aria-label={t('common.delete')} onClick={() => mutation.mutate(() => investmentStrategies.deleteInstrument(strategy.id, item.id))}><Trash2 /></Button>}</div></div>
              {strategyClass?.scoring_mode === 'percentage' && <div className="mt-4 grid grid-cols-[minmax(0,1fr)_6rem] items-end gap-3 rounded-lg bg-muted/30 p-3">
                <Field label={t('investmentAdvisor.instrumentTarget')}>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="0.5"
                    disabled={!canWrite}
                    aria-label={`${item.ticker || item.name}: ${t('investmentAdvisor.instrumentTarget')}`}
                    className="h-2 w-full cursor-pointer accent-primary disabled:cursor-not-allowed disabled:opacity-60"
                    value={Number(percentageTargets[item.id] ?? item.target_percentage ?? 0)}
                    onChange={(event) => setPercentageTargets((current) => ({ ...current, [item.id]: event.target.value }))}
                  />
                </Field>
                <div className="relative">
                  <Input
                    aria-label={`${item.ticker || item.name}: ${t('investmentAdvisor.instrumentTarget')}`}
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    disabled={!canWrite}
                    className="pr-7 text-right tabular-nums"
                    value={percentageTargets[item.id] ?? String(item.target_percentage ?? 0)}
                    onChange={(event) => setPercentageTargets((current) => ({ ...current, [item.id]: event.target.value }))}
                  />
                  <span className="pointer-events-none absolute right-2.5 top-2 text-sm text-muted-foreground">%</span>
                </div>
              </div>}
              {strategyClass?.scoring_mode === 'questionnaire' && !bank?.questions.length ? <p className="mt-4 rounded-lg bg-muted/30 p-3 text-xs text-muted-foreground">{t('investmentAdvisor.noQuestionnaireQuestions')}</p> : null}
              <details className="mt-3 border-t border-border pt-3">
                <summary className="cursor-pointer text-sm font-medium text-primary marker:text-muted-foreground">{t('investmentAdvisor.scoringAndMatching')}</summary>
                <div className="mt-3 space-y-3">
                  {strategyClass?.scoring_mode === 'manual' && <div className="flex items-center gap-2"><Label className="shrink-0">{t('investmentAdvisor.strength')}</Label><Input className="max-w-24" type="number" disabled={!canWrite} defaultValue={item.manual_strength ?? 0} onBlur={(event) => { if (canWrite) mutation.mutate(() => investmentStrategies.updateInstrument(strategy.id, item.id, { manual_strength: Number(event.target.value) })) }} /></div>}
                  {strategyClass?.scoring_mode === 'questionnaire' && bank?.questions.length ? <InlineQuestionnaireAnswers strategyId={strategy.id} instrument={item} bank={bank} canWrite={canWrite} refresh={refresh} onPositiveChange={(positive) => setQuestionnairePositiveCounts((current) => ({ ...current, [item.id]: positive }))} /> : null}
                  <MatchControls strategyId={strategy.id} instrument={item} canWrite={canWrite} refresh={refresh} />
                </div>
              </details>
            </div>
          })}
          {portfolioClass?.scoring_mode === 'percentage' && portfolioInstruments.length > 0 && <div className={`flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between ${percentagesReady ? 'border-emerald-500/30 bg-emerald-500/10' : 'border-amber-500/30 bg-amber-500/10'}`}>
            <div>
              <p className="text-sm font-medium">{percentagesReady ? t('investmentAdvisor.instrumentTargetsReady') : percentageTotal < 100 ? t('investmentAdvisor.instrumentTargetsRemaining', { amount: (100 - percentageTotal).toLocaleString(undefined, { maximumFractionDigits: 2 }) }) : t('investmentAdvisor.instrumentTargetsOver', { amount: (percentageTotal - 100).toLocaleString(undefined, { maximumFractionDigits: 2 }) })}</p>
              <p className="text-xs text-muted-foreground">{t('investmentAdvisor.total')}: {percentageTotal.toLocaleString(undefined, { maximumFractionDigits: 2 })}% / 100%</p>
            </div>
            {canWrite && <Button
              disabled={!percentagesReady || mutation.isPending}
              onClick={() => mutation.mutate(() => Promise.all(
                portfolioInstruments.map((item) => investmentStrategies.updateInstrument(
                  strategy.id,
                  item.id,
                  { target_percentage: Number(percentageTargets[item.id] ?? item.target_percentage ?? 0) },
                )),
              ))}
            ><Save />{t('investmentAdvisor.savePercentages')}</Button>}
          </div>}
          {!portfolioInstruments.length && <p className="py-8 text-center text-sm text-muted-foreground">{t('investmentAdvisor.noInstrumentsInCategory')}</p>}
        </CardContent>
      </Card>

      <div className="space-y-6">
        <Card>
          <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.wallets')}</CardTitle><CardDescription>{t('investmentAdvisor.walletsDescription')}</CardDescription></CardHeader>
          <CardContent className="space-y-2 pb-6">{wallets.map((wallet) => <label key={wallet.id} className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!canWrite} checked={strategy.wallet_ids.includes(wallet.id)} onChange={(event) => { const wallet_ids = event.target.checked ? [...strategy.wallet_ids, wallet.id] : strategy.wallet_ids.filter((id) => id !== wallet.id); mutation.mutate(() => investmentStrategies.update(strategy.id, { wallet_ids })) }} />{wallet.name}</label>)}{!wallets.length && <p className="text-sm text-muted-foreground">{t('investmentAdvisor.noWallets')}</p>}</CardContent>
        </Card>

        {canWrite && <Card>
          <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.addInstrument')}</CardTitle></CardHeader>
          <CardContent className="space-y-4 pb-6">
            <Field label={t('investmentAdvisor.instrumentSource')}>
              <select className={selectClass} value={source} onChange={(event) => selectSource(event.target.value as 'manual' | 'holding' | 'market')}>
                <option value="holding">{t('investmentAdvisor.existingHoldingSource')}</option>
                <option value="market">{t('investmentAdvisor.listedAssetSource')}</option>
                <option value="manual">{t('investmentAdvisor.manualAssetSource')}</option>
              </select>
            </Field>
            <p className="rounded-lg bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground">{t(`investmentAdvisor.${source === 'holding' ? 'existingHoldingSourceDescription' : source === 'market' ? 'listedAssetSourceDescription' : 'manualAssetSourceDescription'}`)}</p>
            <Field label={t('investmentAdvisor.class')}><select className={selectClass} value={classId} onChange={(event) => { setClassId(event.target.value); setPortfolioClassId(event.target.value) }}>{strategy.classes.filter((item) => !item.is_archived).map((item) => <option key={item.id} value={item.id}>{strategyClassLabel(item, t)}</option>)}</select></Field>
            {source === 'holding' ? <Field label={t('investmentAdvisor.existingHolding')}><select className={selectClass} value={holdingId} onChange={(event) => setHoldingId(event.target.value)}><option value="">{t('investmentAdvisor.selectHolding')}</option>{selectedHoldings.map((asset) => <option key={asset.id} value={asset.id}>{asset.ticker || asset.name} · {asset.currency}</option>)}</select></Field> : source === 'market' ? <div className="space-y-3">
              <div className="space-y-2"><div className="flex gap-2"><Input aria-label={t('investmentAdvisor.marketSearch')} placeholder={t('investmentAdvisor.marketSearchPlaceholder')} value={marketQuery} onChange={(event) => setMarketQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && marketQuery.trim()) marketSearchMutation.mutate() }} /><Button type="button" variant="outline" disabled={!marketQuery.trim() || marketSearchMutation.isPending} onClick={() => marketSearchMutation.mutate()}><Search />{t('investmentAdvisor.search')}</Button></div>{marketResults.length > 0 && <div className="max-h-48 overflow-y-auto rounded-md border border-border p-1">{marketResults.map((result) => <button type="button" key={result.symbol} className="flex w-full items-center justify-between rounded px-2 py-2 text-left text-sm hover:bg-accent" onClick={() => marketQuoteMutation.mutate(result.symbol)}><span><strong>{result.symbol}</strong> · {result.name || result.symbol}</span><span className="text-xs text-muted-foreground">{result.exchange}</span></button>)}</div>}</div>
              {marketQuoteMutation.isPending && <p className="text-xs text-muted-foreground">{t('common.loading')}</p>}
              {selectedMarketSymbol && <div className="rounded-lg border border-primary/30 bg-primary/5 p-3" role="status"><div className="flex items-start gap-3"><span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"><Check className="size-4" /></span><div className="min-w-0"><p className="text-sm font-medium">{name}</p><p className="text-xs text-muted-foreground">{ticker}{exchange ? ` · ${exchange}` : ''} · {currency}</p><p className="mt-2 text-xs font-medium">{t('investmentAdvisor.automaticMarketPrice')}: {price} {currency}</p><p className="mt-0.5 text-xs text-muted-foreground">{t('investmentAdvisor.automaticMarketPriceDescription')}</p></div></div></div>}
              {!selectedMarketSymbol && !marketQuoteMutation.isPending && <p className="text-xs text-muted-foreground">{t('investmentAdvisor.selectMarketAsset')}</p>}
            </div> : <>
              <Field label={t('common.name')}><Input value={name} onChange={(event) => setName(event.target.value)} /></Field><div className="grid grid-cols-2 gap-3"><Field label={t('investmentAdvisor.ticker')}><Input value={ticker} onChange={(event) => setTicker(event.target.value.toUpperCase())} /></Field><Field label={t('investmentAdvisor.currency')}><Input maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} /></Field></div><div className="grid grid-cols-2 gap-3"><Field label={t('investmentAdvisor.exchange')}><Input value={exchange} onChange={(event) => setExchange(event.target.value)} /></Field><Field label="ISIN"><Input value={isin} onChange={(event) => setIsin(event.target.value.toUpperCase())} /></Field></div>{manualPriceSupported && <div><Field label={`${t('investmentAdvisor.unitPrice')}${manualPriceRequired ? '' : ` (${t('investmentAdvisor.optional')})`}`}><Input type="number" min="0" step="0.000001" value={price} onChange={(event) => setPrice(event.target.value)} /></Field><p className="mt-1.5 text-xs text-muted-foreground">{t(manualPriceRequired ? 'investmentAdvisor.manualUnitPriceDescription' : 'investmentAdvisor.manualUnitPriceOptionalDescription')}</p></div>}</>}
            {selectedClass?.scoring_mode === 'manual' && <Field label={t('investmentAdvisor.strength')}><Input type="number" value={strength} onChange={(event) => setStrength(event.target.value)} /></Field>}
            {selectedClass?.scoring_mode === 'percentage' && <Field label={t('investmentAdvisor.instrumentTarget')}><div className="relative"><Input type="number" min="0" max="100" step="0.01" className="pr-7" value={instrumentTarget} onChange={(event) => setInstrumentTarget(event.target.value)} /><span className="pointer-events-none absolute right-2.5 top-2 text-sm text-muted-foreground">%</span></div></Field>}
            <Button className="w-full" disabled={!classId || (source === 'holding' ? !holdingId : source === 'market' ? !selectedMarketSymbol : !name.trim() || !currency || (manualPriceRequired && !(Number(price) > 0))) || mutation.isPending || addInstrumentMutation.isPending} onClick={addInstrument}><Plus />{selectedClass?.scoring_mode === 'questionnaire' && strategy.question_banks.find((item) => item.id === selectedClass.question_bank_id)?.questions.length ? t('investmentAdvisor.addAndAnswerQuestions') : t('investmentAdvisor.addInstrument')}</Button>
          </CardContent>
        </Card>}
      </div>
    </TabsContent>

    <TabsContent value="questions" className="mx-auto w-full max-w-5xl">
      <Card>
        <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.questionBanks')}</CardTitle><CardDescription>{t('investmentAdvisor.questionBanksDescription')}</CardDescription></CardHeader>
        <CardContent className="space-y-4 pb-6">
          <select className={selectClass} value={questionBankId} onChange={(event) => setQuestionBankId(event.target.value)}>{strategy.question_banks.map((bank) => <option key={bank.id} value={bank.id}>{questionBankLabel(bank.name, t)} ({bank.questions.length})</option>)}</select>
          {strategy.question_banks.find((bank) => bank.id === questionBankId)?.questions.map((question) => (
            <div key={question.id} className="grid gap-3 rounded-lg border border-border p-4 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.6fr)_auto]">
              <Field label={t('investmentAdvisor.questionCategory')}><Input aria-label={t('investmentAdvisor.questionCategory')} disabled={!canWrite} defaultValue={question.label} onBlur={(event) => { const value = event.target.value.trim(); if (!value) { event.target.value = question.label; toast.error(t('investmentAdvisor.questionCategoryRequired')) } else if (value !== question.label) mutation.mutate(() => investmentStrategies.updateQuestion(strategy.id, question.id, { label: value })) }} /></Field>
              <Field label={t('investmentAdvisor.question')}><Input aria-label={t('investmentAdvisor.question')} disabled={!canWrite} defaultValue={question.text} onBlur={(event) => { const value = event.target.value.trim(); if (!value) event.target.value = question.text; else if (value !== question.text) mutation.mutate(() => investmentStrategies.updateQuestion(strategy.id, question.id, { text: value })) }} /></Field>
              {canWrite && <div className="flex items-end"><Button size="icon" variant="ghost" aria-label={t('common.delete')} onClick={() => mutation.mutate(() => investmentStrategies.deleteQuestion(strategy.id, question.id))}><Trash2 /></Button></div>}
            </div>
          ))}
          {canWrite && <>
            <div className="rounded-lg border border-dashed border-border bg-muted/20 p-4">
              <div className="grid gap-3 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.6fr)_auto]">
                <Field label={t('investmentAdvisor.questionCategory')}><Input placeholder={t('investmentAdvisor.questionCategoryPlaceholder')} value={questionCategory} onChange={(event) => setQuestionCategory(event.target.value)} /></Field>
                <Field label={t('investmentAdvisor.question')}><Input placeholder={t('investmentAdvisor.questionPlaceholder')} value={questionText} onChange={(event) => setQuestionText(event.target.value)} /></Field>
                <div className="flex items-end"><Button disabled={!questionCategory.trim() || !questionText.trim() || !questionBankId || mutation.isPending} onClick={() => mutation.mutate(async () => { await investmentStrategies.createQuestion(strategy.id, questionBankId, { label: questionCategory.trim(), text: questionText.trim() }); setQuestionCategory(''); setQuestionText('') })}><Plus />{t('investmentAdvisor.addQuestion')}</Button></div>
              </div>
            </div>
            <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row"><Input placeholder={t('investmentAdvisor.bankName')} value={newBankName} onChange={(event) => setNewBankName(event.target.value)} /><Button variant="outline" disabled={!newBankName.trim()} onClick={() => mutation.mutate(async () => { await investmentStrategies.createQuestionBank(strategy.id, { name: newBankName }); setNewBankName('') })}><Plus />{t('investmentAdvisor.addBank')}</Button></div>
          </>}
        </CardContent>
      </Card>
    </TabsContent>

    <TabsContent value="settings" className="mx-auto w-full max-w-3xl space-y-6">
      <Card>
        <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.strategySettings')}</CardTitle><CardDescription>{t('investmentAdvisor.strategySettingsDescription')}</CardDescription></CardHeader>
        <CardContent className="grid gap-4 pb-6 sm:grid-cols-3">
          <Field label={t('common.name')}><Input disabled={!canWrite} value={strategyName} onChange={(event) => setStrategyName(event.target.value)} /></Field>
          <Field label={t('investmentAdvisor.currency')}><Input disabled={!canWrite} maxLength={3} value={strategyCurrency} onChange={(event) => setStrategyCurrency(event.target.value.toUpperCase())} /></Field>
          <Field label={t('investmentAdvisor.homeCountry')}><Input disabled={!canWrite} maxLength={2} value={strategyCountry} onChange={(event) => setStrategyCountry(event.target.value.toUpperCase())} /></Field>
          {canWrite && <div className="flex flex-wrap gap-2 sm:col-span-3"><Button onClick={() => mutation.mutate(() => investmentStrategies.update(strategy.id, { name: strategyName, currency: strategyCurrency, home_country: strategyCountry }))}><Save />{t('common.save')}</Button><Button variant="outline" onClick={() => mutation.mutate(() => investmentStrategies.archive(strategy.id))}><Archive />{t('investmentAdvisor.archiveStrategy')}</Button></div>}
        </CardContent>
      </Card>
      {canWrite && <Card className="border-destructive/30">
        <CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.deleteStrategySection')}</CardTitle><CardDescription>{t('investmentAdvisor.deleteStrategySectionDescription')}</CardDescription></CardHeader>
        <CardContent className="pb-6"><Button variant="destructive" onClick={() => { setDeleteStrategyConfirmation(''); setDeleteStrategyOpen(true) }}><Trash2 />{t('investmentAdvisor.deleteStrategy')}</Button></CardContent>
      </Card>}
    </TabsContent>
    <Dialog open={deleteStrategyOpen} onOpenChange={(open) => { if (!deleteStrategyMutation.isPending) { setDeleteStrategyOpen(open); if (!open) setDeleteStrategyConfirmation('') } }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>{t('investmentAdvisor.deleteStrategyTitle')}</DialogTitle><DialogDescription>{t('investmentAdvisor.deleteStrategyDescription', { name: strategy.name })}</DialogDescription></DialogHeader>
        <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{t('investmentAdvisor.deleteStrategyWarning')}</p>
        <Field label={t('investmentAdvisor.deleteStrategyConfirmation', { name: strategy.name })}><Input autoComplete="off" value={deleteStrategyConfirmation} onChange={(event) => setDeleteStrategyConfirmation(event.target.value)} /></Field>
        <DialogFooter><Button variant="outline" disabled={deleteStrategyMutation.isPending} onClick={() => setDeleteStrategyOpen(false)}>{t('common.cancel')}</Button><Button variant="destructive" disabled={deleteStrategyMutation.isPending || deleteStrategyConfirmation !== strategy.name} onClick={() => deleteStrategyMutation.mutate()}><Trash2 />{deleteStrategyMutation.isPending ? t('common.deleting') : t('investmentAdvisor.deleteStrategy')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    {questionnairePrompt && <QuestionnaireDialog
      key={questionnairePrompt.mode === 'create' ? `create-${questionnairePrompt.payload.class_id}` : questionnairePrompt.instrument.id}
      prompt={questionnairePrompt}
      canWrite={canWrite}
      pending={questionnaireMutation.isPending}
      onClose={() => setQuestionnairePrompt(null)}
      onSave={(yesQuestionIds) => questionnaireMutation.mutate({ prompt: questionnairePrompt, yesQuestionIds })}
    />}
  </Tabs>
}

function MatchControls({ strategyId, instrument, canWrite, refresh }: { strategyId: string; instrument: StrategyInstrument; canWrite: boolean; refresh: () => Promise<void> }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<string[]>(instrument.linked_asset_ids)
  const matchesQuery = useQuery({ queryKey: ['investment-matches', strategyId, instrument.id], queryFn: () => investmentStrategies.matches(strategyId, instrument.id), enabled: open })
  const confirmMutation = useMutation({ mutationFn: () => investmentStrategies.confirmMatches(strategyId, instrument.id, selected), onSuccess: async () => { toast.success(t('investmentAdvisor.matchesConfirmed')); await refresh() }, onError: (error) => toast.error(apiError(error, t('common.error'), t)) })
  const toggle = (candidate: InstrumentMatchCandidate) => setSelected((ids) => ids.includes(candidate.asset_id) ? ids.filter((id) => id !== candidate.asset_id) : [...ids, candidate.asset_id])
  return <div className="mt-3 border-t border-border pt-3">
    <Button type="button" size="sm" variant="outline" onClick={() => setOpen((value) => !value)}><Link2 />{t('investmentAdvisor.findMatches')}</Button>
    {open && <div className="mt-2 space-y-2">{matchesQuery.isLoading ? <p className="text-xs text-muted-foreground">{t('common.loading')}</p> : matchesQuery.isError ? <p className="text-xs text-destructive">{t('investmentAdvisor.matchError')}</p> : matchesQuery.data?.length ? <>{matchesQuery.data.map((candidate) => <label key={candidate.asset_id} className="flex items-start gap-2 rounded-md bg-muted/50 p-2 text-sm"><input type="checkbox" className="mt-1" disabled={!canWrite} checked={selected.includes(candidate.asset_id)} onChange={() => toggle(candidate)} /><span><strong>{candidate.asset_name}</strong><span className="block text-xs text-muted-foreground">{candidate.match_kind === 'isin' ? t('investmentAdvisor.exactIsinMatch') : t('investmentAdvisor.tickerMatch')} · {candidate.current_quantity} {candidate.currency}</span></span></label>)}{canWrite && <Button size="sm" disabled={confirmMutation.isPending} onClick={() => confirmMutation.mutate()}>{t('investmentAdvisor.confirmMatches')}</Button>}</> : <p className="text-xs text-muted-foreground">{t('investmentAdvisor.noMatches')}</p>}</div>}
  </div>
}

function ContributionView({ strategy, canWrite, locale, mask, refresh }: { strategy: InvestmentStrategy; canWrite: boolean; locale: string; mask: (value: string) => string; refresh: () => Promise<void> }) {
  const { t } = useTranslation()
  const contributionAmountStorageKey = `securo.investmentAdvisor.lastContributionAmount.${strategy.id}`
  const [amount, setAmount] = useState(() => {
    try {
      const stored = localStorage.getItem(contributionAmountStorageKey)
      return stored && Number.isFinite(Number(stored)) && Number(stored) > 0 ? stored : '500'
    } catch {
      return '500'
    }
  })
  const [preview, setPreview] = useState<ContributionPreview | null>(null)
  const [excluded, setExcluded] = useState<string[]>([])
  const [calculationStage, setCalculationStage] = useState<'refreshing' | 'calculating' | null>(null)
  const calculateMutation = useMutation({
    mutationFn: async ({ ids, refreshPrices }: { ids: string[]; refreshPrices: boolean }) => {
      if (refreshPrices) {
        await investmentStrategies.refreshMarketData(strategy.id)
        setCalculationStage('calculating')
      }
      return investmentStrategies.preview(strategy.id, Number(amount), ids)
    },
    onSuccess: (result) => { setPreview(result); setExcluded(result.excluded_instrument_ids) },
    onError: (error) => toast.error(apiError(error, t('investmentAdvisor.calculateError'), t)),
    onSettled: () => setCalculationStage(null),
  })
  const saveMutation = useMutation({ mutationFn: () => investmentStrategies.savePlan(strategy.id, preview?.amount ?? Number(amount), preview?.excluded_instrument_ids ?? excluded), onSuccess: async () => { toast.success(t('investmentAdvisor.planSaved')); await refresh() }, onError: (error) => toast.error(apiError(error, t('common.error'), t)) })
  const money = (value: number) => mask(formatCurrency(value, strategy.currency, locale))
  const recalculate = (ids: string[], refreshPrices = false) => {
    try {
      if (Number.isFinite(Number(amount)) && Number(amount) > 0) localStorage.setItem(contributionAmountStorageKey, amount)
    } catch {
      // Storage can be disabled; calculation should still work for this session.
    }
    setCalculationStage(refreshPrices ? 'refreshing' : 'calculating')
    calculateMutation.mutate({ ids, refreshPrices })
  }
  const warningSummary = preview ? summarizeAdvisorWarnings(preview.warnings) : null
  const oldestPriceTimestamp = warningSummary?.priceTimestamps.length
    ? new Date(Math.min(...warningSummary.priceTimestamps.map((timestamp) => timestamp.getTime())))
    : null
  const allocationGroups = preview ? groupContributionAllocations(preview) : []
  return <div className="mx-auto max-w-5xl space-y-6">
    <Card><CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.newContribution')}</CardTitle><CardDescription>{t('investmentAdvisor.contributionDescription')}</CardDescription></CardHeader><CardContent className="flex flex-col gap-3 pb-6 sm:flex-row sm:items-end"><Field label={`${t('investmentAdvisor.amount')} (${strategy.currency})`}><Input type="number" min="0.01" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} /></Field><Button disabled={!Number(amount) || calculateMutation.isPending || !strategy.instruments.length} onClick={() => recalculate([], canWrite && !preview)}><Sparkles />{calculateMutation.isPending ? t('investmentAdvisor.calculating') : t('investmentAdvisor.calculate')}</Button></CardContent></Card>
    {calculateMutation.isPending && <div className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/10 p-4" role="status" aria-live="polite">
      <LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-primary" />
      <div><p className="font-medium">{calculationStage === 'refreshing' ? t('investmentAdvisor.refreshingMarketPrices') : t('investmentAdvisor.calculatingAllocation')}</p><p className="mt-1 text-sm text-muted-foreground">{calculationStage === 'refreshing' ? t('investmentAdvisor.refreshingMarketPricesDescription') : t('investmentAdvisor.calculatingAllocationDescription')}</p></div>
    </div>}
    {preview && <>
      <div className="grid gap-3 sm:grid-cols-4">{[[t('investmentAdvisor.amount'), preview.amount], [t('investmentAdvisor.portfolio'), preview.portfolio_total], [t('investmentAdvisor.suggested'), preview.amount - preview.residual], [t('investmentAdvisor.residual'), preview.residual]].map(([label, value]) => <Card key={String(label)}><CardContent className="py-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{money(Number(value))}</p></CardContent></Card>)}</div>
      <AllocationChart preview={preview} />
      {oldestPriceTimestamp && warningSummary && <div className="flex items-start gap-3 rounded-xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm text-sky-800 dark:text-sky-200"><span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-sky-500/20"><Check className="size-3.5" /></span><div><p className="font-medium">{t('investmentAdvisor.marketDataUsed')}</p><p className="mt-1 text-muted-foreground">{t('investmentAdvisor.marketDataUsedDescription', { count: warningSummary.priceTimestamps.length, date: new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(oldestPriceTimestamp) })}</p></div></div>}
      {warningSummary && warningSummary.warnings.length > 0 && <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">{warningSummary.warnings.join(' · ')}</div>}
      <Card><CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.suggestions')}</CardTitle><CardDescription>{t('investmentAdvisor.suggestionsGroupedDescription')}</CardDescription></CardHeader><CardContent className="space-y-4 pb-6">{allocationGroups.map((group) => {
        const classSnapshot = preview.class_snapshot.find((item) => item.id === group.id)
        const displayCurrency = classSnapshot?.display_currency
        const groupTotal = group.allocations.reduce((total, item) => total + Number(item.suggested_value), 0)
        const secondaryGroupTotal = secondaryCurrencyMoney(groupTotal, displayCurrency, preview.currency, preview.fx_rates, locale, mask)
        return <section key={group.id} className="overflow-hidden rounded-xl border border-border" aria-labelledby={`allocation-group-${group.id}`}>
          <div className="flex items-center justify-between gap-3 bg-muted/40 px-4 py-3">
            <h3 id={`allocation-group-${group.id}`} className="font-semibold">{snapshotClassLabel(group.name, t)}</h3>
            <div className="text-right tabular-nums"><p className="text-sm font-medium">{money(groupTotal)}</p>{secondaryGroupTotal && <p className="text-xs font-semibold text-primary">{secondaryGroupTotal}</p>}</div>
          </div>
          <div className="divide-y divide-border">{group.allocations.map((item) => {
            const secondaryValue = secondaryCurrencyMoney(item.suggested_value, displayCurrency, preview.currency, preview.fx_rates, locale, mask)
            return <div key={item.instrument_id ?? item.instrument_name} className="grid gap-3 p-4 sm:grid-cols-[minmax(0,1fr)_auto_auto_auto] sm:items-center"><div><p className="font-medium">{item.instrument_name}</p><p className="text-xs text-muted-foreground">{item.target_percentage != null ? `${t('investmentAdvisor.instrumentTarget')} ${Number(item.target_percentage).toLocaleString(locale, { maximumFractionDigits: 2 })}%` : `${t('investmentAdvisor.score')} ${item.strength ?? '—'}`}</p></div><div className="sm:text-right"><p className="text-xs text-muted-foreground">{t('investmentAdvisor.value')}</p><p className="font-medium">{money(item.suggested_value)}</p>{secondaryValue && <p className="text-xs font-semibold text-primary">{secondaryValue}</p>}</div><div className="sm:text-right"><p className="text-xs text-muted-foreground">{t('investmentAdvisor.quantity')}</p><p className="font-medium tabular-nums">{item.suggested_quantity?.toLocaleString(locale, { maximumFractionDigits: 8 }) ?? '—'}</p></div><Button variant="ghost" size="icon" aria-label={t('investmentAdvisor.exclude')} onClick={() => recalculate([...excluded, item.instrument_id!])}><X /></Button></div>
          })}</div>
        </section>
      })}</CardContent></Card>
      {canWrite && <div className="flex justify-end"><Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}><Save />{t('investmentAdvisor.savePlan')}</Button></div>}
    </>}
  </div>
}

function AllocationChart({ preview }: { preview: ContributionPreview }) {
  const { t } = useTranslation()
  const rows = preview.class_snapshot.map((strategyClass, index) => ({
    ...strategyClass,
    total: Number(preview.class_totals[strategyClass.id] ?? 0),
    color: `var(--chart-${(index % 5) + 1})`,
  })).filter((row) => row.total > 0)
  return <Card><CardHeader className={advisorCardHeaderClass}><CardTitle>{t('investmentAdvisor.allocationMix')}</CardTitle></CardHeader><CardContent className="space-y-3 pb-6">
    <div className="flex h-3 overflow-hidden rounded-full bg-muted" role="img" aria-label={t('investmentAdvisor.allocationMix')}>{rows.map((row) => <div key={row.id} style={{ width: `${preview.new_total ? row.total / preview.new_total * 100 : 0}%`, backgroundColor: row.color }} />)}</div>
    <div className="grid gap-2 sm:grid-cols-2">{rows.map((row) => <div key={row.id} className="flex items-center justify-between text-xs"><span className="flex items-center gap-2"><span className="size-2 rounded-full" style={{ backgroundColor: row.color }} />{snapshotClassLabel(row.name, t)}</span><span className="tabular-nums">{preview.new_total ? (row.total / preview.new_total * 100).toFixed(1) : '0.0'}%</span></div>)}</div>
  </CardContent></Card>
}

function HistoryView({ strategy, canWrite, locale, mask }: { strategy: InvestmentStrategy; canWrite: boolean; locale: string; mask: (value: string) => string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const plansQuery = useQuery({ queryKey: ['investment-plans', strategy.id], queryFn: () => investmentStrategies.plans(strategy.id) })
  const [expanded, setExpanded] = useState('')
  const [livePrices, setLivePrices] = useState<Record<string, PlanPriceRefresh>>({})
  const [planToDelete, setPlanToDelete] = useState<ContributionPlan | null>(null)
  const executeMutation = useMutation({ mutationFn: ({ plan, allocation, payload }: { plan: ContributionPlan; allocation: NonNullable<ContributionPlan['allocations'][number]>; payload: { executed: boolean; actual_value?: number | null; actual_quantity?: number | null; note?: string | null } }) => investmentStrategies.updateExecution(strategy.id, plan.id, allocation.id!, payload), onSuccess: (updated) => { queryClient.setQueryData<ContributionPlan[]>(['investment-plans', strategy.id], (rows) => rows?.map((row) => row.id === updated.id ? updated : row) ?? []) }, onError: (error) => toast.error(apiError(error, t('common.error'), t)) })
  const priceMutation = useMutation({ mutationFn: (planId: string) => investmentStrategies.refreshPlanPrices(strategy.id, planId), onSuccess: (result) => setLivePrices((current) => ({ ...current, [result.plan_id]: result })), onError: (error) => toast.error(apiError(error, t('investmentAdvisor.refreshPlanPricesError'), t)) })
  const executeGroupMutation = useMutation({
    mutationFn: async ({ plan, allocations }: { plan: ContributionPlan; allocations: ContributionAllocation[] }) => {
      const prices = new Map(livePrices[plan.id]?.allocations.map((item) => [item.allocation_id, item]))
      await Promise.all(allocations.filter((item) => !item.executed_at).map((allocation) => {
        const current = allocation.id ? prices.get(allocation.id) : undefined
        return investmentStrategies.updateExecution(strategy.id, plan.id, allocation.id!, {
          executed: true,
          actual_value: Number(current?.estimated_value ?? allocation.suggested_value),
          actual_quantity: current?.estimated_quantity ?? allocation.suggested_quantity,
        })
      }))
      return investmentStrategies.plans(strategy.id)
    },
    onSuccess: (plans) => queryClient.setQueryData(['investment-plans', strategy.id], plans),
    onError: (error) => toast.error(apiError(error, t('common.error'), t)),
  })
  const deleteMutation = useMutation({
    mutationFn: (planId: string) => investmentStrategies.deletePlan(strategy.id, planId),
    onSuccess: (_, planId) => {
      queryClient.setQueryData<ContributionPlan[]>(['investment-plans', strategy.id], (rows) => rows?.filter((row) => row.id !== planId) ?? [])
      setLivePrices((current) => {
        const next = { ...current }
        delete next[planId]
        return next
      })
      if (expanded === planId) setExpanded('')
      setPlanToDelete(null)
      toast.success(t('investmentAdvisor.planDeleted'))
    },
    onError: (error) => toast.error(apiError(error, t('investmentAdvisor.deletePlanError'), t)),
  })
  if (plansQuery.isLoading) return <Card className="mx-auto max-w-5xl"><CardContent className="py-12 text-center text-sm text-muted-foreground">{t('common.loading')}</CardContent></Card>
  if (plansQuery.isError) return <Card className="mx-auto max-w-5xl"><CardContent className="py-12 text-center text-sm text-destructive">{t('common.error')}</CardContent></Card>
  if (!plansQuery.data?.length) return <Card className="mx-auto max-w-5xl"><CardContent className="py-12 text-center text-sm text-muted-foreground">{t('investmentAdvisor.noHistory')}</CardContent></Card>
  return <><div className="mx-auto max-w-5xl space-y-3">{plansQuery.data.map((plan) => {
    const planMoney = (value: number) => mask(formatCurrency(value, plan.currency, locale))
    const completed = plan.allocations.filter((item) => item.executed_at).length
    const pricing = livePrices[plan.id]
    const pricesByAllocation = new Map(pricing?.allocations.map((item) => [item.allocation_id, item]))
    const groups = groupContributionAllocations(plan)
    return <Card key={plan.id}><CardHeader className={`${advisorCardHeaderClass} cursor-pointer`} tabIndex={0} role="button" aria-expanded={expanded === plan.id} onClick={() => setExpanded(expanded === plan.id ? '' : plan.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setExpanded(expanded === plan.id ? '' : plan.id) } }}><div className="flex items-center justify-between gap-3"><div><CardTitle>{planMoney(plan.amount)}</CardTitle><CardDescription>{new Date(plan.created_at).toLocaleString(locale)} · {plan.algorithm_version}</CardDescription></div><div className="flex shrink-0 items-center gap-2"><Badge variant="secondary">{completed}/{plan.allocations.length} {t('investmentAdvisor.executed')}</Badge>{canWrite && <Button type="button" size="icon" variant="ghost" className="text-muted-foreground hover:text-destructive" aria-label={t('investmentAdvisor.deletePlan')} onClick={(event) => { event.stopPropagation(); setPlanToDelete(plan) }} onKeyDown={(event) => event.stopPropagation()}><Trash2 /></Button>}</div></div></CardHeader>{expanded === plan.id && <CardContent className="space-y-4 pb-6">
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-muted/20 p-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-medium">{t('investmentAdvisor.stagedExecution')}</p><p className="mt-1 text-sm text-muted-foreground">{t('investmentAdvisor.stagedExecutionDescription')}</p></div>{canWrite && <Button variant="outline" disabled={priceMutation.isPending || completed === plan.allocations.length} onClick={() => priceMutation.mutate(plan.id)}><RefreshCw className={priceMutation.isPending && priceMutation.variables === plan.id ? 'animate-spin' : ''} />{priceMutation.isPending && priceMutation.variables === plan.id ? t('investmentAdvisor.refreshingCurrentPrices') : t('investmentAdvisor.refreshCurrentPrices')}</Button>}</div>
      {priceMutation.isPending && priceMutation.variables === plan.id && <div className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/10 p-4" role="status" aria-live="polite"><LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-primary" /><div><p className="font-medium">{t('investmentAdvisor.refreshingCurrentPrices')}</p><p className="mt-1 text-sm text-muted-foreground">{t('investmentAdvisor.refreshingCurrentPricesDescription')}</p></div></div>}
      {pricing && <div className="rounded-xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm text-sky-800 dark:text-sky-200"><p className="font-medium">{t('investmentAdvisor.pricesRefreshedAt', { date: new Date(pricing.refreshed_at).toLocaleString(locale) })}</p><p className="mt-1 text-muted-foreground">{t('investmentAdvisor.pricesRefreshedDescription')}</p>{pricing.warnings.length > 0 && <p className="mt-2 text-amber-700 dark:text-amber-300">{t('investmentAdvisor.somePricesUnavailable')}</p>}</div>}
      {groups.map((group) => {
        const groupCompleted = group.allocations.filter((item) => item.executed_at).length
        const pendingAllocations = group.allocations.filter((item) => !item.executed_at)
        const hasUnavailablePrice = Boolean(pricing && pendingAllocations.some((item) => item.id && pricesByAllocation.get(item.id)?.available === false))
        const classSnapshot = plan.class_snapshot.find((item) => item.id === group.id)
        const displayCurrency = classSnapshot?.display_currency
        const groupTotal = group.allocations.reduce((total, item) => total + Number(item.suggested_value), 0)
        const groupSecondary = secondaryCurrencyMoney(groupTotal, displayCurrency, plan.currency, plan.fx_rates, locale, mask)
        const savedSecondaryMoney = (value: number) => secondaryCurrencyMoney(value, displayCurrency, plan.currency, plan.fx_rates, locale, mask)
        const currentSecondaryMoney = (value: number) => secondaryCurrencyMoney(value, displayCurrency, plan.currency, pricing?.fx_rates ?? plan.fx_rates, locale, mask)
        return <section key={group.id} className="overflow-hidden rounded-xl border border-border" aria-labelledby={`history-group-${plan.id}-${group.id}`}><div className="flex flex-col gap-3 bg-muted/40 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"><div><h3 id={`history-group-${plan.id}-${group.id}`} className="font-semibold">{snapshotClassLabel(group.name, t)}</h3><p className="mt-0.5 text-xs text-muted-foreground">{t('investmentAdvisor.categoryExecutionProgress', { completed: groupCompleted, total: group.allocations.length })} · {planMoney(groupTotal)}{groupSecondary ? ` · ${groupSecondary}` : ''}</p></div>{groupCompleted === group.allocations.length ? <Badge>{t('investmentAdvisor.categoryComplete')}</Badge> : canWrite && <Button size="sm" disabled={executeGroupMutation.isPending || executeMutation.isPending || hasUnavailablePrice} onClick={() => executeGroupMutation.mutate({ plan, allocations: group.allocations })}><Check />{executeGroupMutation.isPending ? t('investmentAdvisor.markingCategoryComplete') : t('investmentAdvisor.markCategoryComplete')}</Button>}</div><div className="space-y-3 p-3">{group.allocations.map((item) => <ExecutionEditor key={`${item.id}-${pricing?.refreshed_at ?? 'saved'}`} allocation={item} livePrice={item.id ? pricesByAllocation.get(item.id) : undefined} canWrite={canWrite} money={planMoney} secondaryMoney={savedSecondaryMoney} currentSecondaryMoney={currentSecondaryMoney} locale={locale} pending={executeMutation.isPending || executeGroupMutation.isPending} onSubmit={(payload) => executeMutation.mutate({ plan, allocation: item, payload })} />)}</div></section>
      })}
    </CardContent>}</Card>
  })}</div><Dialog open={Boolean(planToDelete)} onOpenChange={(open) => { if (!open && !deleteMutation.isPending) setPlanToDelete(null) }}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>{t('investmentAdvisor.deletePlanTitle')}</DialogTitle><DialogDescription>{planToDelete ? t('investmentAdvisor.deletePlanDescription', { amount: mask(formatCurrency(planToDelete.amount, planToDelete.currency, locale)), date: new Date(planToDelete.created_at).toLocaleString(locale) }) : ''}</DialogDescription></DialogHeader><p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{t('investmentAdvisor.deletePlanWarning')}</p><DialogFooter><Button variant="outline" disabled={deleteMutation.isPending} onClick={() => setPlanToDelete(null)}>{t('common.cancel')}</Button><Button variant="destructive" disabled={deleteMutation.isPending || !planToDelete} onClick={() => { if (planToDelete) deleteMutation.mutate(planToDelete.id) }}><Trash2 />{deleteMutation.isPending ? t('common.deleting') : t('common.delete')}</Button></DialogFooter></DialogContent></Dialog></>
}

function ExecutionEditor({ allocation, livePrice, canWrite, money, secondaryMoney, currentSecondaryMoney, locale, pending, onSubmit }: { allocation: ContributionAllocation; livePrice?: PlanAllocationPrice; canWrite: boolean; money: (value: number) => string; secondaryMoney?: (value: number) => string | null; currentSecondaryMoney?: (value: number) => string | null; locale: string; pending: boolean; onSubmit: (payload: { executed: boolean; actual_value?: number | null; actual_quantity?: number | null; note?: string | null }) => void }) {
  const { t } = useTranslation()
  const [actualValue, setActualValue] = useState(String(allocation.actual_value ?? (livePrice?.available ? livePrice.estimated_value : allocation.suggested_value)))
  const [actualQuantity, setActualQuantity] = useState(String(allocation.actual_quantity ?? (livePrice?.available ? livePrice.estimated_quantity : allocation.suggested_quantity) ?? ''))
  const [note, setNote] = useState(allocation.execution_note ?? '')
  const originalSecondary = secondaryMoney?.(allocation.suggested_value)
  const liveSecondary = livePrice ? currentSecondaryMoney?.(livePrice.estimated_value) : null
  return <div className="space-y-3 rounded-lg border border-border p-3"><div><p className="font-medium">{allocation.instrument_name}</p><p className="text-xs text-muted-foreground">{t('investmentAdvisor.originalSuggestion')}: {money(allocation.suggested_value)}{originalSecondary ? ` · ${originalSecondary}` : ''} · {allocation.suggested_quantity ?? '—'}</p></div>{livePrice && !allocation.executed_at ? livePrice.available ? <div className="flex flex-wrap gap-x-4 gap-y-1 rounded-lg bg-primary/10 px-3 py-2 text-xs text-primary"><span>{t('investmentAdvisor.currentUnitPrice')}: <strong>{livePrice.unit_price != null ? money(livePrice.unit_price) : '—'}</strong></span><span>{t('investmentAdvisor.currentEstimate')}: <strong>{money(livePrice.estimated_value)}{liveSecondary ? ` · ${liveSecondary}` : ''} · {livePrice.estimated_quantity ?? '—'}</strong></span>{livePrice.price_as_of && <span className="text-muted-foreground">{new Date(livePrice.price_as_of).toLocaleString(locale)}</span>}</div> : <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">{t('investmentAdvisor.currentPriceUnavailable')}</p> : null}{allocation.executed_at ? <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{t('investmentAdvisor.executedAt', { date: new Date(allocation.executed_at).toLocaleString(locale) })}{allocation.actual_value != null ? ` · ${money(allocation.actual_value)}` : ''}{allocation.execution_note ? ` · ${allocation.execution_note}` : ''}</span>{canWrite && <Button size="sm" variant="outline" disabled={pending} onClick={() => onSubmit({ executed: false })}><X />{t('investmentAdvisor.clearExecution')}</Button>}</div> : canWrite ? <div className="grid gap-2 sm:grid-cols-[1fr_1fr_2fr_auto]"><Field label={t('investmentAdvisor.actualValue')}><Input type="number" min="0" step="0.01" value={actualValue} onChange={(event) => setActualValue(event.target.value)} /></Field><Field label={t('investmentAdvisor.actualQuantity')}><Input type="number" min="0" step="0.00000001" value={actualQuantity} onChange={(event) => setActualQuantity(event.target.value)} /></Field><Field label={t('investmentAdvisor.executionNote')}><Input value={note} onChange={(event) => setNote(event.target.value)} /></Field><div className="flex items-end"><Button size="sm" disabled={pending || !actualValue} onClick={() => onSubmit({ executed: true, actual_value: Number(actualValue), actual_quantity: actualQuantity ? Number(actualQuantity) : null, note: note || null })}><Check />{t('investmentAdvisor.markExecuted')}</Button></div></div> : null}</div>
}
