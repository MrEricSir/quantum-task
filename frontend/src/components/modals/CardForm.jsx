/**
 * Shared form fields for creating and editing cards.
 * Used by both AddTodoModal and QuickAddModal's confirm/bulk-edit steps.
 */

import { ALL_SECTIONS } from '../../lib/sections'
export { ALL_SECTIONS }

// Compute which board section a scheduled date falls into
function sectionFromScheduledAt(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr)
  const now = new Date()
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dStart = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.round((dStart - todayStart) / (1000 * 60 * 60 * 24))
  if (diffDays <= 0) return 'today'
  if (diffDays <= 7) return 'week'
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return 'month'
  return 'later'
}

export function isoToLocal(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

export default function CardForm({
  idPrefix = 'cf',
  title, setTitle,
  description, setDescription,
  section, setSection,
  scheduledAt, setScheduledAt,
  recurrenceRule, setRecurrenceRule,
  allTags = [],
  selectedTagIds,
  onToggleTag,
  titleError,
  autoFocus = true,
}) {
  return (
    <>
      <div className="form-group">
        <label htmlFor={`${idPrefix}-title`}>Title</label>
        <input
          id={`${idPrefix}-title`}
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus={autoFocus}
        />
        {titleError && <span className="form-error">{titleError}</span>}
      </div>

      <div className="form-group">
        <label htmlFor={`${idPrefix}-desc`}>Description</label>
        <textarea
          id={`${idPrefix}-desc`}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional details…"
          rows={3}
        />
      </div>

      <div className="form-row">
        <div className="form-group">
          <label htmlFor={`${idPrefix}-section`}>Section</label>
          <select
            id={`${idPrefix}-section`}
            value={section}
            onChange={(e) => setSection(e.target.value)}
            disabled={!!scheduledAt}
          >
            {ALL_SECTIONS.map(({ value, label }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          {scheduledAt && <span className="form-hint">Set by scheduled date</span>}
        </div>
        <div className="form-group">
          <label htmlFor={`${idPrefix}-scheduled`}>Scheduled date &amp; time</label>
          <input
            id={`${idPrefix}-scheduled`}
            type="datetime-local"
            value={scheduledAt}
            onChange={(e) => {
              setScheduledAt(e.target.value)
              const computed = sectionFromScheduledAt(e.target.value)
              if (computed) setSection(computed)
            }}
          />
        </div>
      </div>

      <div className="form-group">
        <label htmlFor={`${idPrefix}-recurrence`}>Repeats</label>
        <select
          id={`${idPrefix}-recurrence`}
          value={recurrenceRule}
          onChange={(e) => setRecurrenceRule(e.target.value)}
        >
          <option value="">Does not repeat</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="yearly">Yearly</option>
        </select>
      </div>

      {allTags.length > 0 && (
        <div className="form-group">
          <label>Tags</label>
          <div className="tag-toggles">
            {allTags.map((tag) => {
              const on = selectedTagIds.includes(tag.id)
              return (
                <button
                  key={tag.id}
                  type="button"
                  className={`tag-toggle ${on ? 'tag-toggle--on' : ''}`}
                  style={on
                    ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                    : { borderColor: tag.color, color: tag.color }
                  }
                  onClick={() => onToggleTag(tag.id)}
                >
                  {tag.name}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </>
  )
}
