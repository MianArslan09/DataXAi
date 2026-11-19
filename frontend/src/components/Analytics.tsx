import { useState } from 'react'
import { ChartNoAxesCombined, Wallet } from 'lucide-react'
import { useWork } from './Workspace'
import { Card, Empty, Field, MoreLink, Table } from './ui'
import { date, money, type ForecastSummary } from '../services/contracts'
import { href } from '../routes'

function fullDate(value: string) {
  return new Date(value).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'Asia/Karachi',
  })
}

function DemandChart({ forecast, detail }: { forecast: ForecastSummary; detail: boolean }) {
  const [selected, setSelected] = useState<number | null>(null)
  const weeks = forecast.weeks
  const firstPrediction = weeks.findIndex((week) => week.kind === 'Predicted')
  const active = selected === null ? null : weeks[selected]
  const min = Math.min(0, ...weeks.map((week) => week.units))
  const max = Math.max(0, ...weeks.map((week) => week.units))
  const step = Math.max(1, Math.ceil((max - min) / 4))
  const x = (i: number) => (weeks.length === 1 ? 260 : 38 + i * (444 / (weeks.length - 1)))
  const y = (units: number) => 131 - ((units - min) / (4 * step)) * 104
  const boundary = firstPrediction > 0 ? (x(firstPrediction - 1) + x(firstPrediction)) / 2 : 34
  const historicalCount = firstPrediction < 0 ? weeks.length : firstPrediction
  return (
    <div className="demand-chart">
      <div className="chart-meta">
        <span>{forecast.product} · units / week</span>
        <div>
          <i />
          Historical
          <i className="pred" />
          Predicted
        </div>
      </div>
      <svg
        className="chart"
        viewBox="0 0 510 161"
        role="img"
        aria-label={
          forecast.product +
          ' weekly demand in units. ' +
          weeks.map((week) => fullDate(week.start) + ' ' + week.kind + ' ' + week.units).join(', ')
        }
      >
        {firstPrediction >= 0 && (
          <g>
            <rect x={boundary} y="13" width={498 - boundary} height="120" fill="#f1f9f7" />
            <line
              x1={boundary}
              x2={boundary}
              y1="13"
              y2="133"
              stroke="#64748b"
              strokeDasharray="2 3"
            />
          </g>
        )}
        {[0, 1, 2, 3, 4].map((i) => {
          const tick = min + step * i
          return (
            <g key={i}>
              <line
                x1="34"
                x2="498"
                y1={y(tick)}
                y2={y(tick)}
                stroke="#e1e8e9"
                strokeDasharray="3 4"
              />
              <text x="24" y={y(tick) + 4} textAnchor="end">
                {tick}
              </text>
            </g>
          )
        })}
        {historicalCount > 1 && (
          <polygon
            points={[
              x(0) + ',' + y(0),
              ...weeks.slice(0, historicalCount).map((week, i) => x(i) + ',' + y(week.units)),
              x(historicalCount - 1) + ',' + y(0),
            ].join(' ')}
            fill="#dff2ed"
          />
        )}
        {detail && firstPrediction >= 0 && (
          <text x={boundary} y="11" textAnchor={firstPrediction === 0 ? 'start' : 'middle'}>
            Forecast begins
          </text>
        )}
        {weeks.map((week, i) => {
          const previous = weeks[i - 1]
          const predicted = week.kind === 'Predicted'
          return (
            <g
              key={week.start}
              onMouseEnter={() => setSelected(i)}
              onMouseLeave={() => setSelected(null)}
            >
              {previous && (
                <line
                  x1={x(i - 1)}
                  y1={y(previous.units)}
                  x2={x(i)}
                  y2={y(week.units)}
                  stroke="#0f766e"
                  strokeWidth="3"
                  strokeDasharray={predicted ? '6 5' : undefined}
                />
              )}
              <circle cx={x(i)} cy={y(week.units)} r="10" fill="transparent" />
              <circle
                cx={x(i)}
                cy={y(week.units)}
                r="3.5"
                fill={predicted ? 'white' : '#0f766e'}
                stroke="#0f766e"
                strokeWidth="2"
              />
              {detail && (
                <text x={x(i)} y={y(week.units) - 9} textAnchor="middle">
                  {week.units}
                </text>
              )}
              {(i % 2 === 0 || i === weeks.length - 1) && (
                <text x={x(i)} y="153" textAnchor="middle">
                  {date(week.start)}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {active && (
        <div className="chart-tooltip">
          {date(active.start)} · {active.kind} · {active.units} units
        </div>
      )}
    </div>
  )
}

export function Forecast({ detail = false }: { detail?: boolean }) {
  const { data, user } = useWork()
  const [selectedId, setSelectedId] = useState('')
  const forecast = data.forecasts.find((entry) => entry.id === selectedId) ?? data.forecasts[0]
  if (!forecast) {
    return (
      <Card title="Weekly product demand" icon={ChartNoAxesCombined}>
        <Empty>No forecast available for this business.</Empty>
      </Card>
    )
  }
  const history = forecast.weeks.filter((week) => week.kind === 'Historical')
  const predictions = forecast.weeks.filter((week) => week.kind === 'Predicted')
  const cutoff = history.at(-1)
  const end = predictions.at(-1)
  return (
    <Card
      title="Weekly product demand"
      icon={ChartNoAxesCombined}
      action={!detail && <MoreLink to={href('Seller', 'forecasts')}>Details</MoreLink>}
    >
      {detail && data.forecasts.length > 1 && (
        <div className="padded">
          <Field label="Product forecast">
            <select value={forecast.id} onChange={(event) => setSelectedId(event.target.value)}>
              {data.forecasts.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.product} · {entry.id}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      {forecast.weeks.length ? (
        <>
          <div className={detail ? 'weekly-evidence' : undefined}>
            <DemandChart
              key={JSON.stringify([forecast.businessId, forecast.id])}
              forecast={forecast}
              detail={detail}
            />
            {detail && (
              <section className="padded">
                <h3>Forecast evidence</h3>
                <p>
                  {user.businessName} · {forecast.id} · Illustrative weekly series
                </p>
                <dl className="details">
                  <dt>Last historical week beginning</dt>
                  <dd>{cutoff ? fullDate(cutoff.start) : 'Unavailable'}</dd>
                  <dt>Prediction horizon</dt>
                  <dd>
                    {end
                      ? predictions.length +
                        ' weekly periods, beginning ' +
                        fullDate(predictions[0].start) +
                        ' through the week of ' +
                        fullDate(end.start)
                      : 'No predicted weeks supplied'}
                  </dd>
                </dl>
                <p className="muted">
                  These fixture values are supplied without model execution. Model name, run ID,
                  confidence intervals and evaluation metrics are unavailable. Before/after
                  forecasting performance has not been evaluated.
                </p>
              </section>
            )}
          </div>
          {detail && (
            <Table
              headers={['Week beginning', 'Series', 'Units per week']}
              rows={forecast.weeks.map((week) => [date(week.start), week.kind, week.units])}
            />
          )}
        </>
      ) : (
        <Empty>No weekly values supplied for {forecast.product}.</Empty>
      )}
    </Card>
  )
}

export function Spending({ detail = false }: { detail?: boolean }) {
  const { data, user } = useWork()
  const first = data.spending[0]
  return (
    <Card
      title="Customer spending"
      icon={Wallet}
      action={!detail && <MoreLink to={href('Seller', 'spending')}>Details</MoreLink>}
    >
      {!first ? (
        <Empty>No spending estimate available for this business.</Empty>
      ) : (
        <>
          <div className={detail ? 'weekly-evidence' : undefined}>
            <div className="spending">
              <div className="horizon">
                <i />
                {first.horizonDays}-day prediction
              </div>
              <strong>{money(first.amount, first.currency)}</strong>
              <p>Estimated spend · {first.customer}</p>
              <div className="estimate-date">Estimate as of {fullDate(first.asOf)}</div>
            </div>
            {detail && (
              <section className="padded">
                <h3>Prediction evidence</h3>
                <p>{user.businessName} · Illustrative customer estimates</p>
                <p>
                  Each estimate covers expected spending over the next 90 days from its as-of date.
                  Actual historical spending and realised outcomes are unavailable.
                </p>
                <p className="muted">
                  The fixture supplies estimates without model execution. A before/after comparison
                  of predicted spending has not been evaluated.
                </p>
              </section>
            )}
          </div>
          {detail && (
            <div className="estimate-table">
              <Table
                headers={[
                  'Customer',
                  'Prediction horizon',
                  'Spending estimate',
                  'As of',
                  'Estimate reference',
                ]}
                rows={data.spending.map((estimate) => [
                  estimate.customer,
                  estimate.horizonDays + ' days',
                  money(estimate.amount, estimate.currency) + ' ' + estimate.currency,
                  fullDate(estimate.asOf),
                  estimate.id,
                ])}
              />
            </div>
          )}
        </>
      )}
    </Card>
  )
}
