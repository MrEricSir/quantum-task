import { useEffect, useState } from 'react'
import { breakdownCard, commitBreakdown } from '../api'

// "Break down" tab state for AssistModal -- extracted so AssistModal.jsx isn't
// one file owning chat, breakdown, and code/bridge state all at once.
export function useAssistBreakdown(task, open, initialTab, mode, onBreakdown, handleClose) {
  const [bdStatus,   setBdStatus]   = useState('idle')
  const [bdSubtasks, setBdSubtasks] = useState([])
  const [bdTagName,  setBdTagName]  = useState('')
  const [bdError,    setBdError]    = useState('')

  useEffect(() => {
    if (!open || !task?.id) return
    setBdStatus('idle'); setBdSubtasks([]); setBdTagName(''); setBdError('')
  }, [open, task?.id, initialTab])

  const generateBreakdown = async () => {
    setBdStatus('loading'); setBdError('')
    try {
      const { subtasks, tag_name } = await breakdownCard(task.id)
      setBdSubtasks(subtasks); setBdTagName(tag_name); setBdStatus('ready')
    } catch {
      setBdError('Failed to generate subtasks.'); setBdStatus('error')
    }
  }

  useEffect(() => {
    if (mode === 'breakdown' && bdStatus === 'idle') generateBreakdown()
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  const confirmBreakdown = async () => {
    const valid = bdSubtasks.filter(s => s.trim())
    if (!valid.length) return
    setBdStatus('saving')
    try {
      const result = await commitBreakdown(task.id, valid, bdTagName)
      onBreakdown?.(result); handleClose()
    } catch {
      setBdError('Failed to create subtasks.'); setBdStatus('ready')
    }
  }

  return { bdStatus, bdSubtasks, setBdSubtasks, bdTagName, bdError, confirmBreakdown }
}
