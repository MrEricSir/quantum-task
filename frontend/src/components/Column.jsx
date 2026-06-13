import { useDroppable, useDndContext } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import TodoCard from './TodoCard'
import './Column.css'

const SECTION_COLORS = {
  today: '#3b82f6',
  week: '#8b5cf6',
  month: '#f59e0b',
  later: '#6b7280',
}

export default function Column({ section, label, todos, isActive, isMobile, onEdit, onDelete, onToggle, onMove }) {
  const { setNodeRef, isOver } = useDroppable({ id: section })
  const { active } = useDndContext()

  return (
    <div className={`column ${isOver ? 'column--over' : ''} ${!isActive ? 'column--inactive' : ''}`}>
      <div className="column-header">
        <span className="column-dot" style={{ background: SECTION_COLORS[section] }} />
        <span className="column-label">{label}</span>
        <span className="column-count">{todos.length}</span>
      </div>

      <SortableContext items={todos.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <div className="column-body" ref={setNodeRef}>
          {todos.map((todo) => (
            <TodoCard
              key={todo.id}
              todo={todo}
              isMobile={isMobile}
              onEdit={onEdit}
              onDelete={onDelete}
              onToggle={onToggle}
              onMove={onMove}
            />
          ))}
          {todos.length === 0 && !!active && (
            <div className="column-empty">Drop cards here</div>
          )}
        </div>
      </SortableContext>
    </div>
  )
}
