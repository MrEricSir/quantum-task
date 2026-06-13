import * as Dialog from '@radix-ui/react-dialog'
import './ConfirmDialog.css'

export default function ConfirmDialog({ open, title, description, confirmLabel = 'Delete', onConfirm, onCancel }) {
  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!v) onCancel() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="confirm-overlay" />
        <Dialog.Content className="confirm-content" onOpenAutoFocus={(e) => e.preventDefault()}>
          <Dialog.Title className="confirm-title">{title}</Dialog.Title>
          {description && (
            <Dialog.Description className="confirm-description">{description}</Dialog.Description>
          )}
          <div className="confirm-actions">
            <button type="button" className="confirm-cancel" onClick={onCancel}>
              Cancel
            </button>
            <button type="button" className="confirm-delete" onClick={onConfirm} autoFocus>
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
