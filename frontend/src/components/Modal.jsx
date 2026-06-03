import { useEffect } from 'react'
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
  // Push bottom-sheet up when the software keyboard opens on mobile.
  // visualViewport.height shrinks when the keyboard appears; the difference
  // between window.innerHeight and that height is the keyboard height.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const update = () => {
      const kh = Math.max(0, window.innerHeight - vv.offsetTop - vv.height)
      document.documentElement.style.setProperty('--keyboard-height', `${kh}px`)
    }
    update()
    vv.addEventListener('resize', update)
    vv.addEventListener('scroll', update)
    return () => {
      vv.removeEventListener('resize', update)
      vv.removeEventListener('scroll', update)
      document.documentElement.style.removeProperty('--keyboard-height')
    }
  }, [])

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
