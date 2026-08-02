import { CheckIcon } from '@radix-ui/react-icons'
import './TagFilterBar.css'

export default function TagFilterBar({ tags, selectedTagIds, onToggleTag, onClearTags }) {
  if (!tags.length) return null

  return (
    <div className="tag-filter-bar">
      <button
        className={`tag-filter-bar-pill${selectedTagIds.size === 0 ? ' tag-filter-bar-pill--active' : ''}`}
        onClick={onClearTags}
      >
        All
      </button>
      {tags.map((tag) => {
        const active = selectedTagIds.has(tag.id)
        return (
          <button
            key={tag.id}
            className={`tag-filter-bar-pill${active ? ' tag-filter-bar-pill--active' : ''}`}
            style={
              active
                ? { background: tag.color, borderColor: tag.color, color: '#fff' }
                : { borderColor: tag.color, color: tag.color }
            }
            onClick={() => onToggleTag(tag.id)}
            aria-pressed={active}
          >
            {active && <CheckIcon width={11} height={11} className="tag-filter-bar-check" />}
            {tag.name}
          </button>
        )
      })}
    </div>
  )
}
