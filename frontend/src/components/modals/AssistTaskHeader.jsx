import { useState } from 'react'
import descriptionToHtml from '../../lib/descriptionToHtml'
import './AssistModal.css'

// Task chip + "what the assistant is reading" sources chip -- shared by AssistModal's
// Chat/Break down tabs and CardDetailPanel's Code mode, since both read the same card
// content and GitHub context under the same precedence rules (Task Notes outrank the
// GitHub issue; a newer comment supersedes the description/an earlier comment). Extracted
// so promoting Code to a top-level mode peer of Chat/Break down didn't mean copy-pasting
// this block into CardDetailPanel.jsx too.
export default function AssistTaskHeader({ task, githubContext = null }) {
  const [showDesc, setShowDesc] = useState(false)
  const [showSources, setShowSources] = useState(false)

  let sourcesLabel = null
  if (githubContext) {
    const kind = githubContext.item_type === 'pr' ? 'PR' : 'Issue'
    const commentCount = githubContext.comments?.length ?? 0
    const commentPart = commentCount > 0 ? ` (${commentCount} comment${commentCount === 1 ? '' : 's'})` : ''
    sourcesLabel = `Using ${task.description ? 'Task Notes + ' : ''}${kind} ${githubContext.repo}#${githubContext.number}${commentPart}`
  }

  return (
    <>
      {/* Task chip */}
      <div className="assist-task">
        <span className="assist-task-label">Task</span>
        <div className="assist-task-body">
          <div className="assist-task-title-row">
            <span className="assist-task-name">{task.title}</span>
            {task.description && (
              <button className="assist-task-desc-toggle" onClick={() => setShowDesc(v => !v)}>
                {showDesc ? 'Hide' : 'Details'}
              </button>
            )}
          </div>
          {task.description && showDesc && (
            <span className="assist-task-desc" dangerouslySetInnerHTML={{ __html: descriptionToHtml(task.description) }} />
          )}
        </div>
      </div>

      {/* Context sources -- surfaces what the assistant is actually reading. GitHub
          issue/comment context is fed into every prompt here but was otherwise
          invisible once outside the card's own view mode. */}
      {sourcesLabel && (
        <div className="assist-sources">
          <button className="assist-sources-toggle" onClick={() => setShowSources(v => !v)}>
            ⓘ {sourcesLabel}
          </button>
          {showSources && (
            <div className="assist-sources-detail">
              If these disagree: Task Notes (written directly on this card) take priority
              over the GitHub {githubContext.item_type === 'pr' ? 'PR' : 'issue'} description.
              Within GitHub, a newer comment supersedes the original description or an
              earlier comment on the same point.
            </div>
          )}
        </div>
      )}
    </>
  )
}
