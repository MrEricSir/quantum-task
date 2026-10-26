// "Break down" tab content for AssistModal. All state/handlers come from
// useAssistBreakdown -- this component is presentational only.
export default function AssistBreakdownTab({ breakdown }) {
  const { bdStatus, bdSubtasks, setBdSubtasks, bdTagName, bdError, generateBreakdown, confirmBreakdown } = breakdown

  const validBdCount = bdSubtasks.filter(s => s.trim()).length

  return (
    <>
      {/* Header row mirrors Code tab's Brief header (label + action button in the same
          spot every time) -- same reason the button itself reuses cdp-spec-gen-btn:
          a direct child of .assist-inline's flex column stretches to full width with
          no wrapper, which is why this lives in a row instead of sitting on its own. */}
      <div className="cdp-spec-header">
        <div className="cdp-section-label">Subtasks</div>
        <div className="cdp-spec-actions">
          {(bdStatus === 'idle' || bdStatus === 'error') && (
            <button className="cdp-gh-btn cdp-spec-gen-btn" onClick={generateBreakdown}>
              ✦ {bdStatus === 'error' ? 'Try again' : 'Generate'}
            </button>
          )}
        </div>
      </div>
      {bdStatus === 'idle' && (
        <p className="assist-bd-intro">
          Splits this card into subtasks you can edit before creating them.
        </p>
      )}
      {bdStatus === 'loading' && (
        <div className="assist-bd-loading">
          <span className="assist-spinner assist-spinner--dark" /> Generating subtasks…
        </div>
      )}
      {bdStatus === 'error' && <div className="assist-bd-error">{bdError}</div>}
      {(bdStatus === 'ready' || bdStatus === 'saving') && (
        <>
          <p className="assist-bd-intro">
            Original card will be archived and tagged <strong>{bdTagName}</strong>. Edit or remove subtasks before creating:
          </p>
          <div className="assist-bd-list">
            {bdSubtasks.map((s, i) => (
              <div key={i} className="assist-bd-item">
                <span className="assist-bd-num">{i + 1}</span>
                <input
                  className="assist-bd-input"
                  value={s}
                  onChange={e => setBdSubtasks(prev => prev.map((x, idx) => idx === i ? e.target.value : x))}
                />
                <button type="button" className="assist-bd-remove"
                  onClick={() => setBdSubtasks(prev => prev.filter((_, idx) => idx !== i))}
                  aria-label="Remove"
                >✕</button>
              </div>
            ))}
          </div>
          {bdError && <p className="assist-bd-error">{bdError}</p>}
          <button className="assist-run" onClick={confirmBreakdown}
            disabled={bdStatus === 'saving' || validBdCount === 0}
          >
            {bdStatus === 'saving'
              ? <><span className="assist-spinner" /> Creating…</>
              : `Create ${validBdCount} subtask${validBdCount !== 1 ? 's' : ''}`}
          </button>
        </>
      )}
    </>
  )
}
