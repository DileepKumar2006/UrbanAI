import './styles/App.css';
import './styles/Nagrik.css';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { I18nextProvider } from 'react-i18next';
import i18n from './i18n';
import { ThemeProvider } from './components/Preferences';
import App from './components/Dashboard';
createRoot(document.getElementById('root')).render(<I18nextProvider i18n={i18n}><ThemeProvider><App /></ThemeProvider></I18nextProvider>);
