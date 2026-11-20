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
    expect(container.textContent).toContain('Good evening, Administrator');
    expect(container.textContent).toContain('Settings');
    expect(container.textContent).toContain('Live Photo Reporting');
    const settings = [...container.querySelectorAll('button')].find(button => button.textContent.includes('Settings'));
    act(() => settings.click());
    expect(container.textContent).toContain('Create a citizen account');
    expect(container.textContent).toContain('Sign in');
  });
});
