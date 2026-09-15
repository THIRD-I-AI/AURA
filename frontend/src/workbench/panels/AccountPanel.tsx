/* Account — native panel. shadcn/ui + Tailwind (frontend/CLAUDE.md): ui-kit
   primitives + token utilities, no inline styles. Identity comes off the
   decoded JWT; workspace detail and profile/password edits are the real
   round-trips (PATCH /auth/me, POST /auth/change-password). Editing is
   password-mode only — an open-mode session has no DB row to persist to,
   and the backend 422s that case with a clear reason. */
import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut, Pencil, X } from 'lucide-react';

import { cn } from '@/lib/cn';
import { Panel, PanelHeader, PanelBody } from '@/components/ui-kit/panel';
import { Button } from '@/components/ui-kit/button';
import { Avatar } from '@/components/ui-kit/avatar';
import { useAuth } from '../../auth/AuthContext';
import { authService, getCurrentWorkspaceId, workspaceService, type Workspace } from '../../services/api';

/** ApiClient's request() throws a plain {message, status, ...} object, not an
 *  Error instance (see ApiError in services/api.ts) — `instanceof Error`
 *  never matches it, so it needs its own duck-typed check here. */
function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error) return err.message;
  if (err && typeof err === 'object' && typeof (err as { message?: unknown }).message === 'string') {
    return (err as { message: string }).message;
  }
  return fallback;
}

const inputClass = cn(
  'w-full rounded-none border border-border bg-secondary px-3 py-1.5 font-mono text-sm text-card-foreground',
  'placeholder:text-text-tertiary outline-none focus-visible:border-ring',
  'aria-invalid:border-danger',
);

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="block font-mono text-2xs uppercase tracking-wider text-text-tertiary">
      {children}
    </label>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border-hairline py-1.5 last:border-0">
      <span className="font-mono text-2xs uppercase tracking-wider text-text-tertiary">{label}</span>
      <span className="truncate text-sm text-text-primary">{value}</span>
    </div>
  );
}

function ProfileCard() {
  const { user, updateProfile } = useAuth();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user?.name || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startEdit = () => { setName(user?.name || ''); setError(null); setEditing(true); };
  const cancelEdit = () => { setEditing(false); setError(null); };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || trimmed === user?.name) { setEditing(false); return; }
    setSaving(true);
    setError(null);
    try {
      await updateProfile(trimmed);
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err, 'Could not update profile.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel>
      <PanelHeader
        title="Profile"
        actions={!editing && (
          <Button variant="ghost" size="icon-sm" onClick={startEdit} aria-label="Edit profile">
            <Pencil />
          </Button>
        )}
      />
      <PanelBody>
        <div className="mb-1 flex items-center gap-3 border-b border-border-hairline pb-3">
          <Avatar name={user?.name} email={user?.email} seed={user?.sub} size="lg" />
          {!editing ? (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-text-primary">{user?.name || 'Signed in'}</div>
              <div className="truncate font-mono text-2xs text-text-tertiary">{user?.email || '—'}</div>
            </div>
          ) : (
            <form onSubmit={save} className="flex min-w-0 flex-1 flex-col gap-1.5">
              <FieldLabel htmlFor="account-name-input">Name</FieldLabel>
              <div className="flex items-center gap-1.5">
                <input
                  id="account-name-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={saving}
                  aria-invalid={!!error}
                  autoFocus
                  className={inputClass}
                />
                <Button type="submit" size="sm" disabled={saving || !name.trim()}>
                  {saving ? '…' : 'Save'}
                </Button>
                <Button type="button" variant="ghost" size="icon-sm" onClick={cancelEdit} disabled={saving} aria-label="Cancel">
                  <X />
                </Button>
              </div>
              {error && <div className="font-mono text-2xs text-danger">{error}</div>}
            </form>
          )}
        </div>
        <Field label="Role" value={user?.role || '—'} />
        <Field label="User ID" value={user?.sub || '—'} />
      </PanelBody>
    </Panel>
  );
}

function WorkspaceCard() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);

  useEffect(() => {
    const wsId = getCurrentWorkspaceId();
    workspaceService.list()
      .then((list) => setWorkspace(list.find((w) => w.id === wsId) ?? null))
      .catch(() => undefined);
  }, []);

  return (
    <Panel>
      <PanelHeader title="Workspace" />
      <PanelBody>
        <Field label="Name" value={workspace?.name || getCurrentWorkspaceId()} />
        <Field label="Workspace ID" value={getCurrentWorkspaceId()} />
        {workspace?.description && <Field label="Description" value={workspace.description} />}
      </PanelBody>
    </Panel>
  );
}

/** Live requirement hint — grey until typed, then red/green. Real-time
 *  feedback instead of only finding out on submit. */
function Hint({ ok, text }: { ok: boolean | null; text: string }) {
  return (
    <div className={cn(
      'font-mono text-2xs',
      ok === null ? 'text-text-tertiary' : ok ? 'text-signal' : 'text-danger',
    )}>
      {text}
    </div>
  );
}

function SecurityCard() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const longEnough = next.length >= 8;
  const tooShort = next.length > 0 && !longEnough;
  const matches = confirm.length > 0 && next === confirm;
  const mismatch = confirm.length > 0 && !matches;
  const canSubmit = current.length > 0 && longEnough && matches;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSuccess(false);
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      await authService.changePassword(current, next);
      setCurrent(''); setNext(''); setConfirm('');
      setSuccess(true);
    } catch (err) {
      setError(errorMessage(err, 'Could not change password.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel>
      <PanelHeader title="Security" hint="change password" />
      <PanelBody>
        <form onSubmit={submit} className="flex flex-col gap-2.5" data-testid="account-password-form">
          <div className="flex flex-col gap-1">
            <FieldLabel htmlFor="account-current-password">Current password</FieldLabel>
            <input
              id="account-current-password"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              disabled={saving}
              required
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <FieldLabel htmlFor="account-new-password">New password</FieldLabel>
            <input
              id="account-new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              disabled={saving}
              required
              aria-invalid={tooShort}
              className={inputClass}
            />
            <Hint ok={next.length === 0 ? null : longEnough} text="At least 8 characters" />
          </div>
          <div className="flex flex-col gap-1">
            <FieldLabel htmlFor="account-confirm-password">Confirm new password</FieldLabel>
            <input
              id="account-confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={saving}
              required
              aria-invalid={mismatch}
              className={inputClass}
            />
            {confirm.length > 0 && <Hint ok={matches} text={matches ? 'Passwords match' : 'Passwords do not match'} />}
          </div>
          {error && <div className="font-mono text-2xs text-danger">{error}</div>}
          {success && <div className="font-mono text-2xs text-signal">Password updated.</div>}
          <Button type="submit" size="sm" disabled={saving || !canSubmit} className="self-start">
            {saving ? 'Updating…' : 'Update password'}
          </Button>
        </form>
      </PanelBody>
    </Panel>
  );
}

const DELETE_CONFIRM_TEXT = 'DELETE';

function DangerZoneCard() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canDelete = password.length > 0 && confirmText === DELETE_CONFIRM_TEXT;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canDelete) return;
    setDeleting(true);
    setError(null);
    try {
      await authService.deleteAccount(password);
      logout();
      navigate('/');
    } catch (err) {
      setError(errorMessage(err, 'Could not delete account.'));
      setDeleting(false);
    }
  };

  return (
    <Panel>
      <PanelHeader title={<span className="text-danger">Danger zone</span>} hint="permanently delete account" />
      <PanelBody>
        <form onSubmit={submit} className="flex flex-col gap-2.5" data-testid="account-delete-form">
          <p className="font-mono text-2xs text-text-tertiary">
            This permanently deletes your account and cannot be undone.
          </p>
          <div className="flex flex-col gap-1">
            <FieldLabel htmlFor="account-delete-password">Password</FieldLabel>
            <input
              id="account-delete-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={deleting}
              required
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <FieldLabel htmlFor="account-delete-confirm">
              Type {DELETE_CONFIRM_TEXT} to confirm
            </FieldLabel>
            <input
              id="account-delete-confirm"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              disabled={deleting}
              required
              className={inputClass}
            />
          </div>
          {error && <div className="font-mono text-2xs text-danger">{error}</div>}
          <Button type="submit" variant="destructive" size="sm" disabled={!canDelete || deleting} className="self-start">
            {deleting ? 'Deleting…' : 'Delete account'}
          </Button>
        </form>
      </PanelBody>
    </Panel>
  );
}

const TABS = [
  { id: 'profile', label: 'Profile' },
  { id: 'security', label: 'Security' },
  { id: 'danger', label: 'Danger Zone' },
] as const;
type TabId = (typeof TABS)[number]['id'];

export default function AccountPanel() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabId>('profile');

  const signOut = () => { logout(); navigate('/'); };

  return (
    <div className="flex flex-col gap-3.5" data-testid="wb-account-panel">
      <div className="flex items-center gap-3">
        <div role="tablist" aria-label="Account sections" className="flex flex-1 gap-1.5 overflow-x-auto">
          {TABS.map((t) => (
            <Button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              aria-controls={`account-tabpanel-${t.id}`}
              variant={tab === t.id ? 'secondary' : 'ghost'}
              size="sm"
              className="shrink-0"
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </Button>
          ))}
        </div>
        <Button variant="destructive" size="sm" onClick={signOut} data-testid="account-sign-out" className="shrink-0">
          <LogOut /> Sign out
        </Button>
      </div>

      <div id="account-tabpanel-profile" role="tabpanel" hidden={tab !== 'profile'}>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(360px,100%),1fr))] items-start gap-4">
          <ProfileCard />
          <WorkspaceCard />
        </div>
      </div>

      <div id="account-tabpanel-security" role="tabpanel" hidden={tab !== 'security'} className="max-w-[420px]">
        <SecurityCard />
      </div>

      <div id="account-tabpanel-danger" role="tabpanel" hidden={tab !== 'danger'} className="max-w-[420px]">
        <DangerZoneCard />
      </div>
    </div>
  );
}
