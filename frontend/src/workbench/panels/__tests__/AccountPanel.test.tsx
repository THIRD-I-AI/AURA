import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  authService: {
    currentUser: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
    deleteAccount: vi.fn(),
  },
  getCurrentWorkspaceId: vi.fn(() => 'default'),
  workspaceService: {
    list: vi.fn(),
  },
}));

import { authService, workspaceService } from '../../../services/api';
import { AuthProvider } from '../../../auth/AuthContext';
import AccountPanel from '../AccountPanel';

const currentUser = authService.currentUser as ReturnType<typeof vi.fn>;
const updateProfile = authService.updateProfile as ReturnType<typeof vi.fn>;
const changePassword = authService.changePassword as ReturnType<typeof vi.fn>;
const deleteAccount = authService.deleteAccount as ReturnType<typeof vi.fn>;
const logout = authService.logout as ReturnType<typeof vi.fn>;
const listWorkspaces = workspaceService.list as ReturnType<typeof vi.fn>;

const user = { sub: 'u1', name: 'Ada Lovelace', email: 'ada@x.io', role: 'admin' };
const workspace = {
  id: 'default', name: 'Default', description: 'Default workspace (auto-created)',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/account']}>
      <AuthProvider>
        <Routes>
          <Route path="/account" element={<AccountPanel />} />
          <Route path="/" element={<div>landing page</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('AccountPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentUser.mockReturnValue(user);
    listWorkspaces.mockResolvedValue([workspace]);
  });

  it('shows only the active tab\'s section, switching Profile/Security/Danger Zone', async () => {
    renderPanel();
    const editUser = userEvent.setup();

    expect(screen.getByLabelText('Current password')).not.toBeVisible();
    expect(screen.getByLabelText(/type DELETE to confirm/i)).not.toBeVisible();

    await editUser.click(screen.getByRole('tab', { name: 'Security' }));
    expect(screen.getByLabelText('Current password')).toBeVisible();
    expect(screen.getByLabelText(/type DELETE to confirm/i)).not.toBeVisible();

    await editUser.click(screen.getByRole('tab', { name: 'Danger Zone' }));
    expect(screen.getByLabelText(/type DELETE to confirm/i)).toBeVisible();
    expect(screen.getByLabelText('Current password')).not.toBeVisible();
  });

  it('renders real profile and workspace data', async () => {
    renderPanel();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('ada@x.io')).toBeInTheDocument();
    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.getByText('u1')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Default')).toBeInTheDocument());
    expect(screen.getByText('Default workspace (auto-created)')).toBeInTheDocument();
  });

  it('edits and saves the display name', async () => {
    updateProfile.mockResolvedValue({ ...user, name: 'Ada K. Lovelace' });
    renderPanel();
    const editUser = userEvent.setup();

    await editUser.click(screen.getByRole('button', { name: /edit profile/i }));
    const input = screen.getByLabelText('Name');
    await editUser.clear(input);
    await editUser.type(input, 'Ada K. Lovelace');
    await editUser.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(updateProfile).toHaveBeenCalledWith('Ada K. Lovelace'));
    await waitFor(() => expect(screen.getByText('Ada K. Lovelace')).toBeInTheDocument());
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  });

  it('shows the server error and stays in edit mode when the name update fails', async () => {
    updateProfile.mockRejectedValue(new Error('Profile editing requires password auth mode'));
    renderPanel();
    const editUser = userEvent.setup();

    await editUser.click(screen.getByRole('button', { name: /edit profile/i }));
    await editUser.type(screen.getByLabelText('Name'), ' Jr.');
    await editUser.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(screen.getByText('Profile editing requires password auth mode')).toBeInTheDocument());
    expect(screen.getByLabelText('Name')).toBeInTheDocument(); // still editing
  });

  it('rejects a mismatched password confirmation without calling the API', async () => {
    renderPanel();
    const editUser = userEvent.setup();
    await editUser.click(screen.getByRole('tab', { name: 'Security' }));

    await editUser.type(screen.getByLabelText('Current password'), 'old-pass-123');
    await editUser.type(screen.getByLabelText('New password'), 'new-pass-456');
    await editUser.type(screen.getByLabelText('Confirm new password'), 'does-not-match');
    await editUser.click(screen.getByRole('button', { name: /update password/i }));

    expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
    expect(changePassword).not.toHaveBeenCalled();
  });

  it('changes the password and clears the form on success', async () => {
    changePassword.mockResolvedValue(undefined);
    renderPanel();
    const editUser = userEvent.setup();
    await editUser.click(screen.getByRole('tab', { name: 'Security' }));

    await editUser.type(screen.getByLabelText('Current password'), 'old-pass-123');
    await editUser.type(screen.getByLabelText('New password'), 'new-pass-456');
    await editUser.type(screen.getByLabelText('Confirm new password'), 'new-pass-456');
    await editUser.click(screen.getByRole('button', { name: /update password/i }));

    await waitFor(() => expect(changePassword).toHaveBeenCalledWith('old-pass-123', 'new-pass-456'));
    expect(await screen.findByText('Password updated.')).toBeInTheDocument();
    expect((screen.getByLabelText('Current password') as HTMLInputElement).value).toBe('');
  });

  it('shows the server error when the current password is wrong', async () => {
    changePassword.mockRejectedValue(new Error('Current password is incorrect'));
    renderPanel();
    const editUser = userEvent.setup();
    await editUser.click(screen.getByRole('tab', { name: 'Security' }));

    await editUser.type(screen.getByLabelText('Current password'), 'wrong-pass');
    await editUser.type(screen.getByLabelText('New password'), 'new-pass-456');
    await editUser.type(screen.getByLabelText('Confirm new password'), 'new-pass-456');
    await editUser.click(screen.getByRole('button', { name: /update password/i }));

    expect(await screen.findByText('Current password is incorrect')).toBeInTheDocument();
  });

  it('signs out via the real auth flow and returns to the landing page', async () => {
    renderPanel();
    await userEvent.setup().click(screen.getByTestId('account-sign-out'));
    expect(logout).toHaveBeenCalled();
    expect(await screen.findByText('landing page')).toBeInTheDocument();
  });

  it('keeps delete disabled until the confirmation text matches exactly', async () => {
    renderPanel();
    const editUser = userEvent.setup();
    await editUser.click(screen.getByRole('tab', { name: 'Danger Zone' }));

    await editUser.type(screen.getByLabelText('Password'), 'my-password');
    await editUser.type(screen.getByLabelText(/type DELETE to confirm/i), 'delete');
    expect(screen.getByRole('button', { name: /delete account/i })).toBeDisabled();

    await editUser.clear(screen.getByLabelText(/type DELETE to confirm/i));
    await editUser.type(screen.getByLabelText(/type DELETE to confirm/i), 'DELETE');
    expect(screen.getByRole('button', { name: /delete account/i })).toBeEnabled();
    expect(deleteAccount).not.toHaveBeenCalled();
  });

  it('deletes the account, signs out, and returns to the landing page on success', async () => {
    deleteAccount.mockResolvedValue(undefined);
    renderPanel();
    const editUser = userEvent.setup();
    await editUser.click(screen.getByRole('tab', { name: 'Danger Zone' }));

    await editUser.type(screen.getByLabelText('Password'), 'my-password');
    await editUser.type(screen.getByLabelText(/type DELETE to confirm/i), 'DELETE');
    await editUser.click(screen.getByRole('button', { name: /delete account/i }));

    await waitFor(() => expect(deleteAccount).toHaveBeenCalledWith('my-password'));
    expect(logout).toHaveBeenCalled();
    expect(await screen.findByText('landing page')).toBeInTheDocument();
  });

  it('shows the server error and does not sign out when the password is wrong', async () => {
    deleteAccount.mockRejectedValue(new Error('Password is incorrect'));
    renderPanel();
    const editUser = userEvent.setup();
    await editUser.click(screen.getByRole('tab', { name: 'Danger Zone' }));

    await editUser.type(screen.getByLabelText('Password'), 'wrong-password');
    await editUser.type(screen.getByLabelText(/type DELETE to confirm/i), 'DELETE');
    await editUser.click(screen.getByRole('button', { name: /delete account/i }));

    expect(await screen.findByText('Password is incorrect')).toBeInTheDocument();
    expect(logout).not.toHaveBeenCalled();
  });
});
