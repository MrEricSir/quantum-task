import { useEffect, useRef, useState } from 'react'
import { localDate } from '../api'

// Shared "dated log entry" CRUD state for a single day: fetch-on-mount, add,
// delete, and (optional) inline edit. Field sets differ between food and
// workout entries, so the API calls and edit-form shape are supplied by the
// caller rather than hardcoded here.
export function useDailyLog({ fetchEntries, createEntry, updateEntry, deleteEntry, buildEditForm, buildUpdatePayload, sortKey, revision = 0, onMutate }) {
  const [entries, setEntries] = useState([])
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [date, setDate] = useState(() => localDate())
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState(null)
  const inputRef = useRef(null)

  const load = (d) => fetchEntries(d).then(setEntries).catch(() => {})

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(date) }, [date, revision]) // revision triggers reload after capture modal saves

  const handleAdd = async (extraFields = {}) => {
    if (!input.trim() || saving) return
    setSaving(true)
    try {
      await createEntry({ raw_input: input.trim(), ...extraFields })
      setInput('')
      await load(date)
      onMutate?.()
    } catch {
      // keep input
    } finally {
      setSaving(false)
      inputRef.current?.focus()
    }
  }

  const handleDelete = async (id) => {
    await deleteEntry(id).catch(() => {})
    setEntries(prev => prev.filter(e => e.id !== id))
    onMutate?.()
  }

  const startEdit = (entry) => {
    setEditingId(entry.id)
    setEditForm(buildEditForm(entry))
  }

  const confirmEdit = async () => {
    const payload = buildUpdatePayload(editForm)
    if (payload === null) { setEditingId(null); return }
    try {
      const updated = await updateEntry(editingId, payload)
      setEntries(prev => {
        const next = prev.map(e => (e.id === editingId ? updated : e))
        return sortKey ? next.sort((a, b) => sortKey(a) - sortKey(b)) : next
      })
      onMutate?.()
    } catch {
      // keep editing open on failure
      return
    }
    setEditingId(null)
  }

  return {
    entries, input, setInput, saving, date, setDate,
    editingId, editForm, setEditForm, setEditingId,
    inputRef, handleAdd, handleDelete, startEdit, confirmEdit,
  }
}
