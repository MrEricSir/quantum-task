import * as Dialog from '@radix-ui/react-dialog'
import './KeyboardShortcutsModal.css'

const GROUPS = [
  { id: 'action', label: 'Actions' },
  { id: 'nav',    label: 'Navigation' },
]

export default function KeyboardShortcutsModal({ open, onClose, shortcuts }) {
  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="shortcuts-overlay" />
        <Dialog.Content className="shortcuts-content" onOpenAutoFocus={(e) => e.preventDefault()}>
          <Dialog.Title className="shortcuts-title">Keyboard Shortcuts</Dialog.Title>
          {GROUPS.map(({ id, label }) => {
            const rows = shortcuts.filter((s) => s.group === id)
            if (!rows.length) return null
            return (
              <div key={id} className="shortcuts-group">
                <div className="shortcuts-group-label">{label}</div>
                {rows.map((s) => (
                  <div key={s.key} className="shortcuts-row">
                    <kbd className="shortcuts-kbd">{s.key === '/' ? '/' : s.key}</kbd>
                    <span className="shortcuts-label">{s.label}</span>
                  </div>
                ))}
              </div>
            )
          })}
          <div className="shortcuts-footer">
            <button type="button" className="shortcuts-close" onClick={onClose}>Close</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
