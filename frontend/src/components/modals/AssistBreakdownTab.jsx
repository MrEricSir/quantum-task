// "Break down" tab content for AssistModal. All state/handlers come from
// useAssistBreakdown -- this component is presentational only.
export default function AssistBreakdownTab({ breakdown }) {
  const { bdStatus, bdSubtasks, setBdSubtasks, bdTagName, bdError, confirmBreakdown } = breakdown

  const validBdCount = bdSubtasks.filter(s => s.trim()).length

  return (
    <>
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
