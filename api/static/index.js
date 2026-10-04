/* index.js — Pool overview dashboard logic
 *
 * Extracted from inline script to comply with Content-Security-Policy
 * (no 'unsafe-inline' in script-src).
 */

/* ── Analytics ────────────────────────────────────────────────── */
function track(name, data) {
	if (typeof umami !== 'undefined') umami.track(name, data)
}

/* ── Filter state ─────────────────────────────────────────────── */
const filters = { open: false, types: new Set(), city: '' }

const FILTERS_KEY = 'badi_filters'
const FILTERS_VERSION = 1
const FILTER_OPEN_KEY = 'badi_filter_open'
const RETURN_CARD_KEY = 'badi_return_card'

const TYPE_LABELS = {
	freibad: 'Freibad',
	hallenbad: 'Hallenbad',
	strandbad: 'Strandbad',
	seebad: 'Seebad',
	flussbad: 'Flussbad',
	kombibad: 'Kombibad',
}

let occupancySettled = false
let returnCardTarget = null

function storageGet(area, key) {
	try {
		return window[area].getItem(key)
	} catch {
		return null
	}
}

function storageSet(area, key, value) {
	try {
		window[area].setItem(key, value)
	} catch {
		// QuotaExceededError / SecurityError — session-only filters still work
	}
}

function storageRemove(area, key) {
	try {
		window[area].removeItem(key)
	} catch {
		// blocked storage
	}
}

function allowedFilterValues() {
	const types = new Set()
	document.querySelectorAll('.filter-btn-type[data-value]').forEach(btn => {
		if (btn.dataset.value) types.add(btn.dataset.value)
	})
	const cities = new Set()
	const sel = document.getElementById('city-select')
	if (sel) {
		;[...sel.options].forEach(o => {
			if (o.value) cities.add(o.value)
		})
	}
	return { types, cities }
}

function normalizeFilters(raw, allowed) {
	const src = raw && typeof raw === 'object' ? raw : {}
	const types = Array.isArray(src.types) ? src.types : []
	return {
		open: src.open === '1',
		types: new Set(types.filter(t => typeof t === 'string' && allowed.types.has(t))),
		city: typeof src.city === 'string' && allowed.cities.has(src.city) ? src.city : '',
	}
}

function hasAnyFilter(f) {
	return f.open || f.types.size > 0 || f.city !== ''
}

function parseUrlFilters(search) {
	const params = new URLSearchParams(search)
	const typeParam = params.get('type')
	return {
		open: params.get('open'),
		types: typeParam ? typeParam.split(',').filter(Boolean) : [],
		city: params.get('city'),
	}
}

function readStoredFilters() {
	const raw = { open: storageGet('sessionStorage', FILTER_OPEN_KEY) }
	const text = storageGet('localStorage', FILTERS_KEY)
	if (!text) return raw
	try {
		const parsed = JSON.parse(text)
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || parsed.v !== FILTERS_VERSION) {
			return raw
		}
		raw.types = parsed.types
		raw.city = parsed.city
		return raw
	} catch {
		return raw
	}
}

function setFilters(next) {
	filters.open = !!next.open
	filters.types = new Set(next.types)
	filters.city = next.city || ''
	const btnOpen = document.getElementById('btn-open')
	if (btnOpen) btnOpen.setAttribute('aria-pressed', filters.open ? 'true' : 'false')
	document.querySelectorAll('.filter-btn-type').forEach(btn => {
		btn.setAttribute('aria-pressed', filters.types.has(btn.dataset.value) ? 'true' : 'false')
	})
	const sel = document.getElementById('city-select')
	if (sel) sel.value = filters.city
}

function restoreFilters() {
	const allowed = allowedFilterValues()
	const fromUrl = normalizeFilters(parseUrlFilters(location.search), allowed)
	if (hasAnyFilter(fromUrl)) {
		setFilters(fromUrl)
		return 'url'
	}
	const fromStorage = normalizeFilters(readStoredFilters(), allowed)
	if (hasAnyFilter(fromStorage)) {
		setFilters(fromStorage)
		return 'storage'
	}
	return null
}

function persistFilters() {
	if (filters.types.size || filters.city) {
		storageSet(
			'localStorage',
			FILTERS_KEY,
			JSON.stringify({
				v: FILTERS_VERSION,
				types: [...filters.types].sort(),
				city: filters.city,
			}),
		)
	} else {
		storageRemove('localStorage', FILTERS_KEY)
	}
	if (filters.open) {
		storageSet('sessionStorage', FILTER_OPEN_KEY, '1')
	} else {
		storageRemove('sessionStorage', FILTER_OPEN_KEY)
	}
}

function buildFilterBar() {
	const types = new Set(),
		cities = new Set(),
		cityLabels = {}

	document.querySelectorAll('.pool-card').forEach(card => {
		if (card.dataset.type) types.add(card.dataset.type)
		if (card.dataset.city) {
			cities.add(card.dataset.city)
			const section = card.closest('.section-block')
			if (section) {
				const h2 = section.querySelector('.section-title')
				if (h2) cityLabels[card.dataset.city] = h2.textContent.replace('Schwimmbäder', '').trim()
			}
			if (!cityLabels[card.dataset.city])
				cityLabels[card.dataset.city] = card.dataset.city.charAt(0).toUpperCase() + card.dataset.city.slice(1)
		}
	})

	// Type toggle buttons
	const typeGroup = document.getElementById('type-filter-group')
	;[...types].sort().forEach(t => {
		const btn = document.createElement('button')
		btn.className = `filter-btn filter-btn-type filter-type-${t}`
		btn.dataset.value = t
		btn.textContent = TYPE_LABELS[t] || t
		btn.setAttribute('aria-pressed', 'false')
		btn.addEventListener('click', () => {
			const active = filters.types.has(t)
			active ? filters.types.delete(t) : filters.types.add(t)
			btn.setAttribute('aria-pressed', active ? 'false' : 'true')
			track('filter-type-toggle', { type: t, action: active ? 'off' : 'on' })
			applyFilters()
		})
		typeGroup.appendChild(btn)
	})

	// City dropdown — only show if more than one city
	const cityGroup = document.getElementById('city-filter-group')
	if (cities.size <= 1) {
		cityGroup.style.display = 'none'
		return
	}
	const sel = document.getElementById('city-select')
	;[...cities]
		.sort((a, b) => (cityLabels[a] || a).localeCompare(cityLabels[b] || b))
		.forEach(c => {
			const opt = document.createElement('option')
			opt.value = c
			opt.textContent = cityLabels[c] || c
			sel.appendChild(opt)
		})
	sel.addEventListener('change', () => {
		filters.city = sel.value
		if (sel.value) track('filter-city-change', { city: sel.value })
		applyFilters()
	})
}

function updateEmptyState(shown) {
	const el = document.getElementById('filter-empty')
	const textEl = document.getElementById('filter-empty-text')
	if (!el || !textEl) return
	const active = hasAnyFilter(filters)
	if (!active || shown > 0 || (filters.open && !occupancySettled)) {
		el.hidden = true
		return
	}
	if (filters.open && occupancySettled && !document.querySelector('.pool-card[data-open]')) {
		textEl.textContent =
			'Live-Status nicht verfügbar – der Filter „Offen“ kann gerade nicht angewendet werden.'
	} else {
		textEl.textContent = 'Keine Bäder für diese Filter.'
	}
	el.hidden = false
}

function applyFilters() {
	let shown = 0,
		total = 0
	document.querySelectorAll('.pool-card').forEach(card => {
		total++
		const typeOk = filters.types.size === 0 || filters.types.has(card.dataset.type)
		const cityOk = !filters.city || card.dataset.city === filters.city
		const openOk = !filters.open || card.dataset.open === 'true'
		const visible = typeOk && cityOk && openOk
		card.style.display = visible ? '' : 'none'
		if (visible) shown++
	})

	document.querySelectorAll('.section-block:not(#favorites-section)').forEach(section => {
		const anyVisible = [...section.querySelectorAll('.pool-card')].some(c => c.style.display !== 'none')
		section.style.display = anyVisible ? '' : 'none'
	})

	// Favorites: filter cloned cards; show section only if favorites exist AND at least one passes filters
	const favSection = document.getElementById('favorites-section')
	if (favSection && getFavorites().length > 0) {
		let anyFavVisible = false
		favSection.querySelectorAll('.pool-card').forEach(card => {
			const typeOk = filters.types.size === 0 || filters.types.has(card.dataset.type)
			const cityOk = !filters.city || card.dataset.city === filters.city
			const openOk = !filters.open || card.dataset.open === 'true'
			const vis = typeOk && cityOk && openOk
			card.style.display = vis ? '' : 'none'
			if (vis) anyFavVisible = true
		})
		favSection.style.display = anyFavVisible ? '' : 'none'
	}

	const hasFilters = hasAnyFilter(filters)
	const countEl = document.getElementById('filter-count')
	if (countEl) countEl.textContent = hasFilters ? `${shown} von ${total}` : ''
	const resetBtn = document.getElementById('filter-reset')
	if (resetBtn) resetBtn.style.display = hasFilters ? '' : 'none'

	updateEmptyState(shown)
	syncToUrl()
	persistFilters()
}

function syncToUrl() {
	const params = new URLSearchParams(location.search)
	if (filters.open) params.set('open', '1')
	else params.delete('open')
	if (filters.types.size > 0) params.set('type', [...filters.types].sort().join(','))
	else params.delete('type')
	if (filters.city) params.set('city', filters.city)
	else params.delete('city')
	const qs = params.toString()
	const next = (qs ? `?${qs}` : location.pathname) + location.hash
	history.replaceState(null, '', next)
}

function resetFilters() {
	setFilters({ open: false, types: new Set(), city: '' })
	applyFilters()
}

function rememberReturnCard(card) {
	const uid = card.dataset.uid
	if (!uid) return
	const fav = !!card.closest('#favorites-section')
	storageSet('sessionStorage', RETURN_CARD_KEY, JSON.stringify({ uid, fav }))
}

function consumeReturnCard() {
	const text = storageGet('sessionStorage', RETURN_CARD_KEY)
	storageRemove('sessionStorage', RETURN_CARD_KEY)
	if (!text) return null
	let parsed
	try {
		parsed = JSON.parse(text)
	} catch {
		return null
	}
	if (!parsed || typeof parsed !== 'object' || typeof parsed.uid !== 'string') return null

	let navType = 'navigate'
	try {
		const nav = performance.getEntriesByType('navigation')[0]
		if (nav && nav.type) navType = nav.type
	} catch {
		// treat as navigate
	}
	if (navType !== 'navigate') return null

	const ref = document.referrer
	if (!ref) return null
	try {
		const url = new URL(ref)
		if (url.origin !== location.origin) return null
		if (!url.pathname.startsWith('/bad/')) return null
	} catch {
		return null
	}
	return { uid: parsed.uid, fav: !!parsed.fav }
}

function isCardVisible(card) {
	return card.style.display !== 'none' && card.offsetParent !== null
}

function scrollToReturnCard(target) {
	if (!target) return
	const cards = [...document.querySelectorAll('.pool-card')].filter(c => c.dataset.uid === target.uid)
	const inFav = card => !!card.closest('#favorites-section')
	const preferred = cards.find(c => inFav(c) === target.fav && isCardVisible(c))
	const fallback = cards.find(c => isCardVisible(c))
	const card = preferred || fallback
	if (!card) return
	card.scrollIntoView({ block: 'center' })
	card.focus({ preventScroll: true })
	card.classList.add('pool-card-return')
	const clearReturn = () => {
		card.classList.remove('pool-card-return')
		card.removeEventListener('blur', clearReturn)
	}
	card.addEventListener('blur', clearReturn)
}

function markOccupancySettled() {
	const first = !occupancySettled
	occupancySettled = true
	applyFilters()
	if (first) scrollToReturnCard(returnCardTarget)
}

document.getElementById('btn-open').addEventListener('click', () => {
	filters.open = !filters.open
	document.getElementById('btn-open').setAttribute('aria-pressed', filters.open ? 'true' : 'false')
	track('filter-open-toggle', { active: filters.open })
	applyFilters()
})
document.getElementById('filter-reset').addEventListener('click', resetFilters)
const emptyReset = document.getElementById('filter-empty-reset')
if (emptyReset) emptyReset.addEventListener('click', resetFilters)

/* ── Favorites ─────────────────────────────────────────────────── */
const FAVORITES_KEY = 'badi_favorites'

function getFavorites() {
	try {
		return JSON.parse(localStorage.getItem(FAVORITES_KEY)) || []
	} catch (e) {
		return []
	}
}
function setFavorites(favs) {
	localStorage.setItem(FAVORITES_KEY, JSON.stringify(favs))
}
function isFavorite(uid) {
	return getFavorites().includes(uid)
}

function toggleFavorite(uid) {
	let favs = getFavorites()
	const adding = !favs.includes(uid)
	favs = adding ? [...favs, uid] : favs.filter(f => f !== uid)
	setFavorites(favs)
	track('favorite-toggle', { pool_uid: uid, action: adding ? 'add' : 'remove', total_favorites: favs.length })
	renderSections()
	applyFilters()
}

function applyStarState() {
	document.querySelectorAll('.star-btn').forEach(btn => {
		const fav = isFavorite(btn.dataset.uid)
		btn.classList.toggle('starred', fav)
		btn.setAttribute('aria-label', fav ? 'Aus Favoriten entfernen' : 'Zu Favoriten hinzufügen')
	})
}

function cloneCardForFavorites(uid) {
	const original = document.querySelector(`.pool-card[data-uid="${uid}"]`)
	if (!original) return null
	const clone = original.cloneNode(true)
	clone.querySelector('.star-btn').addEventListener('click', e => {
		e.preventDefault()
		e.stopPropagation()
		toggleFavorite(uid)
	})
	return clone
}

function renderSections() {
	applyStarState()
	const favs = getFavorites()
	const favSection = document.getElementById('favorites-section')
	const favGrid = document.getElementById('favorites-grid')
	favGrid.innerHTML = ''
	if (!favs.length) {
		favSection.style.display = 'none'
		return
	}
	favSection.style.display = ''
	for (const uid of favs) {
		const card = cloneCardForFavorites(uid)
		if (card) favGrid.appendChild(card)
	}
}

document.querySelectorAll('.pool-card .star-btn').forEach(btn => {
	btn.addEventListener('click', e => {
		e.preventDefault()
		e.stopPropagation()
		toggleFavorite(btn.dataset.uid)
	})
})

document.addEventListener('click', e => {
	if (e.target.closest('.star-btn')) return
	const card = e.target.closest('.pool-card')
	if (!card) return
	rememberReturnCard(card)
	track('pool-card-click', { pool_uid: card.dataset.uid, pool_type: card.dataset.type, city: card.dataset.city })
})

renderSections()

async function fetchOccupancy() {
	try {
		const res = await fetch('/api/current')
		if (!res.ok) {
			markOccupancySettled()
			return
		}
		const data = await res.json()
		const map = {}
		for (const item of data) map[item.pool_uid] = item

		document.querySelectorAll('.pool-card').forEach(card => {
			const uid = card.dataset.uid
			const item = map[uid]
			const label = card.querySelector('.occupancy-label')
			const guestCount = card.querySelector('.guest-count')
			const bar = card.querySelector('.occupancy-bar')
			const badge = card.querySelector('.status-badge')

			if (item && item.occupancy_pct !== null) {
				const pct = Math.round(item.occupancy_pct)
				label.textContent = pct + '%'
				if (guestCount) {
					// Slash denom must match occupancy_pct basis (CrowdMonitor max_space).
					const maxSpace = item.max_space > 0 ? item.max_space : null
					const cap = maxSpace != null ? ` / ${maxSpace}` : ''
					guestCount.textContent = item.current_fill != null ? `${item.current_fill}${cap} Gäste` : ''
				}
				bar.style.width = Math.min(pct, 100) + '%'
				bar.className = 'occupancy-bar ' + (pct <= 50 ? 'green' : pct <= 80 ? 'yellow' : 'red')
			} else if (item && item.is_open === false) {
				// Closed with no fill (e.g. Revision) — status badge carries the message
				label.textContent = '—'
				if (guestCount) guestCount.textContent = ''
				bar.style.width = '0%'
				bar.className = 'occupancy-bar grey'
			} else {
				label.textContent = 'Keine Daten'
				if (guestCount) guestCount.textContent = ''
				bar.style.width = '0%'
				bar.className = 'occupancy-bar grey'
			}

			if (badge && item) {
				badge.innerHTML = renderStatusBadge(item)
			}

			// Mute occupancy when closed — status matters more than a stale %
			if (item && !item.is_open) {
				label.classList.add('occupancy-muted')
				bar.classList.add('grey')
			} else {
				label.classList.remove('occupancy-muted')
			}

			// Set data-open for filter logic
			if (item) card.dataset.open = item.is_open ? 'true' : 'false'
		})

		markOccupancySettled()
	} catch (e) {
		console.warn('Auslastung konnte nicht geladen werden', e)
		document.querySelectorAll('.pool-card .status-badge').forEach(badge => {
			badge.innerHTML =
				'<span class="status-dot closed"></span>' +
				'<span class="status-text">Live-Daten nicht verfügbar</span>'
		})
		markOccupancySettled()
	}
}

function escapeHtml(value) {
	return String(value)
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;')
}

function renderStatusBadge(item) {
	const dotClass = item.is_open ? 'status-dot open' : 'status-dot closed'

	if (item.is_open) {
		return `<span class="${dotClass}" aria-hidden="true"></span>` +
			`<span class="status-text">Offen</span>`
	}

	let text = 'Geschlossen'
	if (item.state === 'observed_closed') {
		text = 'Aktuell geschlossen'
	} else if (item.state === 'closed_exception' && item.reason) {
		text = `Geschlossen · ${escapeHtml(item.reason)}`
	} else if (item.opens_seasonal) {
		text = `Geschlossen · ${escapeHtml(item.opens_seasonal)}`
	} else if (item.next_open) {
		// "09:00" or "So. 09:00" → always include "um"
		const t = escapeHtml(item.next_open)
		text = t.includes(' ')
			? `Geschlossen · Öffnet ${t.replace(' ', ' um ')}`
			: `Geschlossen · Öffnet um ${t}`
	} else if (item.reason) {
		text = `Geschlossen · ${escapeHtml(item.reason)}`
	}

	return `<span class="${dotClass}" aria-hidden="true"></span>` +
		`<span class="status-text">${text}</span>`
}

fetchOccupancy()
setInterval(fetchOccupancy, 60000)

// Build filter bar, then restore from URL or storage
buildFilterBar()
const restoreSource = restoreFilters()
applyFilters()
returnCardTarget = consumeReturnCard()
if (restoreSource === 'storage') {
	const fireRestored = () =>
		track('filter-restored', {
			source: 'storage',
			open: filters.open,
			types: [...filters.types].sort().join(','),
			city: filters.city,
		})
	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', fireRestored)
	} else {
		fireRestored()
	}
}
