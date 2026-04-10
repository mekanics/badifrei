/* pool.js — Pool detail page logic
 *
 * Server-side data is injected via a hidden <div id="ssr-data"> element
 * using data-* attributes to avoid unsafe-inline scripts.
 */

// ── Analytics ────────────────────────────────────────────────────────────────

function track(name, data) {
	if (typeof umami !== 'undefined') umami.track(name, data)
}

// ── Live occupancy count refresh ───────────────────────────────────────────

async function refreshLiveCount() {
	const el = document.getElementById('detail-live-count')
	if (!el) return
	const uid = el.dataset.uid
	const cap = parseInt(el.dataset.capacity) || 0
	try {
		const res = await fetch('/api/current')
		if (!res.ok) return
		const data = await res.json()
		const item = data.find(d => d.pool_uid === uid)
		if (!item || item.current_fill == null) return
		const capStr = cap > 0 ? ` / ${cap}` : ''
		const pct = item.occupancy_pct != null ? ` (${Math.round(item.occupancy_pct)}%)` : ''
		el.textContent = `${item.current_fill}${capStr} Gäste${pct}`
		el.className = 'detail-live-count'
		const p = item.occupancy_pct ?? 0
		el.classList.add(p <= 50 ? 'count-green' : p <= 80 ? 'count-yellow' : 'count-red')
	} catch (e) {
		/* silent */
	}
}

refreshLiveCount()
setInterval(refreshLiveCount, 60000)

// ── Chart ───────────────────────────────────────────────────────────────────

const appData = document.getElementById('ssr-data')
const POOL_UID = appData.dataset.poolUid
const SSR_DATE = appData.dataset.todayDate
const SSR_PREDICTIONS = JSON.parse(appData.dataset.ssrPredictions)

function todayZurich() {
	return new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Zurich' })
}

function currentZurichHour() {
	const h = parseInt(
		new Date().toLocaleString('en-US', { timeZone: 'Europe/Zurich', hour: 'numeric', hour12: false }),
		10
	)
	return h === 24 ? 0 : h
}

const picker = document.getElementById('date-picker')
picker.value = todayZurich()

const ctx = document.getElementById('occupancy-chart').getContext('2d')
const chart = new Chart(ctx, {
	type: 'line',
	data: {
		labels: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0') + ':00'),
		datasets: [
			{
				label: 'Aktuell',
				data: Array(24).fill(null),
				borderColor: '#111111',
				backgroundColor: 'rgba(17,17,17,0.07)',
				fill: false,
				tension: 0.3,
				pointRadius: 3,
				borderDash: [],
			},
			{
				label: 'Prognose',
				data: SSR_PREDICTIONS,
				borderColor: '#0057ff',
				backgroundColor: 'rgba(0,87,255,0.07)',
				fill: true,
				tension: 0.3,
				pointRadius: 3,
				borderDash: [5, 5],
			},
		],
	},
	options: {
		responsive: true,
		maintainAspectRatio: false,
		scales: {
			y: {
				min: 0,
				max: 100,
				title: { display: true, text: 'Auslastung %', color: '#666' },
				ticks: { color: '#666' },
				grid: { color: '#e5e5e5' },
			},
			x: {
				title: { display: true, text: 'Uhrzeit', color: '#666' },
				ticks: { color: '#666' },
				grid: { color: '#e5e5e5' },
			},
		},
		interaction: { mode: 'index', intersect: false },
		plugins: {
			legend: {
				display: true,
				position: 'bottom',
				labels: { usePointStyle: true, pointStyle: 'line', boxWidth: 30, color: '#111111' },
			},
			tooltip: {
				mode: 'index',
				intersect: false,
				callbacks: {
					label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y !== null ? Math.round(ctx.parsed.y) + '%' : '—'}`,
				},
			},
		},
	},
})

async function loadChart(date) {
	const today = todayZurich()
	const isPast = date < today
	const isToday = date === today
	const isFuture = date > today

	try {
		let predValues
		if (date === SSR_DATE) {
			predValues = SSR_PREDICTIONS
		} else {
			const predRes = await fetch(`/predict/range?pool_uid=${encodeURIComponent(POOL_UID)}&date=${date}`)
			if (!predRes.ok) return
			const predData = await predRes.json()
			predValues = predData.predictions.map(p => p.predicted_occupancy_pct)
		}
		const allZero = predValues.every(v => v === 0.0)
		document.getElementById('no-model-msg').style.display = allZero ? 'block' : 'none'
		chart.data.datasets[1].data = predValues

		let actuals = Array(24).fill(null)
		if (isPast || isToday) {
			try {
				const histRes = await fetch(`/api/history?pool_uid=${encodeURIComponent(POOL_UID)}&date=${date}`)
				if (histRes.ok) {
					const histData = await histRes.json()
					actuals = histData.actuals.map(a => a.occupancy_pct)
					if (isToday) {
						const currentHour = currentZurichHour()
						actuals = actuals.map((v, i) => (i <= currentHour ? v : null))
					}
				}
			} catch (e) {
				console.warn('Verlaufsdaten konnten nicht geladen werden', e)
			}
		}
		chart.data.datasets[0].data = actuals
		chart.update()
	} catch (e) {
		console.warn('Prognosedaten konnten nicht geladen werden', e)
	}
}

loadChart(picker.value)
picker.addEventListener('change', () => {
	const selected = picker.value
	const today = todayZurich()
	const direction = selected < today ? 'past' : selected === today ? 'today' : 'future'
	track('date-picker-change', { pool_uid: POOL_UID, direction: direction })
	loadChart(selected)
})
