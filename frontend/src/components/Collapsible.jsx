import { useState } from 'react'
import { ChevronDownIcon, ChevronRightIcon } from '@radix-ui/react-icons'
import './Collapsible.css'

export function CollapseBody({ open, children }) {
  return (
    <div className={`collapse-body${open ? '' : ' collapse-body--closed'}`}>
      <div>{children}</div>
    </div>
  )
}

export default function Collapsible({ label, count, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="collapsible">
      <button className="collapsible-toggle" onClick={() => setOpen((v) => !v)}>
        <span className="collapsible-chevron">
          {open ? <ChevronDownIcon /> : <ChevronRightIcon />}
        </span>
        {label}
        {count != null && count > 0 && (
          <span className="collapsible-count">{count}</span>
        )}
      </button>
      <CollapseBody open={open}>{children}</CollapseBody>
    </div>
  )
}
