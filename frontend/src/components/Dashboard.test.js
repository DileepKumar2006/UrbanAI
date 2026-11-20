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

  test('renders the dashboard and account controls', async () => {
    await act(async () => {
      root = createRoot(container);
      root.render(<Dashboard />);
    });
    expect(container.textContent).toContain('City overview');
    expect(container.textContent).toContain('Settings');
    expect(container.textContent).toContain('Live Photo Reporting');
    const settings = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Settings'));
    act(() => settings.click());
    expect(container.textContent).toContain('Create a citizen account');
    expect(container.textContent).toContain('Sign in');
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
    const settings = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Settings'));
    act(() => settings.click());
    const fields = container.querySelectorAll('form:nth-of-type(2) input');
    await act(async () => {
      fields[0].value = 'resident';
      fields[0].dispatchEvent(new Event('input', { bubbles: true }));
      fields[1].value = 'resident@example.test';
      fields[1].dispatchEvent(new Event('input', { bubbles: true }));
      fields[2].value = 'a-long-test-password';
      fields[2].dispatchEvent(new Event('input', { bubbles: true }));
    });
    const register = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Create citizen account'));
    await act(async () => register.click());
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/auth/register'), expect.objectContaining({ method: 'POST' }));
    expect(sessionStorage.getItem('urbanpulse-access-token')).toBe('test-token');
  });
});
