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

export default function Column({ section, label, cards, isActive, isMobile, onEdit, onSave, onDelete, onArchive, onToggle, onMove, onAdd, allTags, onBreakdown, onSelect, selectedCardId }) {
  const { setNodeRef, isOver } = useDroppable({ id: section })
  const { active } = useDndContext()

  // Group overdue cards at the top of the Today column under a single label
  const overdueCards = section === 'today' ? cards.filter(c => (c.overdue_days ?? 0) > 0) : []
  const normalCards  = section === 'today' ? cards.filter(c => (c.overdue_days ?? 0) <= 0) : cards
  const sortedCards  = section === 'today' ? [...overdueCards, ...normalCards] : cards

  const renderCard = (todo, inOverdueGroup = false) => (
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
      onSelect={onSelect}
      isSelected={selectedCardId === todo.id}
      inOverdueGroup={inOverdueGroup}
    />
  )

  return (
    <div className={`column ${isOver ? 'column--over' : ''} ${!isActive ? 'column--inactive' : ''}`}>
      <div className="column-header">
        <span className="column-dot" style={{ background: SECTION_COLORS[section] }} />
        <span className="column-label">{label}</span>
        <span className="column-count">{cards.length}</span>
      </div>

      <SortableContext items={sortedCards.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <div className="column-body" ref={setNodeRef}>
          {overdueCards.length > 0 && (
            <>
              <div className="column-group-label column-group-label--overdue">
                ⚠ Overdue
              </div>
              {overdueCards.map(todo => renderCard(todo, true))}
              {normalCards.length > 0 && <div className="column-group-divider" />}
            </>
          )}
          {normalCards.map(todo => renderCard(todo))}
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
