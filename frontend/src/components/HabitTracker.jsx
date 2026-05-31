import './HabitTracker.css'

export default function HabitTracker({ habits, onToggle }) {
  const done = habits.filter((h) => h.completed_today).length

  return (
    <section className="habit-panel">
      <div className="habit-panel-header">
        <span className="habit-panel-dot" />
        <span className="habit-panel-title">Habits</span>
        <span className="habit-panel-count">{done}/{habits.length}</span>
      </div>
      <div className="habit-panel-body">
        {habits.length === 0 ? (
          <span className="habit-panel-empty">No habits yet — add one in the sidebar</span>
        ) : (
          habits.map((habit) => (
            <div key={habit.id} className={`habit-row${habit.completed_today ? ' habit-row--done' : ''}`}>
              <button
                className="habit-check"
                onClick={() => onToggle(habit)}
                aria-label={habit.completed_today ? 'Mark incomplete' : 'Mark complete'}
              >
                {habit.completed_today && (
                  <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
                    <path d="M1 4L3.5 6.5L9 1" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
              <span className="habit-name">{habit.name}</span>
              {habit.tags.length > 0 && (
                <div className="habit-tag-dots">
                  {habit.tags.map((tag) => (
                    <span key={tag.id} className="habit-tag-dot" style={{ background: tag.color }} title={tag.name} />
                  ))}
                </div>
              )}
              {habit.streak > 0 && (
                <span className="habit-streak">{habit.streak}</span>
              )}
            </div>
          ))
        )}
      </div>
    </section>
  )
}
