import { useEffect, useState } from 'react'
import {
  generateSpec, queueBridgeJob, getBridgeJob, getBridgeJobChain, queueResumeJob,
  queueCompanionJob, getKnownBridgeRepos,
} from '../api'

// Mirrors bridge/scripts/agent_core.py's _slugify -- only used for the branch-name field's
// live preview of what the bridge would auto-generate; doesn't need to be byte-identical
// since it's a preview, not the value actually sent (an empty override still lets the
// bridge compute the real default itself, at pickup time, from the card's title then).
function slugifyPreview(text) {
  return (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
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

    if (task.spec) {
      getBridgeJobChain(task.id)
        .then(({ root, companion }) => { setBridgeJob(root); setCompanionJob(companion) })
        .catch(() => {})
    }
  }, [open, task?.id, initialTab]) // eslint-disable-line react-hooks/exhaustive-deps

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
    if (branchName && /\s/.test(branchName)) {
      setBridgeError("Branch name can't contain whitespace")
      return
    }
    if (branchName.startsWith('-')) {
      setBridgeError("Branch name can't start with '-'")
      return
    }
    setBridgeQueuing(true); setBridgeError('')
    try {
      const job = await queueBridgeJob(task.id, branchName || undefined)
      setBridgeJob(job)
    } catch (e) {
      setBridgeError(e.message || 'Failed to queue bridge job')
    } finally {
      setBridgeQueuing(false)
    }
  }

  const handleResumeJob = async () => {
    if (!bridgeJob || resumeQueuing) return
    setResumeQueuing(true); setBridgeError('')
    try {
      const job = await queueResumeJob(bridgeJob.id)
      setBridgeJob(job)
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
  const branchFieldDisabled =
    bridgeQueuing || bridgeJob?.status === 'running' || bridgeJob?.status === 'pending'

  return {
    specText, specGenerating, specEditing, specDraft, setSpecDraft, specError, copiedSpec,
    bridgeJob, bridgeQueuing, bridgeError, copiedWorktree, resumeQueuing,
    branchOverride, setBranchOverride, defaultBranch, branchFieldDisabled,
    companionJob, companionOpen, companionRepo, setCompanionRepo, companionQueuing,
    companionError, knownRepos, companionResumeQueuing,
    handleGenerateSpec, handleSaveSpec, handleStartSpecEdit, handleCancelSpecEdit, handleCopySpec,
    handleCopyWorktreePath, handleSendToBridge, handleResumeJob,
    handleResumeCompanionJob, handleOpenCompanion, handleCancelCompanion, handleQueueCompanion,
  }
}
