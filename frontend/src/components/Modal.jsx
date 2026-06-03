import * as Dialog from '@radix-ui/react-dialog'
import './Modal.css'

/**
 * Thin wrapper around Radix Dialog.
 * Handles portal, overlay, focus-trap, Escape, and scroll-lock automatically.
 *
 * Usage:
 *   <Modal onClose={fn} className="my-modal">
 *     <Dialog.Title asChild><h2>Title</h2></Dialog.Title>
 *     …content…
 *   </Modal>
 */
export default function Modal({ onClose, className = '', children }) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content
          className={`modal ${className}`.trim()}
          aria-describedby={undefined}
        >
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
