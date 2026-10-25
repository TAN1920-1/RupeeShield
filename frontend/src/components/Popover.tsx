/**
 * Disclosure popover used by the top navigation.
 *
 * It is a button with `aria-expanded`, not a hover menu, so it can be opened with
 * the keyboard and closed with Escape exactly like the dialogs are.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

export function Popover({
  trigger,
  triggerLabel,
  children,
  panelClassName = '',
  onOpen,
}: {
  /** Rendered inside the trigger button. */
  trigger: (state: { open: boolean }) => ReactNode;
  /** Accessible name for the trigger button. */
  triggerLabel: string;
  children: ReactNode | ((close: () => void) => ReactNode);
  panelClassName?: string;
  /** Called when the panel opens, so the content can be refreshed on demand. */
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (container.current && !container.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) onOpen?.();
  }

  return (
    <div className="popover" ref={container}>
      <button
        type="button"
        className="button button--ghost"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={triggerLabel}
        onClick={toggle}
      >
        {trigger({ open })}
      </button>
      {open && (
        <div className={`popover__panel ${panelClassName}`}>
          {typeof children === 'function' ? children(() => setOpen(false)) : children}
        </div>
      )}
    </div>
  );
}
