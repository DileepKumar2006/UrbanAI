import React from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import Dashboard from './Dashboard';

describe('UrbanAI dashboard', () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    sessionStorage.clear();
    global.fetch = jest.fn((url) => Promise.resolve({
      ok: true,
      json: async () => url.includes('/api/email/status')
        ? { configured: false, department_configured: false, webhook_configured: false, deliveries: [] }
        : { items: [] },
    }));
    container = document.createElement('div');
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (root) act(() => root.unmount());
    container.remove();
  });

  test('opens with sign-in and registration, then keeps guest access available', async () => {
    await act(async () => {
      root = createRoot(container);
      root.render(<Dashboard />);
    });
    expect(container.textContent).toContain('Sign in');
    expect(container.textContent).toContain('Create account');
    expect(container.textContent).toContain('Explore as a guest');
    expect(container.textContent).not.toContain('City overview');
    const guest = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Explore as a guest'));
    await act(async () => guest.click());
    expect(container.textContent).toContain('City overview');
    expect(container.textContent).toContain('Settings');
    expect(container.textContent).toContain('Live Photo Reporting');
    const settings = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Settings'));
    act(() => settings.click());
    expect(container.textContent).toContain('Browsing as a guest');
    expect(container.textContent).not.toContain('Create a citizen account');
    const accountEntry = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Open sign in / create account'));
    await act(async () => accountEntry.click());
    expect(container.textContent).toContain('Create account');
  });

  test('registers a citizen account through the account form', async () => {
    global.fetch = jest.fn((url) => Promise.resolve({
      ok: true,
      json: async () => url.includes('/api/auth/register')
        ? { access_token: 'test-token', user: { id: 4, username: 'resident', role: 'citizen' } }
        : url.includes('/api/email/status')
          ? { configured: false, department_configured: false, webhook_configured: false, deliveries: [] }
          : { items: [] },
    }));
    await act(async () => {
      root = createRoot(container);
      root.render(<Dashboard />);
    });
    const createAccount = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Create account'));
    await act(async () => createAccount.click());
    const fields = container.querySelectorAll('.welcome-auth-form input');
    await act(async () => {
      fields[0].value = 'resident';
      fields[0].dispatchEvent(new Event('input', { bubbles: true }));
      fields[1].value = 'resident@example.test';
      fields[1].dispatchEvent(new Event('input', { bubbles: true }));
      fields[2].value = 'a-long-test-password';
      fields[2].dispatchEvent(new Event('input', { bubbles: true }));
    });
    const register = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Create account') && button.className.includes('primary-btn'));
    await act(async () => register.click());
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/auth/register'), expect.objectContaining({ method: 'POST' }));
    expect(sessionStorage.getItem('urbanpulse-access-token')).toBe('test-token');
    expect(container.textContent).toContain('Welcome, resident');
  });

  test('logs in from the opening screen', async () => {
    global.fetch = jest.fn((url) => Promise.resolve({
      ok: true,
      json: async () => url.includes('/api/auth/login')
        ? { access_token: 'login-token', user: { id: 5, username: 'returning', role: 'citizen' } }
        : url.includes('/api/email/status')
          ? { configured: false, department_configured: false, webhook_configured: false, deliveries: [] }
          : { items: [] },
    }));
    await act(async () => {
      root = createRoot(container);
      root.render(<Dashboard />);
    });
    const fields = container.querySelectorAll('.welcome-auth-form input');
    await act(async () => {
      fields[0].value = 'returning';
      fields[0].dispatchEvent(new Event('input', { bubbles: true }));
      fields[1].value = 'resident-password';
      fields[1].dispatchEvent(new Event('input', { bubbles: true }));
    });
    const signIn = container.querySelector('.welcome-auth-form .primary-btn');
    await act(async () => signIn.click());
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/auth/login'), expect.objectContaining({ method: 'POST' }));
    expect(sessionStorage.getItem('urbanpulse-access-token')).toBe('login-token');
    expect(container.textContent).toContain('Welcome, returning');
  });
});
