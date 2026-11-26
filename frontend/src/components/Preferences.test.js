import React from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import i18n from '../i18n';
import { PreferenceControls, ThemeProvider } from './Preferences';

describe('Nagrik language and theme preferences', () => {
  let root, container;
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => { if (root) act(() => root.unmount()); container.remove(); });

  test('system mode follows operating-system changes and manual mode stays fixed', async () => {
    const originalMatchMedia=window.matchMedia;
    let systemDark=false, listener=()=>{};
    window.matchMedia=()=>({get matches(){return systemDark;},addEventListener:(_name,callback)=>{listener=callback;},removeEventListener:()=>{}});
    try {
      await act(async()=>{root=createRoot(container);root.render(<ThemeProvider><PreferenceControls/></ThemeProvider>);});
      expect(document.documentElement.dataset.theme).toBe('light');
      await act(async()=>{systemDark=true;listener();});
      expect(document.documentElement.dataset.theme).toBe('dark');
      const theme=container.querySelectorAll('select')[1];
      await act(async()=>{theme.value='light';theme.dispatchEvent(new Event('change',{bubbles:true}));});
      systemDark=false;
      await act(async()=>listener());
      expect(document.documentElement.dataset.theme).toBe('light');
      expect(document.documentElement.dataset.themeMode).toBe('light');
    } finally { window.matchMedia=originalMatchMedia; }
  });

  test('persists language and theme selections and applies the selected theme', async () => {
    await act(async () => { root=createRoot(container); root.render(<ThemeProvider><PreferenceControls/></ThemeProvider>); });
    const selects=container.querySelectorAll('select');
    await act(async () => {
      selects[0].value='kn'; selects[0].dispatchEvent(new Event('change',{bubbles:true}));
      selects[1].value='dark'; selects[1].dispatchEvent(new Event('change',{bubbles:true}));
    });
    expect(localStorage.getItem('nagrik-language')).toBe('kn');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(selects[1].value).toBe('dark');
    await act(async () => { selects[1].value='system'; selects[1].dispatchEvent(new Event('change',{bubbles:true})); });
    expect(localStorage.getItem('nagrik-theme')).toBeNull();
    expect(document.documentElement.dataset.themeMode).toBe('system');
    await act(async () => i18n.changeLanguage('en'));
  });
});
