import '../i18n';
import React, { useEffect, useState } from 'react';
import RatingStars from './RatingStars';
import { PreferenceControls } from './Preferences';
import { useTranslation } from 'react-i18next';

export default function LandingPage({ api, mode, setMode, loginName, setLoginName, loginPassword,
  setLoginPassword, registerName, setRegisterName, registerEmail, setRegisterEmail,
  registerPassword, setRegisterPassword, onSignIn, onRegister, onGuest, error }) {
  const [stats, setStats] = useState(null);
  const [reviews, setReviews] = useState([]);
  const {t}=useTranslation();
  const ui=key=>t(`ui.${key}`);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(`${api}/api/public/stats`, { signal: controller.signal }).then(response => response.ok ? response.json() : null),
      fetch(`${api}/api/reviews?per_page=2&sort=newest`, { signal: controller.signal }).then(response => response.ok ? response.json() : null),
    ]).then(([cityStats, publicReviews]) => {
      if (cityStats?.success) setStats(cityStats);
      if (publicReviews?.success) setReviews(publicReviews.items || []);
    }).catch(() => {});
    return () => controller.abort();
  }, [api]);

  return (
    <main className="nagrik-landing">
      <header className="landing-nav">
        <a className="nagrik-brand" href="#top" aria-label="Nagrik home">
          <span className="nagrik-mark" aria-hidden="true">N</span>
          <span><b>Nagrik</b><small>Your City. Your Voice. Your Nagrik.</small></span>
        </a>
        <nav aria-label="Main navigation" className="landing-nav-links">
          <a href="#how-it-works">{ui('howItWorks')}</a><a href="#community">{ui('community')}</a>
          <button type="button" className="landing-signin-link" onClick={() => { setMode('login'); document.getElementById('account-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>{t('landing.signIn')}</button>
          <PreferenceControls compact />
        </nav>
      </header>

      <section className="landing-hero" id="top">
        <div className="landing-hero-copy">
          <span className="landing-eyebrow"><i/>{ui('civicAction')}</span>
          <h1>{t('landing.heroTitle')} <em>{t('landing.heroEmphasis')}</em></h1>
          <p>{t('landing.heroBody')}</p>
          <div className="landing-cta-row">
            <button type="button" className="primary-btn landing-primary" onClick={() => { setMode('register'); document.getElementById('account-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>{t('landing.create')} <span aria-hidden="true">→</span></button>
            <button type="button" className="landing-secondary" onClick={onGuest}>{t('landing.guest')}</button>
          </div>
          <div className="landing-trust"><span>●</span> {ui('landingTrust')}</div>
        </div>
        <div className="landing-visual" aria-label="A civic issue moving through a transparent resolution process">
          <div className="visual-orbit orbit-one"/><div className="visual-orbit orbit-two"/>
          <div className="city-card city-card-main"><div className="city-card-head"><span className="nagrik-mark small-mark">N</span><span><b>Nagrik response</b><small>Community issue tracking</small></span><i className="live-dot"/></div>
            <div className="city-progress-title">{ui('pathToResolution')}</div>
            <div className="city-progress"><span className="progress-step done">✓</span><i/><span className="progress-step done">✓</span><i/><span className="progress-step active">3</span><i/><span className="progress-step">4</span></div>
            <div className="city-progress-labels"><span>{ui('reported')}</span><span>{ui('reviewed')}</span><span>{ui('inProgress')}</span><span>{ui('resolved')}</span></div>
            <div className="city-photo-strip"><span>Road repair</span><span>Waste services</span><span>Public spaces</span></div>
          </div>
          <div className="floating-chip chip-top"><span>◎</span> {ui('locationAware')}</div>
          <div className="floating-chip chip-bottom"><span>✓</span> {ui('updatesInOnePlace')}</div>
        </div>
      </section>

      <section className="landing-stats" aria-label={ui('liveStats')}>
        <div><strong>{stats ? stats.total_reports.toLocaleString() : '—'}</strong><span>{ui('reportsSubmitted')}</span></div>
        <div><strong>{stats ? stats.in_progress_reports.toLocaleString() : '—'}</strong><span>{ui('inProgress')}</span></div>
        <div><strong>{stats ? stats.completed_reports.toLocaleString() : '—'}</strong><span>{ui('markedResolved')}</span></div>
        <div><strong>{stats ? stats.published_reviews.toLocaleString() : '—'}</strong><span>{ui('publishedReviews')}</span></div>
        <small>{ui('statsNote')}</small>
      </section>

      <section className="landing-how" id="how-it-works">
        <div className="landing-section-heading"><span className="landing-eyebrow">{ui('simpleProcess')}</span><h2>{t('landing.howTitle')}</h2><p>{ui('everyStep')}</p></div>
        <div className="landing-feature-grid">
          <article><span className="feature-number">01</span><div className="feature-icon">◎</div><h3>{ui('shareWhat')}</h3><p>{ui('shareWhatBody')}</p></article>
          <article><span className="feature-number">02</span><div className="feature-icon">↗</div><h3>{ui('followResponse')}</h3><p>{ui('followResponseBody')}</p></article>
          <article><span className="feature-number">03</span><div className="feature-icon">✓</div><h3>{ui('seeOutcome')}</h3><p>{ui('seeOutcomeBody')}</p></article>
        </div>
      </section>

      <section className="landing-community" id="community">
        <div className="landing-community-copy"><span className="landing-eyebrow">{ui('communityVoice')}</span><h2>{t('landing.communityTitle')}</h2><p>{ui('communityBody')}</p>
          <button type="button" className="landing-secondary" onClick={() => { setMode('login'); document.getElementById('account-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>{ui('joinConversation')} <span aria-hidden="true">→</span></button>
        </div>
        <div className="landing-review-preview">
          {reviews.length ? reviews.map(review => <article key={review.id} className="landing-review-card"><RatingStars value={review.rating} size="small"/><b>{review.author}</b><p>{review.review_text}</p></article>) : <div className="landing-review-empty"><span>“</span><p>{ui('reviewEmpty')}</p></div>}
        </div>
      </section>

      <section className="landing-account-wrap" id="account-card">
        <div className="landing-account-intro"><span className="landing-eyebrow">{ui('yourCivicSpace')}</span><h2>{mode === 'login' ? t('landing.signInTitle') : t('landing.accountTitle')}</h2><p>{ui('signInFollow')}</p></div>
        <section className="landing-account-card" aria-label="Account access">
          <div className="landing-tabs" role="tablist" aria-label="Choose account action">
            <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>{t('landing.signIn')}</button>
            <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>{t('landing.createAccount')}</button>
          </div>
          {error && <div className="api-error" role="alert">{error}</div>}
          {mode === 'login' ? <form className="landing-auth-form welcome-auth-form" onSubmit={onSignIn}>
            <label>{ui('usernameOrEmail')}<input value={loginName} onChange={event => setLoginName(event.target.value)} autoComplete="username" required/></label>
            <label>{ui('password')}<input type="password" value={loginPassword} onChange={event => setLoginPassword(event.target.value)} autoComplete="current-password" required/></label>
            <button className="primary-btn">{t('landing.signIn')} · Nagrik</button>
          </form> : <form className="landing-auth-form welcome-auth-form" onSubmit={onRegister}>
            <label>{ui('username')}<input value={registerName} onChange={event => setRegisterName(event.target.value)} autoComplete="username" minLength="3" maxLength="80" required/></label>
            <label>{ui('email')}<input type="email" value={registerEmail} onChange={event => setRegisterEmail(event.target.value)} autoComplete="email" required/></label>
            <label>{ui('password')} <small>{ui('passwordMin')}</small><input type="password" value={registerPassword} onChange={event => setRegisterPassword(event.target.value)} autoComplete="new-password" minLength="12" maxLength="256" required/></label>
            <button className="primary-btn">{t('landing.createAccount')}</button>
          </form>}
          <div className="landing-or"><span>{ui('or')}</span></div><button type="button" className="landing-guest" onClick={onGuest}>{t('landing.guest')}</button>
        </section>
      </section>
      <footer className="nagrik-footer"><a className="nagrik-brand" href="#top"><span className="nagrik-mark">N</span><span><b>Nagrik</b><small>Your City. Your Voice. Your Nagrik.</small></span></a><span>{ui('publicService')}</span><a href="#top">{ui('backToTop')}</a></footer>
    </main>
  );
}
