import { CopyIcon, CheckIcon } from '@radix-ui/react-icons'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text, { breaks: true }), { ADD_ATTR: ['target', 'rel'] })
}

// "Code" tab content for AssistModal (spec generation + bridge job + cross-repo
// companion job). All state/handlers come from useAssistCode -- this component
// is presentational only.
export default function AssistCodeTab({ code }) {
  const {
    specText, specGenerating, specEditing, specDraft, setSpecDraft, specError, copiedSpec,
    bridgeJob, bridgeQueuing, bridgeError, copiedWorktree, resumeQueuing,
    branchOverride, setBranchOverride, defaultBranch, branchFieldDisabled,
    companionJob, companionOpen, companionRepo, setCompanionRepo, companionQueuing,
    companionError, knownRepos, companionResumeQueuing,
    handleGenerateSpec, handleSaveSpec, handleStartSpecEdit, handleCancelSpecEdit, handleCopySpec,
    handleCopyWorktreePath, handleSendToBridge, handleResumeJob,
    handleResumeCompanionJob, handleOpenCompanion, handleCancelCompanion, handleQueueCompanion,
  } = code

  return (
    <div className="assist-spec-tab">
      <div className="cdp-spec-header">
        <div className="cdp-section-label">Brief</div>
        <div className="cdp-spec-actions">
          {specText && !specEditing && (
            <button className="cdp-gh-btn" onClick={handleCopySpec} title="Copy brief">
              {copiedSpec ? '✓ Copied' : '⎘ Copy'}
            </button>
          )}
          <button
            className="cdp-gh-btn cdp-spec-gen-btn"
            onClick={handleGenerateSpec}
            disabled={specGenerating}
            title={specText ? 'Regenerate brief' : 'Generate brief from card context'}
          >
            {specGenerating ? 'Generating…' : specText ? '↻ Regen' : '✦ Generate'}
          </button>
          {specText && !specEditing && (
            <button
              className="cdp-gh-btn cdp-spec-bridge-btn"
              onClick={handleSendToBridge}
              disabled={
                bridgeQueuing ||
                bridgeJob?.status === 'running' ||
                bridgeJob?.status === 'pending' ||
                bridgeJob?.spec_snapshot === specText
              }
              title={
                bridgeJob?.spec_snapshot === specText
                  ? 'Already submitted — edit the brief to queue a new job'
                  : 'Send to local Claude Code agent'
              }
            >
              {bridgeQueuing ? 'Queuing…' : '▶ Run'}
            </button>
          )}
        </div>
      </div>

      {specText && !specEditing && (
        <div className="cdp-branch-row">
          <label className="cdp-branch-label" htmlFor="cdp-branch-input">Branch</label>
          <input
            id="cdp-branch-input"
            type="text"
            className="cdp-branch-input"
            value={branchOverride}
            onChange={e => setBranchOverride(e.target.value)}
            placeholder={defaultBranch}
            disabled={branchFieldDisabled}
            title="Leave blank to use the auto-generated name shown as a placeholder"
          />
          <button
            type="button"
            className="cdp-branch-use-default"
            onClick={() => setBranchOverride(defaultBranch)}
            disabled={branchFieldDisabled || branchOverride === defaultBranch}
            title="Copy the auto-generated name in so you can tweak it, instead of typing it from scratch"
          >
            Use this
          </button>
        </div>
      )}

      {specError && <div className="cdp-spec-error">{specError}</div>}
      {bridgeError && <div className="cdp-spec-error">{bridgeError}</div>}

      {bridgeJob && (
        <div className={`cdp-bridge-status cdp-bridge-status--${bridgeJob.status}`}>
          <span className="cdp-bridge-dot" />
          <div className="cdp-bridge-status-body">
            <span className="cdp-bridge-label">
              {bridgeJob.status === 'pending'  && (
                bridgeJob.fix_comment_ids?.length > 0 ? 'Queued — waiting for agent to apply fixes…'
                : bridgeJob.resumes_job_id ? 'Queued — waiting for agent to resume…'
                : 'Queued — waiting for agent…'
              )}
              {bridgeJob.status === 'running'  && (
                bridgeJob.fix_comment_ids?.length > 0 ? 'Applying fixes…'
                : bridgeJob.resumes_job_id ? 'Resuming previous session…'
                : 'Claude Code running…'
              )}
              {bridgeJob.status === 'done'     && (bridgeJob.result || 'Complete')}
              {bridgeJob.status === 'error'    && `Error: ${bridgeJob.result}`}
              {bridgeJob.status === 'stalled'  && 'Agent went quiet — may have crashed or lost network'}
              {bridgeJob.status === 'blocked'  && 'Waiting on another job to finish…'}
            </span>
            {bridgeJob.branch_name && (
              <span className="cdp-bridge-branch">
                {bridgeJob.branch_name}
                {bridgeJob.agent_name && <span className="cdp-bridge-agent"> · {bridgeJob.agent_name}</span>}
              </span>
            )}
            {bridgeJob.worktree_path && (
              <div className="cdp-bridge-worktree">
                <span className="cdp-bridge-worktree-path" title={bridgeJob.worktree_path}>
                  {bridgeJob.worktree_path}
                </span>
                <button
                  type="button"
                  className="cdp-bridge-worktree-copy"
                  onClick={handleCopyWorktreePath}
                  title="Copy path"
                >
                  {copiedWorktree ? <CheckIcon /> : <CopyIcon />}
                </button>
              </div>
            )}
            {(bridgeJob.status === 'error' || bridgeJob.status === 'stalled') && bridgeJob.worktree_path && (
              <button
                type="button"
                className="cdp-gh-btn cdp-bridge-resume-btn"
                onClick={handleResumeJob}
                disabled={resumeQueuing}
                title="Resume in the same worktree, picking up where the session left off"
              >
                {resumeQueuing ? 'Queuing…' : '↻ Resume'}
              </button>
            )}
          </div>
        </div>
      )}

      {bridgeJob?.output && (
        <div
          className="cdp-gh-markdown cdp-bridge-md-output"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(bridgeJob.output) }}
        />
      )}

      {bridgeJob && (
        <div className="cdp-companion-section">
          {companionJob ? (
            <div className={`cdp-bridge-status cdp-bridge-status--${companionJob.status}`}>
              <span className="cdp-bridge-dot" />
              <div className="cdp-bridge-status-body">
                <span className="cdp-bridge-companion-repo">{companionJob.target_repo}</span>
                <span className="cdp-bridge-label">
                  {companionJob.status === 'blocked' && (
                    bridgeJob.status === 'error' || bridgeJob.status === 'stalled'
                      ? `Blocked — ${bridgeJob.target_repo || 'the other job'} failed and needs to be fixed or resumed first`
                      : `Waiting on ${bridgeJob.target_repo || 'the other job'} to finish…`
                  )}
                  {companionJob.status === 'pending'  && 'Queued — waiting for agent…'}
                  {companionJob.status === 'running'  && 'Claude Code running…'}
                  {companionJob.status === 'done'     && (companionJob.result || 'Complete')}
                  {companionJob.status === 'error'    && `Error: ${companionJob.result}`}
                  {companionJob.status === 'stalled'  && 'Agent went quiet — may have crashed or lost network'}
                </span>
                {companionJob.branch_name && (
                  <span className="cdp-bridge-branch">
                    {companionJob.branch_name}
                    {companionJob.agent_name && <span className="cdp-bridge-agent"> · {companionJob.agent_name}</span>}
                  </span>
                )}
                {companionJob.worktree_path && (
                  <div className="cdp-bridge-worktree">
                    <span className="cdp-bridge-worktree-path" title={companionJob.worktree_path}>
                      {companionJob.worktree_path}
                    </span>
                  </div>
                )}
                {(companionJob.status === 'error' || companionJob.status === 'stalled') && companionJob.worktree_path && (
                  <button
                    type="button"
                    className="cdp-gh-btn cdp-bridge-resume-btn"
                    onClick={handleResumeCompanionJob}
                    disabled={companionResumeQueuing}
                    title="Resume in the same worktree, picking up where the session left off"
                  >
                    {companionResumeQueuing ? 'Queuing…' : '↻ Resume'}
                  </button>
                )}
              </div>
            </div>
          ) : companionOpen ? (
            <div className="cdp-companion-add cdp-companion-add--open">
              <input
                type="text"
                className="cdp-companion-repo-input"
                list="cdp-known-repos-list"
                value={companionRepo}
                onChange={e => setCompanionRepo(e.target.value)}
                placeholder="owner/repo"
                autoFocus
              />
              <datalist id="cdp-known-repos-list">
                {knownRepos.map(r => <option key={r} value={r} />)}
              </datalist>
              <button
                type="button"
                className="cdp-gh-btn"
                onClick={handleQueueCompanion}
                disabled={companionQueuing || !companionRepo.trim()}
              >
                {companionQueuing ? 'Queuing…' : 'Queue'}
              </button>
              <button
                type="button"
                className="cdp-btn cdp-btn--cancel"
                onClick={handleCancelCompanion}
              >
                Cancel
              </button>
            </div>
          ) : bridgeJob.status === 'error' || bridgeJob.status === 'stalled' ? (
            <div className="cdp-companion-note">
              Fix or resume this job before adding a companion in another repo.
            </div>
          ) : (
            <button type="button" className="cdp-companion-add-btn" onClick={handleOpenCompanion}>
              + Companion job in another repo
            </button>
          )}
          {companionError && <div className="cdp-spec-error">{companionError}</div>}
        </div>
      )}

      {companionJob?.output && (
        <div
          className="cdp-gh-markdown cdp-bridge-md-output"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(companionJob.output) }}
        />
      )}

      {bridgeJob?.status === 'done' && companionJob?.status === 'done' && (
        <div className="cdp-companion-note">
          Both built separately — not verified to work together. Check them out and run
          them together before merging.
        </div>
      )}

      <div className="assist-spec-content">
        {specGenerating ? (
          <div className="assist-spec-loading">
            <span className="assist-spinner" />
            {specText ? 'Regenerating…' : 'Generating…'}
          </div>
        ) : specEditing ? (
          <textarea
            className="cdp-spec-textarea"
            value={specDraft}
            onChange={e => setSpecDraft(e.target.value)}
            rows={20}
          />
        ) : specText ? (
          <div
            className="cdp-spec-markdown"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(specText) }}
          />
        ) : (
          <div className="cdp-spec-empty">
            No brief yet — click <strong>✦ Generate</strong> to synthesize from the card context.
          </div>
        )}
      </div>

      {specEditing ? (
        <div className="assist-spec-footer">
          <button className="cdp-btn cdp-btn--cancel" onClick={handleCancelSpecEdit}>
            Cancel
          </button>
          <button className="cdp-btn cdp-btn--save" onClick={handleSaveSpec}>Save</button>
        </div>
      ) : specText ? (
        <div className="assist-spec-footer">
          <button
            className="cdp-btn cdp-btn--secondary"
            onClick={handleStartSpecEdit}
            disabled={specGenerating}
          >
            Edit
          </button>
        </div>
      ) : null}
    </div>
  )
}
