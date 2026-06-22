import { useState, useEffect, useRef } from 'react'
import { fetchJobs, createJob, updateJob, deleteJob } from '../../api'
import './WorkshopPage.css'

function sourcesToParts(inputSources) {
  const cards = (inputSources ?? [])
    .filter(s => s.type === 'card')
    .map(s => ({ card_id: s.card_id, card_title: s.card_title }))
  const tagSources = (inputSources ?? [])
    .filter(s => s.type === 'tag')
    .map(s => ({ tag_id: s.tag_id, tag_name: s.tag_name, tag_color: s.tag_color }))
  const text = (inputSources ?? []).find(s => s.type === 'text')?.content ?? ''
  const searches = (inputSources ?? [])
    .filter(s => s.type === 'search')
    .map(s => ({ query: s.query, results: s.results || [] }))
  const urls = (inputSources ?? [])
    .filter(s => s.type === 'url')
    .map(s => ({ url: s.url, title: s.title, content: s.content }))
  return { cards, tagSources, text, searches, urls }
}

function buildSources(cards, tagSources, text, searchSources = [], urlSources = []) {
  return [
    ...tagSources.map(t => ({ type: 'tag', tag_id: t.tag_id, tag_name: t.tag_name, tag_color: t.tag_color })),
    ...cards.map(c => ({ type: 'card', card_id: c.card_id, card_title: c.card_title })),
    ...(text.trim() ? [{ type: 'text', content: text.trim() }] : []),
    ...searchSources.map(s => ({ type: 'search', query: s.query, results: s.results })),
    ...urlSources.map(s => ({ type: 'url', url: s.url, title: s.title, content: s.content })),
  ]
}

export default function WorkshopPage({ todos, tags, onAddCard }) {
  const [jobs, setJobs] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [title, setTitle] = useState('')
  const [prompt, setPrompt] = useState('')
  const [cardSources, setCardSources] = useState([])
  const [tagSources, setTagSources] = useState([])
  const [textContent, setTextContent] = useState('')
  const [searchSources, setSearchSources] = useState([])
  const [urlSources, setUrlSources] = useState([])
  const [output, setOutput] = useState('')
  const [status, setStatus] = useState('idle')
  const [copied, setCopied] = useState(false)
  const [savedAsCard, setSavedAsCard] = useState(false)
  const [cardSearch, setCardSearch] = useState('')
  const [showPicker, setShowPicker] = useState(false)   // 'card' | 'tag' | 'search' | 'url' | false

  // Search picker state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchPickerResults, setSearchPickerResults] = useState([])
  const [searchPickerStatus, setSearchPickerStatus] = useState('idle') // idle|loading|done|error

  // URL fetch picker state
  const [urlInput, setUrlInput] = useState('')
  const [urlFetchStatus, setUrlFetchStatus] = useState('idle') // idle|loading|error

  const abortRef = useRef(null)
  const outputRef = useRef(null)
  const pickerRef = useRef(null)

  useEffect(() => {
    fetchJobs().then(setJobs).catch(() => {})
  }, [])

  // Sync local draft when selection changes
  useEffect(() => {
    if (selectedId === null) return
    const job = jobs.find(j => j.id === selectedId)
    if (!job) return
    setTitle(job.title || '')
    setPrompt(job.prompt || '')
    const { cards, tagSources: tags_, text, searches, urls } = sourcesToParts(job.input_sources)
    setCardSources(cards)
    setTagSources(tags_)
    setTextContent(text)
    setSearchSources(searches)
    setUrlSources(urls)
    setOutput(job.last_output || '')
    setStatus(job.last_output ? 'done' : 'idle')
    setCopied(false)
    setSavedAsCard(!!job.output_card_id)
  }, [selectedId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }, [output])

  // Close picker on outside click
  useEffect(() => {
    if (!showPicker) return
    const handler = (e) => {
      if (!pickerRef.current?.contains(e.target)) setShowPicker(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showPicker])

  const saveJobState = async (overrides = {}) => {
    if (!selectedId) return
    const updated = await updateJob(selectedId, {
      title: title || null,
      prompt,
      input_sources: buildSources(cardSources, tagSources, textContent, searchSources, urlSources),
      ...overrides,
    })
    setJobs(prev => prev.map(j => j.id === selectedId ? updated : j))
    return updated
  }

  const handleNewJob = async () => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    const job = await createJob({ prompt: '', input_sources: [] })
    setJobs(prev => [job, ...prev])
    setSelectedId(job.id)
    setOutput('')
    setStatus('idle')
    setSearchSources([])
    setUrlSources([])
    setSearchQuery('')
    setUrlInput('')
  }

  const handleSelectJob = (id) => {
    if (id === selectedId) return
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    setOutput('')
    setStatus('idle')
    setSelectedId(id)
  }

  const handleDeleteJob = async () => {
    if (!selectedId) return
    await deleteJob(selectedId)
    const remaining = jobs.filter(j => j.id !== selectedId)
    setJobs(remaining)
    setOutput('')
    setStatus('idle')
    setSelectedId(remaining[0]?.id ?? null)
  }

  const handleAddCardToJob = (todo) => {
    if (cardSources.some(s => s.card_id === todo.id)) return
    const newCards = [...cardSources, { card_id: todo.id, card_title: todo.title }]
    setCardSources(newCards)
    setCardSearch('')
    setShowPicker(false)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(newCards, tagSources, textContent, searchSources, urlSources) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleRemoveCard = (cardId) => {
    const newCards = cardSources.filter(s => s.card_id !== cardId)
    setCardSources(newCards)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(newCards, tagSources, textContent, searchSources, urlSources) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleAddTagToJob = (tag) => {
    if (tagSources.some(s => s.tag_id === tag.id)) return
    const newTags = [...tagSources, { tag_id: tag.id, tag_name: tag.name, tag_color: tag.color }]
    setTagSources(newTags)
    setShowPicker(false)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(cardSources, newTags, textContent, searchSources, urlSources) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleRemoveTag = (tagId) => {
    const newTags = tagSources.filter(s => s.tag_id !== tagId)
    setTagSources(newTags)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(cardSources, newTags, textContent, searchSources, urlSources) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleWebSearch = async () => {
    if (!searchQuery.trim() || searchPickerStatus === 'loading') return
    setSearchPickerStatus('loading')
    setSearchPickerResults([])
    try {
      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: searchQuery.trim() }),
      })
      if (!res.ok) throw new Error()
      const data = await res.json()
      setSearchPickerResults(data.results || [])
      setSearchPickerStatus('done')
    } catch {
      setSearchPickerStatus('error')
    }
  }

  const handleAddSearchResults = () => {
    if (!searchPickerResults.length) return
    const newSearch = { query: searchQuery.trim(), results: searchPickerResults }
    const newSearches = [...searchSources, newSearch]
    setSearchSources(newSearches)
    setSearchQuery('')
    setSearchPickerResults([])
    setSearchPickerStatus('idle')
    setShowPicker(false)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(cardSources, tagSources, textContent, newSearches, urlSources) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleRemoveSearch = (idx) => {
    const newSearches = searchSources.filter((_, i) => i !== idx)
    setSearchSources(newSearches)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(cardSources, tagSources, textContent, newSearches, urlSources) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleFetchUrl = async () => {
    if (!urlInput.trim() || urlFetchStatus === 'loading') return
    setUrlFetchStatus('loading')
    try {
      const res = await fetch('/api/fetch-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: urlInput.trim() }),
      })
      if (!res.ok) throw new Error()
      const data = await res.json()
      const newUrl = { url: urlInput.trim(), title: data.title, content: data.content }
      const newUrls = [...urlSources, newUrl]
      setUrlSources(newUrls)
      setUrlInput('')
      setUrlFetchStatus('idle')
      setShowPicker(false)
      if (selectedId) {
        updateJob(selectedId, { input_sources: buildSources(cardSources, tagSources, textContent, searchSources, newUrls) })
          .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
          .catch(() => {})
      }
    } catch {
      setUrlFetchStatus('error')
    }
  }

  const handleRemoveUrl = (idx) => {
    const newUrls = urlSources.filter((_, i) => i !== idx)
    setUrlSources(newUrls)
    if (selectedId) {
      updateJob(selectedId, { input_sources: buildSources(cardSources, tagSources, textContent, searchSources, newUrls) })
        .then(updated => setJobs(prev => prev.map(j => j.id === selectedId ? updated : j)))
        .catch(() => {})
    }
  }

  const handleRun = async () => {
    if (!prompt.trim() || !selectedId) return
    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    await saveJobState()
    setOutput('')
    setStatus('loading')
    setCopied(false)
    setSavedAsCard(false)

    try {
      const res = await fetch(`/api/jobs/${selectedId}/run`, {
        method: 'POST',
        signal: controller.signal,
      })
      if (!res.ok) throw new Error('Server error')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = '', acc = ''

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6).trim()
          if (data === '[DONE]') { setStatus('done'); break outer }
          try {
            const parsed = JSON.parse(data)
            if (parsed.error) { setStatus('error'); setOutput(parsed.error); return }
            if (parsed.text) { acc += parsed.text; setOutput(acc) }
          } catch {}
        }
      }
      setStatus('done')
      setJobs(prev => prev.map(j =>
        j.id === selectedId ? { ...j, last_output: acc, updated_at: new Date().toISOString() } : j
      ))
    } catch (err) {
      if (err.name !== 'AbortError') { setStatus('error'); setOutput('Could not generate output.') }
    }
  }

  const handleSaveAsCard = async () => {
    if (!output || !selectedId) return
    const card = await onAddCard({
      title: title || 'AI output',
      description: output,
      section: 'none',
    })
    if (card?.id) {
      const updated = await updateJob(selectedId, { output_card_id: card.id })
      setJobs(prev => prev.map(j => j.id === selectedId ? updated : j))
      setSavedAsCard(true)
    }
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(output).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const filteredTodos = todos
    .filter(t =>
      !t.archived && !t.completed &&
      !cardSources.some(s => s.card_id === t.id) &&
      (cardSearch === '' || t.title.toLowerCase().includes(cardSearch.toLowerCase()))
    )
    .slice(0, 8)

  const canRun = !!prompt.trim() && (
    tagSources.length > 0 || cardSources.length > 0 || !!textContent.trim() ||
    searchSources.length > 0 || urlSources.length > 0
  )
  const selectedJob = jobs.find(j => j.id === selectedId)

  return (
    <div className="workshop">
      {/* ── Left rail: job list ── */}
      <div className="workshop-sidebar">
        <button className="workshop-new-btn" onClick={handleNewJob}>+ New Job</button>
        <div className="workshop-job-list">
          {jobs.length === 0 && (
            <div className="workshop-sidebar-empty">No jobs yet.</div>
          )}
          {jobs.map(job => (
            <button
              key={job.id}
              className={`workshop-job-item${job.id === selectedId ? ' workshop-job-item--active' : ''}`}
              onClick={() => handleSelectJob(job.id)}
            >
              <span className="workshop-job-title">{job.title || 'Untitled'}</span>
              <span className="workshop-job-date">
                {new Date(job.updated_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* ── Right panel: compose / run ── */}
      {selectedJob ? (
        <div className="workshop-main">
          <input
            className="workshop-title-input"
            placeholder="Job title (optional)"
            value={title}
            onChange={e => setTitle(e.target.value)}
            onBlur={() => saveJobState()}
          />

          {/* Inputs */}
          <div className="workshop-section">
            <div className="workshop-label">Inputs</div>
            <div className="workshop-chips-row">
              {tagSources.map(s => (
                <span key={s.tag_id} className="workshop-chip workshop-chip--tag" style={{ borderColor: s.tag_color, color: s.tag_color }}>
                  <span className="workshop-chip-dot" style={{ background: s.tag_color }} />
                  {s.tag_name}
                  <button
                    className="workshop-chip-remove"
                    onClick={() => handleRemoveTag(s.tag_id)}
                    aria-label={`Remove tag ${s.tag_name}`}
                  >×</button>
                </span>
              ))}
              {cardSources.map(s => (
                <span key={s.card_id} className="workshop-chip">
                  {s.card_title}
                  <button
                    className="workshop-chip-remove"
                    onClick={() => handleRemoveCard(s.card_id)}
                    aria-label={`Remove ${s.card_title}`}
                  >×</button>
                </span>
              ))}
              {searchSources.map((s, i) => (
                <span key={i} className="workshop-chip workshop-chip--search">
                  <span className="workshop-chip-icon">🔍</span>
                  {s.query}
                  <button
                    className="workshop-chip-remove"
                    onClick={() => handleRemoveSearch(i)}
                    aria-label={`Remove search "${s.query}"`}
                  >×</button>
                </span>
              ))}
              {urlSources.map((s, i) => (
                <span key={i} className="workshop-chip workshop-chip--url">
                  <span className="workshop-chip-icon">🔗</span>
                  {s.title || s.url}
                  <button
                    className="workshop-chip-remove"
                    onClick={() => handleRemoveUrl(i)}
                    aria-label={`Remove URL "${s.url}"`}
                  >×</button>
                </span>
              ))}
              <div className="workshop-picker-wrap" ref={pickerRef}>
                <button
                  className="workshop-add-card-btn"
                  onClick={() => setShowPicker(v => v === 'tag' ? false : 'tag')}
                >
                  + By tag
                </button>
                <button
                  className="workshop-add-card-btn"
                  onClick={() => { setCardSearch(''); setShowPicker(v => v === 'card' ? false : 'card') }}
                >
                  + Add card
                </button>
                <button
                  className="workshop-add-card-btn"
                  onClick={() => { setSearchQuery(''); setSearchPickerResults([]); setSearchPickerStatus('idle'); setShowPicker(v => v === 'search' ? false : 'search') }}
                >
                  + Search web
                </button>
                <button
                  className="workshop-add-card-btn"
                  onClick={() => { setUrlInput(''); setUrlFetchStatus('idle'); setShowPicker(v => v === 'url' ? false : 'url') }}
                >
                  + Fetch URL
                </button>
                {showPicker === 'tag' && (
                  <div className="workshop-picker">
                    <div className="workshop-picker-list">
                      {tags.filter(t => !tagSources.some(s => s.tag_id === t.id)).length === 0 ? (
                        <div className="workshop-picker-empty">All tags added</div>
                      ) : tags.filter(t => !tagSources.some(s => s.tag_id === t.id)).map(t => (
                        <button
                          key={t.id}
                          className="workshop-picker-item workshop-picker-item--tag"
                          onClick={() => handleAddTagToJob(t)}
                        >
                          <span className="workshop-picker-tag-dot" style={{ background: t.color }} />
                          {t.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {showPicker === 'card' && (
                  <div className="workshop-picker">
                    <input
                      className="workshop-picker-search"
                      placeholder="Search cards…"
                      value={cardSearch}
                      onChange={e => setCardSearch(e.target.value)}
                      autoFocus
                    />
                    <div className="workshop-picker-list">
                      {filteredTodos.length === 0 ? (
                        <div className="workshop-picker-empty">No cards found</div>
                      ) : filteredTodos.map(t => (
                        <button
                          key={t.id}
                          className="workshop-picker-item"
                          onClick={() => handleAddCardToJob(t)}
                        >
                          {t.title}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {showPicker === 'search' && (
                  <div className="workshop-picker workshop-picker--wide">
                    <div className="workshop-picker-input-row">
                      <input
                        className="workshop-picker-search workshop-picker-search--inline"
                        placeholder="Search the web…"
                        value={searchQuery}
                        onChange={e => { setSearchQuery(e.target.value); setSearchPickerStatus('idle') }}
                        onKeyDown={e => e.key === 'Enter' && handleWebSearch()}
                        autoFocus
                      />
                      <button
                        className="workshop-picker-action-btn"
                        onClick={handleWebSearch}
                        disabled={!searchQuery.trim() || searchPickerStatus === 'loading'}
                      >
                        {searchPickerStatus === 'loading' ? '…' : 'Search'}
                      </button>
                    </div>
                    {searchPickerStatus === 'error' && (
                      <div className="workshop-picker-empty">Search failed. Check TAVILY_API_KEY.</div>
                    )}
                    {searchPickerStatus === 'done' && searchPickerResults.length === 0 && (
                      <div className="workshop-picker-empty">No results found.</div>
                    )}
                    {searchPickerStatus === 'done' && searchPickerResults.length > 0 && (
                      <div className="workshop-picker-list">
                        <button className="workshop-picker-item workshop-picker-item--add-all" onClick={handleAddSearchResults}>
                          Add all {searchPickerResults.length} results
                        </button>
                        {searchPickerResults.map((r, i) => (
                          <div key={i} className="workshop-picker-search-result">
                            <div className="workshop-picker-search-result-title">{r.title}</div>
                            <div className="workshop-picker-search-result-url">{r.url}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                {showPicker === 'url' && (
                  <div className="workshop-picker workshop-picker--wide">
                    <div className="workshop-picker-input-row">
                      <input
                        className="workshop-picker-search workshop-picker-search--inline"
                        placeholder="https://…"
                        value={urlInput}
                        onChange={e => { setUrlInput(e.target.value); setUrlFetchStatus('idle') }}
                        onKeyDown={e => e.key === 'Enter' && handleFetchUrl()}
                        autoFocus
                      />
                      <button
                        className="workshop-picker-action-btn"
                        onClick={handleFetchUrl}
                        disabled={!urlInput.trim() || urlFetchStatus === 'loading'}
                      >
                        {urlFetchStatus === 'loading' ? '…' : 'Fetch'}
                      </button>
                    </div>
                    {urlFetchStatus === 'error' && (
                      <div className="workshop-picker-empty">Could not fetch URL.</div>
                    )}
                  </div>
                )}
              </div>
            </div>

            <textarea
              className="workshop-textarea"
              placeholder="Paste additional context — emails, messages, documents…"
              value={textContent}
              onChange={e => setTextContent(e.target.value)}
              onBlur={() => saveJobState()}
              rows={5}
            />
          </div>

          {/* Prompt */}
          <div className="workshop-section">
            <label className="workshop-label" htmlFor="ws-prompt">
              What should the assistant do?
            </label>
            <textarea
              id="ws-prompt"
              className="workshop-textarea workshop-textarea--prompt"
              placeholder="Draft a reply, extract action items, summarize this thread…"
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              onBlur={() => saveJobState()}
              rows={3}
            />
          </div>

          {/* Run */}
          <div className="workshop-run-row">
            <button
              className="workshop-run-btn"
              onClick={handleRun}
              disabled={!canRun || status === 'loading'}
            >
              {status === 'loading'
                ? <><span className="workshop-spinner" /> Generating…</>
                : '✦ Run'}
            </button>
            <button className="workshop-delete-btn" onClick={handleDeleteJob}>
              Delete job
            </button>
          </div>

          {/* Output */}
          {(output || status === 'loading') && (
            <div className="workshop-output-section">
              <div className="workshop-output-header">
                <span className="workshop-label">Output</span>
                {status === 'done' && (
                  <div className="workshop-output-btns">
                    <button className="workshop-btn-sm" onClick={handleCopy}>
                      {copied ? '✓ Copied' : 'Copy'}
                    </button>
                    <button
                      className="workshop-btn-sm"
                      onClick={handleSaveAsCard}
                      disabled={savedAsCard}
                    >
                      {savedAsCard ? '✓ Saved as card' : 'Save as card'}
                    </button>
                  </div>
                )}
              </div>
              <div
                ref={outputRef}
                className={`workshop-output${status === 'loading' ? ' workshop-output--streaming' : ''}`}
              >
                {output || <span className="workshop-output-placeholder">Thinking…</span>}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="workshop-empty-main">
          <div className="workshop-empty-spark">✦</div>
          <p>Select a job or create a new one to get started.</p>
        </div>
      )}
    </div>
  )
}
