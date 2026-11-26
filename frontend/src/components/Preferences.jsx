import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

const ThemeContext = createContext(null);
export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => localStorage.getItem('nagrik-theme') || 'system');
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)') || { matches: false, addEventListener() {}, removeEventListener() {} };
    const apply = () => {
      const effective = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme;
      document.documentElement.dataset.theme = effective;
      document.documentElement.dataset.themeMode = theme;
    };
    apply();
    if (theme !== 'system') localStorage.setItem('nagrik-theme', theme);
    else localStorage.removeItem('nagrik-theme');
    media.addEventListener?.('change', apply);
    return () => media.removeEventListener?.('change', apply);
  }, [theme]);
  const value = useMemo(() => ({ theme, setTheme }), [theme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function PreferenceControls({ compact = false }) {
  const { i18n, t } = useTranslation();
  const context = useContext(ThemeContext);
  const theme = context?.theme || 'system';
  const setTheme = context?.setTheme || (() => {});
  const languages = [
    ['en', 'English'], ['kn', 'ಕನ್ನಡ'], ['ta', 'தமிழ்'], ['hi', 'हिन्दी'],
  ];
  return <div className={`preference-controls${compact ? ' compact' : ''}`} aria-label={t('preferences.label', {defaultValue:'Nagrik preferences'})}>
    <label><span>{compact ? '文' : t('language.label')}</span><select aria-label={t('language.label')} value={i18n.resolvedLanguage || 'en'} onChange={event => { i18n.changeLanguage(event.target.value); localStorage.setItem('nagrik-language', event.target.value); }}>
      {languages.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
    </select></label>
    <label><span>{compact ? '◐' : t('theme.label')}</span><select aria-label={t('theme.label')} value={theme} onChange={event => setTheme(event.target.value)}>
      <option value="system">{t('theme.system')}</option><option value="light">{t('theme.light')}</option><option value="dark">{t('theme.dark')}</option>
    </select></label>
  </div>;
}

export function useNagrikTheme() {
  return useContext(ThemeContext) || { theme: 'system', setTheme: () => {} };
}
