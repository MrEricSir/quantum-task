import useLoadingStage from '../../hooks/useLoadingStage'

// Mounted for as long as the caller's own loading condition stays true, so the
// elapsed-time stages in useLoadingStage track how long THIS wait has run.
export default function LoadingIndicator({ label = 'Loading…' }) {
  const stageText = useLoadingStage(true)

  return (
    <div className="loading">
      <div className="loading-spinner" aria-hidden="true" />
      <p className="loading-label">{label}</p>
      {stageText && <p className="loading-stage">{stageText}</p>}
    </div>
  )
}
