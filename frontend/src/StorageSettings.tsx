import { useEffect, useState } from 'react';
import { request, downloadBackup, errorMessage } from './api';

interface Storage {
  meetings: number;
  audio_bytes: number;
  disk_free_bytes: number;
  low_disk: boolean;
  audio_retention_enabled: boolean;
  audio_retention_days: number;
  history_policy: string;
}
export function StorageSettings() {
  const [value, setValue] = useState<Storage | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let active = true;
    request<Storage>('/api/storage')
      .then((data) => {
        if (active) setValue(data);
      })
      .catch((error) => {
        if (active) setMessage(errorMessage(error));
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <div className="connection-detail storage-settings">
      <h3>Local history & backups</h3>
      {value && (
        <>
          <p>
            {value.meetings} meetings stored · {(value.audio_bytes / 1048576).toFixed(1)} MB audio ·{' '}
            {(value.disk_free_bytes / 1073741824).toFixed(1)} GB disk free
          </p>
          <p>{value.history_policy}</p>
          <p>
            {value.audio_retention_enabled
              ? `Audio expiry is enabled after ${value.audio_retention_days} days; recent meetings and incomplete recordings are protected.`
              : 'Automatic audio deletion is off.'}
          </p>
          {value.low_disk && (
            <p role="alert">Disk space is low. Save a backup and free space before recording.</p>
          )}
        </>
      )}
      <button
        className="secondary small"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setMessage('');
          try {
            await downloadBackup();
            setMessage('Verified backup downloaded. Keep it in a private location.');
          } catch (error) {
            setMessage(errorMessage(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? 'Verifying backup…' : 'Download full backup'}
      </button>
      {message && <p role="status">{message}</p>}
    </div>
  );
}
