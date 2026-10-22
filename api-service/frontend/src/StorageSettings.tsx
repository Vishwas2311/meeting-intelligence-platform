import { useEffect, useState } from 'react';
import { request, downloadBackup, errorMessage } from './api';

interface Storage {
  database: string;
  backup_supported?: boolean;
  backup_guidance?: string;
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
  const [admin, setAdmin] = useState(false);
  useEffect(() => {
    let active = true;
    request<{ admin: boolean }>('/api/auth/me')
      .then((identity) => {
        if (active) setAdmin(identity.admin);
        if (!identity.admin) return null;
        return request<Storage>('/api/storage');
      })
      .then((data) => {
        if (active && data) setValue(data);
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
      <h3>History & backups</h3>
      {!admin && (
        <p>
          Company administrators manage server backups and retention. Your unsent browser recordings
          remain on this device.
        </p>
      )}
      {value && (
        <>
          <p>
            {value.meetings} meetings stored · {(value.audio_bytes / 1048576).toFixed(1)} MB audio ·{' '}
            {(value.disk_free_bytes / 1073741824).toFixed(1)} GB disk free
          </p>
          <p>{value.history_policy}</p>
          <p>Database: {value.database}</p>
          {value.backup_supported === false && <p>{value.backup_guidance}</p>}
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
      {admin && (
        <button
          className="secondary small"
          disabled={busy || !value || value.backup_supported === false}
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
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
