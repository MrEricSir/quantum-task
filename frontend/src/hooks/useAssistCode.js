import { useEffect, useState } from 'react'
import {
  generateSpec, queueBridgeJob, getBridgeJob, getBridgeJobChain, queueResumeJob,
  queueCompanionJob, getKnownBridgeRepos, requestBranchRename, acknowledgeCheckpoint,
} from '../api'

// Mirrors bridge/scripts/agent_core.py's _slugify -- only used for the branch-name field's
// live preview of what the bridge would auto-generate; doesn't need to be byte-identical
// since it's a preview, not the value actually sent (an empty override still lets the
// bridge compute the real default itself, at pickup time, from the card's title then).
function slugifyPreview(text) {
  return (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}

// Shared by handleSendToBridge (queue-time override) and handleRenameBranch (mid-session
// rename request) -- same constraints either way, see bridge/jobs.py's validate_branch_name.
function _branchNameError(branchName) {
  if (branchName && /\s/.test(branchName)) return "Branch name can't contain whitespace"
  if (branchName.startsWith('-')) return "Branch name can't start with '-'"
  return null
}

// "Code" tab state for AssistModal (spec generation + bridge job + cross-repo
// companion job) -- extracted so AssistModal.jsx isn't one file owning chat,
// breakdown, and code/bridge state all at once.
export function useAssistCode(task, open, initialTab, onSpecSaved) {
  const [specText,       setSpecText]       = useState(null)
  const [specGenerating, setSpecGenerating] = useState(false)
  const [specEditing,    setSpecEditing]    = useState(false)
  const [specDraft,      setSpecDraft]      = useState('')
  const [specError,      setSpecError]      = useState('')
  const [copiedSpec,     setCopiedSpec]     = useState(false)
  const [bridgeJob,      setBridgeJob]      = useState(null)
  const [bridgeQueuing,  setBridgeQueuing]  = useState(false)
  const [bridgeError,    setBridgeError]    = useState('')
  const [copiedWorktree, setCopiedWorktree] = useState(false)
  const [resumeQueuing,  setResumeQueuing]  = useState(false)
  const [branchOverride, setBranchOverride] = useState('')
  const [renameQueuing,  setRenameQueuing]  = useState(false)
  const [attemptStats,   setAttemptStats]   = useState(null)
  const [acknowledging,  setAcknowledging]  = useState(false)

  // Cross-repo companion job (BRIDGE_CROSS_REPO_JOBS.md Phase 3)
  const [companionJob,     setCompanionJob]     = useState(null)
  const [companionOpen,    setCompanionOpen]    = useState(false)
  const [companionRepo,    setCompanionRepo]    = useState('')
  const [companionQueuing, setCompanionQueuing] = useState(false)
  const [companionError,   setCompanionError]   = useState('')
  const [knownRepos,       setKnownRepos]       = useState([])
  const [companionResumeQueuing, setCompanionResumeQueuing] = useState(false)

  // ── Load on open ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!open || !task?.id) return
    setSpecText(task.spec ?? null); setSpecEditing(false); setSpecError(''); setCopiedSpec(false)
    setBridgeJob(null); setBridgeError(''); setResumeQueuing(false); setBranchOverride('')
    setCompanionJob(null); setCompanionOpen(false); setCompanionRepo('')
    setCompanionQueuing(false); setCompanionError(''); setCompanionResumeQueuing(false)
    setAttemptStats(null)

    if (task.spec) {
      getBridgeJobChain(task.id)
        .then(({ root, companion, attempts }) => {
          setBridgeJob(root); setCompanionJob(companion); setAttemptStats(attempts)
          // Show the job's actual current name (rather than blank + placeholder) once
          // there is one, so editing it starts from reality, not a guess.
          if (root?.branch_name) setBranchOverride(root.branch_name)
        })
        .catch(() => {})
    }
  }, [open, task?.id, initialTab]) // eslint-disable-line react-hooks/exhaustive-deps

  // Refreshes attempt-history stats (see bridge/jobs.py's compute_attempt_stats) after
  // queueing a fresh run or a resume -- both add a new attempt, and the count/prior-failure
  // stats aren't part of either action's own response, only the chain endpoint's.
  const _refreshAttemptStats = () => {
    if (!task?.id) return
    getBridgeJobChain(task.id).then(({ attempts }) => setAttemptStats(attempts)).catch(() => {})
  }

  // Pick up spec generated in background while panel was open
  useEffect(() => {
    if (task?.spec && specText === null && !specGenerating) setSpecText(task.spec)
  }, [task?.spec]) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll bridge job while pending / running
  useEffect(() => {
    if (!bridgeJob || (bridgeJob.status !== 'pending' && bridgeJob.status !== 'running')) return
    const iv = setInterval(async () => {
      try {
        const updated = await getBridgeJob(bridgeJob.id)
        setBridgeJob(updated)
        if (updated.status !== 'pending' && updated.status !== 'running') clearInterval(iv)
      } catch { /* ignore */ }
    }, 5000)
    return () => clearInterval(iv)
  }, [bridgeJob?.id, bridgeJob?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll companion job while blocked / pending / running -- "blocked" included since it may
  // flip to "pending" server-side (the moment the root job completes) with no action here.
  useEffect(() => {
    if (!companionJob || !['blocked', 'pending', 'running'].includes(companionJob.status)) return
    const iv = setInterval(async () => {
      try {
        const updated = await getBridgeJob(companionJob.id)
        setCompanionJob(updated)
        if (!['blocked', 'pending', 'running'].includes(updated.status)) clearInterval(iv)
      } catch { /* ignore */ }
    }, 5000)
    return () => clearInterval(iv)
  }, [companionJob?.id, companionJob?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Spec ──────────────────────────────────────────────────────────────────

  const handleGenerateSpec = async () => {
    if (!task || specGenerating) return
    if (specEditing && specDraft !== specText) {
      if (!window.confirm('Your unsaved edits will be discarded and regenerated. Continue?')) return
      setSpecEditing(false)
    }
    setSpecGenerating(true); setSpecError('')
    try {
      const { spec } = await generateSpec(task.id)
      setSpecText(spec)
      onSpecSaved?.(spec)
    } catch {
      setSpecError('Failed to generate brief. Please try again.')
    } finally {
      setSpecGenerating(false)
    }
  }

  const handleSaveSpec = () => {
    onSpecSaved?.(specDraft)
    setSpecText(specDraft)
    setSpecEditing(false)
  }

  const handleStartSpecEdit = () => {
    setSpecDraft(specText); setSpecEditing(true)
  }

  const handleCancelSpecEdit = () => {
    if (specDraft !== specText && !window.confirm('Discard your unsaved changes?')) return
    setSpecEditing(false)
  }

  const handleCopySpec = () => {
    navigator.clipboard.writeText(specText).then(() => {
      setCopiedSpec(true)
      setTimeout(() => setCopiedSpec(false), 2000)
    })
  }

  // ── Bridge job ────────────────────────────────────────────────────────────

  const handleCopyWorktreePath = () => {
    navigator.clipboard.writeText(bridgeJob.worktree_path).then(() => {
      setCopiedWorktree(true)
      setTimeout(() => setCopiedWorktree(false), 2000)
    })
  }

  const handleSendToBridge = async () => {
    if (!task || bridgeQueuing) return
    const branchName = branchOverride.trim()
    const error = _branchNameError(branchName)
    if (error) { setBridgeError(error); return }
    setBridgeQueuing(true); setBridgeError('')
    try {
      const job = await queueBridgeJob(task.id, branchName || undefined)
      setBridgeJob(job)
      _refreshAttemptStats()
    } catch (e) {
      setBridgeError(e.message || 'Failed to queue bridge job')
    } finally {
      setBridgeQueuing(false)
    }
  }

  // Fires when the branch field loses focus while a job is pending/running (see
  // branchFieldDisabled -- it's only editable then, or before a job exists at all, in
  // which case this never fires since handleSendToBridge hasn't created bridgeJob yet).
  // No-ops quietly for an unchanged or empty value -- this is a passive "did it change"
  // check on blur, not an explicit submit action, so it shouldn't complain about a value
  // that's just the same name reformatted by losing and regaining focus.
  const handleRenameBranch = async () => {
    if (!bridgeJob || renameQueuing) return
    if (!['pending', 'running'].includes(bridgeJob.status)) return
    const branchName = branchOverride.trim()
    if (!branchName || branchName === bridgeJob.branch_name || branchName === bridgeJob.requested_branch_name) return
    const error = _branchNameError(branchName)
    if (error) { setBridgeError(error); return }
    setRenameQueuing(true); setBridgeError('')
    try {
      const job = await requestBranchRename(bridgeJob.id, branchName)
      setBridgeJob(job)
    } catch (e) {
      setBridgeError(e.message || 'Failed to request branch rename')
    } finally {
      setRenameQueuing(false)
    }
  }

  // "Mark reviewed" on a needs_confirmation job -- no CLI/subprocess involved at all, the
  // coding session already ended; this is purely "I looked at the flagged diff, it's fine"
  // bookkeeping (needs_confirmation -> done). See bridge/router.py's acknowledge_job.
  const handleAcknowledgeCheckpoint = async () => {
    if (!bridgeJob || acknowledging) return
    setAcknowledging(true); setBridgeError('')
    try {
      const job = await acknowledgeCheckpoint(bridgeJob.id)
      setBridgeJob(job)
    } catch (e) {
      setBridgeError(e.message || 'Failed to acknowledge job')
    } finally {
      setAcknowledging(false)
    }
  }

  const handleResumeJob = async () => {
    if (!bridgeJob || resumeQueuing) return
    setResumeQueuing(true); setBridgeError('')
    try {
      const job = await queueResumeJob(bridgeJob.id)
      setBridgeJob(job)
      _refreshAttemptStats()
    } catch (e) {
      setBridgeError(e.message || 'Failed to queue resume job')
    } finally {
      setResumeQueuing(false)
    }
  }

  // ── Companion job ─────────────────────────────────────────────────────────

  const handleResumeCompanionJob = async () => {
    if (!companionJob || companionResumeQueuing) return
    setCompanionResumeQueuing(true); setCompanionError('')
    try {
      const job = await queueResumeJob(companionJob.id)
      setCompanionJob(job)
    } catch (e) {
      setCompanionError(e.message || 'Failed to queue resume job')
    } finally {
      setCompanionResumeQueuing(false)
    }
  }

  const handleOpenCompanion = () => {
    setCompanionOpen(true)
    if (knownRepos.length === 0) {
      getKnownBridgeRepos().then(setKnownRepos).catch(() => {})
    }
  }

  const handleCancelCompanion = () => {
    setCompanionOpen(false); setCompanionError('')
  }

  const handleQueueCompanion = async () => {
    if (!bridgeJob || companionQueuing) return
    const repo = companionRepo.trim()
    if (!repo) { setCompanionError('Enter a repo (owner/repo)'); return }
    setCompanionQueuing(true); setCompanionError('')
    try {
      const job = await queueCompanionJob(task.id, repo, bridgeJob.id)
      setCompanionJob(job)
      setCompanionOpen(false)
    } catch (e) {
      setCompanionError(e.message || 'Failed to queue companion job')
    } finally {
      setCompanionQueuing(false)
    }
  }

  const defaultBranch = `qtask/${task?.id}${slugifyPreview(task?.title) ? '-' + slugifyPreview(task.title) : ''}`
  // Editable before a job exists (queue-time override) and while pending/running (a live
  // rename request -- see handleRenameBranch); locked once done/error/stalled/blocked/
  // needs_confirmation, since nothing would ever pick up a rename request for those
  // (resuming reuses the existing branch/worktree by design, not a fresh name).
  const branchFieldDisabled =
    bridgeQueuing || renameQueuing ||
    ['done', 'error', 'stalled', 'blocked', 'needs_confirmation'].includes(bridgeJob?.status)

  return {
    specText, specGenerating, specEditing, specDraft, setSpecDraft, specError, copiedSpec,
    bridgeJob, bridgeQueuing, bridgeError, copiedWorktree, resumeQueuing, attemptStats,
    branchOverride, setBranchOverride, defaultBranch, branchFieldDisabled, renameQueuing,
    companionJob, companionOpen, companionRepo, setCompanionRepo, companionQueuing,
    companionError, knownRepos, companionResumeQueuing, acknowledging,
    handleGenerateSpec, handleSaveSpec, handleStartSpecEdit, handleCancelSpecEdit, handleCopySpec,
    handleCopyWorktreePath, handleSendToBridge, handleRenameBranch, handleResumeJob,
    handleResumeCompanionJob, handleOpenCompanion, handleCancelCompanion, handleQueueCompanion,
    handleAcknowledgeCheckpoint,
  }
}
