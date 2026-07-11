import { useDroppable } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import Card from './Card'
import Collapsible from '../layout/Collapsible'
import './Archive.css'

export default function Archive({ cards, onEdit, onDelete, onToggle }) {
  const sorted = [...cards].sort((a, b) => {
    if (!a.completed_at && !b.completed_at) return 0
    if (!a.completed_at) return 1
    if (!b.completed_at) return -1
    return new Date(b.completed_at) - new Date(a.completed_at)
  })

  const { setNodeRef, isOver } = useDroppable({ id: 'archive' })

  return (
    <div className="archive">
      <Collapsible label="Archive" count={cards.length}>
        <SortableContext items={sorted.map((t) => t.id)} strategy={verticalListSortingStrategy}>
          <div
            className={`archive-grid${isOver ? ' archive-grid--over' : ''}`}
            ref={setNodeRef}
          >
            {sorted.length === 0 ? (
              <p className="archive-empty">
                {isOver ? 'Drop here to complete' : 'No completed cards yet.'}
              </p>
            ) : (
              sorted.map((todo) => (
                <Card
                  key={todo.id}
                  card={todo}
                  onEdit={onEdit}
                  onDelete={onDelete}
                  onToggle={onToggle}
                />
              ))
            )}
          </div>
        </SortableContext>
      </Collapsible>
    </div>
  )
}
