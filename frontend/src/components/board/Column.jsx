import { useDroppable, useDndContext } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import Card from './Card'
import './Column.css'

const SECTION_COLORS = {
  today: 'var(--color-today)',
  week: 'var(--color-week)',
  month: 'var(--color-month)',
  later: 'var(--color-later)',
}

export default function Column({ section, label, cards, isActive, isMobile, onEdit, onSave, onDelete, onArchive, onToggle, onMove, onAdd, allTags, onBreakdown }) {
  const { setNodeRef, isOver } = useDroppable({ id: section })
  const { active } = useDndContext()

  return (
    <div className={`column ${isOver ? 'column--over' : ''} ${!isActive ? 'column--inactive' : ''}`}>
      <div className="column-header">
        <span className="column-dot" style={{ background: SECTION_COLORS[section] }} />
        <span className="column-label">{label}</span>
        <span className="column-count">{cards.length}</span>
      </div>

      <SortableContext items={cards.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <div className="column-body" ref={setNodeRef}>
          {cards.map((todo) => (
            <Card
              key={todo.id}
              card={todo}
              isMobile={isMobile}
              onEdit={onEdit}
              onSave={onSave}
              onDelete={onDelete}
              onArchive={onArchive}
              onToggle={onToggle}
              onMove={onMove}
              allTags={allTags}
              onBreakdown={onBreakdown}
            />
          ))}
          {cards.length === 0 && active && (
            <div className="column-empty">Drop here</div>
          )}
        </div>
      </SortableContext>

      {onAdd && (
        <button className="column-add-btn" onClick={onAdd}>
          + Add card
        </button>
      )}
    </div>
  )
}
