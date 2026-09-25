import { useCallback, useEffect, useMemo, useState } from 'react'

const icons = {
  download: '<path d="M12 3v12m0 0 4-4m-4 4-4-4M5 19h14"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1"/>',
  film: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 3v18m8-18v18M4 8h4m8 0h4M4 16h4m8 0h4"/>',
  tv: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="m8 3 4 3 4-3"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2m3 0-1 15H6L5 6m5 4v7m4-7v7"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  alert: '<path d="M12 9v4m0 4h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
}

function Icon({ name, size = 20 }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      dangerouslySetInnerHTML={{ __html: icons[name] }}
    />
  )
}

const formatBytes = (bytes, speed = false) => {
  if (!bytes) return speed ? '0 B/s' : '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** index
  return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}${speed ? '/s' : ''}`
}

const formatEta = (seconds) => {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

async function api(path, options) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return null
  return response.json()
}

function StorageCard({ item }) {
  if (!item) return <div className="storage-card skeleton" aria-label="Loading storage status" />
  const used = Math.max(0, item.total_bytes - item.available_bytes)
  const percentage = item.total_bytes ? Math.round((used / item.total_bytes) * 100) : 0
  const ready = item.mounted && item.writable
  return (
    <article className={`storage-card ${ready ? '' : 'storage-error'}`}>
      <div className="icon-tile"><Icon name={item.destination === 'series' ? 'tv' : 'film'} size={24} /></div>
      <div className="storage-main">
        <div className="storage-title-row">
          <div>
            <h3>{item.label} storage</h3>
            <p>{item.path}</p>
          </div>
          <div className="storage-free">
            <strong>{ready ? `${formatBytes(item.available_bytes)} free` : 'Unavailable'}</strong>
            <span>{ready ? `of ${formatBytes(item.total_bytes)}` : item.error}</span>
          </div>
        </div>
        <div className="meter" aria-label={`${percentage}% used`}><span style={{ width: `${percentage}%` }} /></div>
        <span className="storage-used">{ready ? `${formatBytes(used)} used (${percentage}%)` : 'Downloads are blocked until storage is writable'}</span>
      </div>
    </article>
  )
}

function StatusBadge({ status }) {
  return <span className={`status status-${status}`}><i />{status[0].toUpperCase() + status.slice(1)}</span>
}

function TorrentRow({ torrent, onAction }) {
  const percent = Math.round(torrent.progress * 100)
  return (
    <tr>
      <td className="torrent-name" data-label="Name"><strong title={torrent.name}>{torrent.name}</strong></td>
      <td data-label="Destination"><span className="destination"><Icon name={torrent.destination === 'series' ? 'tv' : 'film'} size={17} />{torrent.destination_label}</span></td>
      <td data-label="Size">{formatBytes(torrent.size)}</td>
      <td data-label="Progress" className="progress-cell"><span>{percent}%</span><div className="progress"><i style={{ width: `${percent}%` }} /></div></td>
      <td data-label="Speed">{formatBytes(torrent.speed, true)}</td>
      <td data-label="ETA">{torrent.status === 'completed' ? 'Complete' : formatEta(torrent.eta)}</td>
      <td data-label="Status"><StatusBadge status={torrent.status} /></td>
      <td data-label="Actions" className="actions">
        {torrent.status === 'downloading' || torrent.status === 'pending' ? (
          <button className="icon-button" onClick={() => onAction(torrent, 'pause')} aria-label={`Pause ${torrent.name}`}><Icon name="pause" /></button>
        ) : torrent.status === 'paused' || torrent.status === 'error' ? (
          <button className="icon-button" onClick={() => onAction(torrent, 'resume')} aria-label={`Resume ${torrent.name}`}><Icon name="play" /></button>
        ) : <span className="action-spacer" />}
        <button className="icon-button danger" onClick={() => onAction(torrent, 'remove')} aria-label={`Remove ${torrent.name}`}><Icon name="trash" /></button>
      </td>
    </tr>
  )
}

function DeleteDialog({ torrent, onClose, onConfirm, busy }) {
  const [deleteFiles, setDeleteFiles] = useState(false)
  useEffect(() => {
    const handler = (event) => event.key === 'Escape' && !busy && onClose()
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [busy, onClose])
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title">
        <div className="dialog-icon"><Icon name="alert" size={24} /></div>
        <h2 id="delete-title">Remove torrent?</h2>
        <p>{torrent.name}</p>
        <label className="checkbox-row">
          <input type="checkbox" checked={deleteFiles} onChange={(event) => setDeleteFiles(event.target.checked)} />
          <span><strong>Also delete downloaded files</strong><small>This permanently removes data from the Windows share.</small></span>
        </label>
        <div className="dialog-actions">
          <button className="button secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="button destructive" onClick={() => onConfirm(deleteFiles)} disabled={busy}>{busy ? 'Removing…' : deleteFiles ? 'Delete files' : 'Remove torrent'}</button>
        </div>
      </section>
    </div>
  )
}

export default function App() {
  const [source, setSource] = useState('')
  const [mode, setMode] = useState('single')
  const [rangeEnabled, setRangeEnabled] = useState(false)
  const [startEpisode, setStartEpisode] = useState('')
  const [endEpisode, setEndEpisode] = useState('')
  const [includeEnabled, setIncludeEnabled] = useState(false)
  const [mustInclude, setMustInclude] = useState('')
  const [resolutions, setResolutions] = useState([])
  const [otherFilter, setOtherFilter] = useState('')
  const [destination, setDestination] = useState('series')
  const [health, setHealth] = useState(null)
  const [storage, setStorage] = useState([])
  const [torrents, setTorrents] = useState([])
  const [filter, setFilter] = useState('all')
  const [busy, setBusy] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [notice, setNotice] = useState(null)

  const showNotice = useCallback((message, type = 'error') => setNotice({ message, type }), [])
  const load = useCallback(async () => {
    const [healthResult, storageResult, torrentsResult] = await Promise.allSettled([
      api('/api/health'), api('/api/storage'), api('/api/torrents'),
    ])
    setHealth(healthResult.status === 'fulfilled' ? healthResult.value : { status: 'degraded' })
    if (storageResult.status === 'fulfilled') setStorage(storageResult.value.destinations)
    if (torrentsResult.status === 'fulfilled') setTorrents(torrentsResult.value.torrents)
  }, [])

  useEffect(() => {
    load()
    const interval = window.setInterval(load, 5000)
    return () => window.clearInterval(interval)
  }, [load])

  useEffect(() => {
    if (!notice) return undefined
    const timeout = window.setTimeout(() => setNotice(null), 5000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  const submit = async (event) => {
    event.preventDefault()
    if (!source.trim()) return showNotice(`Paste a ${mode === 'bulk' ? 'listing page URL' : 'webpage URL or magnet URI'}.`)
    if (mode === 'bulk' && rangeEnabled && (!startEpisode.trim() || !endEpisode.trim())) return showNotice('Enter both episode codes for the range.')
    if (mode === 'bulk' && includeEnabled && !mustInclude.trim()) return showNotice('Enter a name for Must Include.')
    setBusy(true)
    try {
      if (mode === 'bulk') {
        const result = await api('/api/downloads/bulk', {
          method: 'POST',
          body: JSON.stringify({
            source, destination,
            start: rangeEnabled ? startEpisode : null,
            end: rangeEnabled ? endEpisode : null,
            must_include: includeEnabled ? mustInclude : '',
            resolutions,
            other_filter: otherFilter,
          }),
        })
        const summary = `${result.added.length} added, ${result.skipped.length} skipped, ${result.failed.length} failed.`
        showNotice(`Bulk scan complete: ${summary}`, result.added.length || result.skipped.length ? 'success' : 'error')
        if (!result.failed.length) {
          setSource('')
        }
      } else {
        await api('/api/downloads', { method: 'POST', body: JSON.stringify({ source, destination }) })
        setSource('')
        showNotice('Download added to qBittorrent.', 'success')
      }
      await load()
    } catch (error) {
      showNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  const torrentAction = async (torrent, action) => {
    if (action === 'remove') return setDeleteTarget(torrent)
    try {
      await api(`/api/torrents/${torrent.hash}/${action}`, { method: 'POST', body: '{}' })
      await load()
    } catch (error) {
      showNotice(error.message)
    }
  }

  const removeTorrent = async (deleteFiles) => {
    setBusy(true)
    try {
      await api(`/api/torrents/${deleteTarget.hash}/remove`, {
        method: 'POST',
        body: JSON.stringify({ delete_files: deleteFiles, confirm_delete_files: deleteFiles }),
      })
      setDeleteTarget(null)
      showNotice(deleteFiles ? 'Torrent and downloaded files deleted.' : 'Torrent removed.', 'success')
      await load()
    } catch (error) {
      showNotice(error.message)
    } finally {
      setBusy(false)
    }
  }

  const counts = useMemo(() => ({
    all: torrents.length,
    downloading: torrents.filter((item) => item.status === 'downloading' || item.status === 'pending').length,
    paused: torrents.filter((item) => item.status === 'paused').length,
    completed: torrents.filter((item) => item.status === 'completed').length,
  }), [torrents])
  const visible = filter === 'all' ? torrents : torrents.filter((item) => filter === 'downloading' ? ['downloading', 'pending'].includes(item.status) : item.status === filter)
  const selectedStorage = storage.find((item) => item.destination === destination)
  const filtersReady = mode !== 'bulk' || ((!rangeEnabled || (startEpisode.trim() && endEpisode.trim())) && (!includeEnabled || mustInclude.trim()))
  const canSubmit = source.trim() && filtersReady && selectedStorage?.mounted && selectedStorage?.writable && !busy

  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <div className="brand-mark"><Icon name="download" size={32} /></div>
        <div className="brand"><h1>Auto<span>Tor</span></h1><p>Paste a link. Choose a destination. We’ll handle the rest.</p></div>
        <div className={`connection ${health?.status === 'ok' ? 'online' : 'offline'}`}>
          <i /><div><strong>{health?.status === 'ok' ? 'Connected' : 'Disconnected'}</strong><span>{health?.qbittorrent?.version ? `qBittorrent ${health.qbittorrent.version}` : 'qBittorrent unavailable'}</span></div>
        </div>
      </header>

      <main id="main">
        {notice && <div className={`toast ${notice.type}`} role="status"><Icon name={notice.type === 'success' ? 'check' : 'alert'} />{notice.message}<button onClick={() => setNotice(null)} aria-label="Dismiss notification"><Icon name="x" /></button></div>}

        <form className="panel add-panel" onSubmit={submit}>
          <div className="add-heading-row">
            <div className="section-heading"><div className="icon-tile accent"><Icon name="link" /></div><div><h2>{mode === 'bulk' ? 'Add downloads from a page' : 'Paste a URL or magnet link'}</h2><p>{mode === 'bulk' ? 'Scan a listing page and add its torrent links, skipping duplicates.' : 'Use a torrent webpage URL or a direct magnet URI.'}</p></div></div>
            <div className="mode-toggle" role="group" aria-label="Download mode">
              <button type="button" className={mode === 'single' ? 'active' : ''} aria-pressed={mode === 'single'} onClick={() => setMode('single')}>Single</button>
              <button type="button" className={mode === 'bulk' ? 'active' : ''} aria-pressed={mode === 'bulk'} onClick={() => setMode('bulk')}>Bulk</button>
            </div>
          </div>
          <label htmlFor="source" className="field-label">{mode === 'bulk' ? 'Listing page URL' : 'Webpage URL or magnet URI'}</label>
          <div className="input-wrap">
            <input id="source" value={source} onChange={(event) => setSource(event.target.value)} placeholder={mode === 'bulk' ? 'https://example.com/search-or-listing-page' : 'https://example.com/torrent-page or magnet:?xt=…'} autoComplete="off" required />
            {source && <button type="button" onClick={() => setSource('')} aria-label="Clear link"><Icon name="x" /></button>}
          </div>
          {mode === 'bulk' && (
            <fieldset className="bulk-options">
              <legend>Bulk filters</legend>
              <p className="bulk-hint">Selected filters apply together. With a range, only one torrent is added per matching episode.</p>
              <div className="bulk-filter-row">
                <label className="bulk-switch"><input type="checkbox" checked={rangeEnabled} onChange={(event) => setRangeEnabled(event.target.checked)} /><span>Range</span></label>
                {rangeEnabled && <div className="bulk-range">
                  <label htmlFor="start-episode">From<input id="start-episode" value={startEpisode} onChange={(event) => setStartEpisode(event.target.value)} placeholder="S01E01" autoComplete="off" required /></label>
                  <label htmlFor="end-episode">To<input id="end-episode" value={endEpisode} onChange={(event) => setEndEpisode(event.target.value)} placeholder="S01E05" autoComplete="off" required /></label>
                </div>}
              </div>
              <div className="bulk-filter-row">
                <label className="bulk-switch"><input type="checkbox" checked={includeEnabled} onChange={(event) => setIncludeEnabled(event.target.checked)} /><span>Must Include</span></label>
                {includeEnabled && <label className="bulk-text-label" htmlFor="must-include">Name or phrase<input id="must-include" value={mustInclude} onChange={(event) => setMustInclude(event.target.value)} placeholder="Lantern" autoComplete="off" required /></label>}
              </div>
              <div className="bulk-filter-row">
                <span className="bulk-filter-heading">Resolution</span>
                <div className="quality-options" role="group" aria-label="Resolution filters">
                  {['480p', '720p', '1080p', '2160p'].map((value) => <label key={value}><input type="checkbox" checked={resolutions.includes(value)} onChange={(event) => setResolutions((current) => event.target.checked ? [...current, value] : current.filter((item) => item !== value))} />{value === '2160p' ? '2160p / 4K' : value}</label>)}
                </div>
                <small>Choose one or more. Leave all clear for any resolution.</small>
              </div>
              <label className="bulk-text-label" htmlFor="other-filter">Other keyword (optional)<input id="other-filter" value={otherFilter} onChange={(event) => setOtherFilter(event.target.value)} placeholder="e.g. WEB-DL" autoComplete="off" /></label>
            </fieldset>
          )}
          <fieldset>
            <legend><span className="icon-tile"><Icon name="film" /></span><span><strong>Choose destination</strong><small>Select where qBittorrent should save the files.</small></span></legend>
            <div className="destination-grid">
              {['series', 'movies'].map((key) => (
                <label className={`destination-card ${destination === key ? 'selected' : ''}`} key={key}>
                  <input type="radio" name="destination" value={key} checked={destination === key} onChange={() => setDestination(key)} />
                  <span className="icon-tile"><Icon name={key === 'series' ? 'tv' : 'film'} size={26} /></span>
                  <span><strong>{key === 'series' ? 'Series' : 'Movies'}</strong><small>/downloads/{key}</small></span><i />
                </label>
              ))}
              <button className="button primary" type="submit" disabled={!canSubmit}><Icon name="download" />{busy ? (mode === 'bulk' ? 'Scanning…' : 'Adding…') : (mode === 'bulk' ? 'Start bulk download' : 'Start download')}</button>
            </div>
          </fieldset>
        </form>

        <section className="storage-grid" aria-label="Storage status">
          <StorageCard item={storage.find((item) => item.destination === 'series')} />
          <StorageCard item={storage.find((item) => item.destination === 'movies')} />
        </section>

        <section className="panel queue-panel">
          <div className="queue-header">
            <div className="section-heading"><div className="icon-tile accent"><Icon name="list" /></div><div><h2>Downloads</h2><p>Live queue from qBittorrent.</p></div></div>
            <div className="filters" aria-label="Filter downloads">
              {['all', 'downloading', 'paused', 'completed'].map((key) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}><span>{key[0].toUpperCase() + key.slice(1)}</span><i>{counts[key]}</i></button>)}
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Destination</th><th>Size</th><th>Progress</th><th>Speed</th><th>ETA</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>{visible.map((torrent) => <TorrentRow key={torrent.hash} torrent={torrent} onAction={torrentAction} />)}</tbody>
            </table>
            {!visible.length && <div className="empty-state"><Icon name="download" size={30} /><strong>{torrents.length ? 'No downloads match this filter' : 'Your queue is empty'}</strong><span>Paste a link above to add a download.</span></div>}
          </div>
        </section>
      </main>
      {deleteTarget && <DeleteDialog torrent={deleteTarget} busy={busy} onClose={() => setDeleteTarget(null)} onConfirm={removeTorrent} />}
    </>
  )
}
