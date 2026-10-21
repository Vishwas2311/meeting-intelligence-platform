import { useEffect, useRef, useState } from 'react';
import { errorMessage } from './api';

export function SpeakerRenameDialog({
  label,
  onSave,
  onClose,
}: {
  label: string;
  onSave: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(label);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="speaker-dialog"
      aria-labelledby="speaker-dialog-title"
      onCancel={(event) => {
        if (busy) event.preventDefault();
      }}
      onClose={onClose}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || !name.trim()) return;
          setBusy(true);
          setError('');
          try {
            await onSave(name.trim());
            onClose();
          } catch (error) {
            setError(errorMessage(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        <h3 id="speaker-dialog-title">Rename speaker</h3>
        <label>
          Name
          <input
            autoFocus
            required
            maxLength={100}
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy}
          />
        </label>
        <small>Updates this speaker throughout this meeting.</small>
        {error && <p role="alert">{error}</p>}
        <div className="buttons">
          <button type="button" className="secondary small" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button className="primary small" disabled={busy || !name.trim()}>
            {busy ? 'Saving…' : 'Save name'}
          </button>
        </div>
      </form>
    </dialog>
  );
}
