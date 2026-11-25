import React, { useEffect, useState } from 'react';
import RatingStars from './RatingStars';

export default function LandingPage({ api, mode, setMode, loginName, setLoginName, loginPassword,
  setLoginPassword, registerName, setRegisterName, registerEmail, setRegisterEmail,
  registerPassword, setRegisterPassword, onSignIn, onRegister, onGuest, error }) {
  const [stats, setStats] = useState(null);
  const [reviews, setReviews] = useState([]);

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
          <a href="#how-it-works">How it works</a><a href="#community">Community</a>
          <button type="button" className="landing-signin-link" onClick={() => { setMode('login'); document.getElementById('account-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>Sign in</button>
        </nav>
      </header>

      <section className="landing-hero" id="top">
        <div className="landing-hero-copy">
          <span className="landing-eyebrow"><i/>CIVIC ACTION, MADE VISIBLE</span>
          <h1>Your city works better when <em>you’re heard.</em></h1>
          <p>Report local problems, follow every update, and see how your community moves from concern to resolution.</p>
          <div className="landing-cta-row">
            <button type="button" className="primary-btn landing-primary" onClick={() => { setMode('register'); document.getElementById('account-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>Create your account <span aria-hidden="true">→</span></button>
            <button type="button" className="landing-secondary" onClick={onGuest}>Explore as a guest</button>
          </div>
          <div className="landing-trust"><span>●</span> Real reports · Transparent progress · Built for every citizen</div>
        </div>
        <div className="landing-visual" aria-label="A civic issue moving through a transparent resolution process">
          <div className="visual-orbit orbit-one"/><div className="visual-orbit orbit-two"/>
          <div className="city-card city-card-main"><div className="city-card-head"><span className="nagrik-mark small-mark">N</span><span><b>Nagrik response</b><small>Community issue tracking</small></span><i className="live-dot"/></div>
            <div className="city-progress-title">A clearer path to resolution</div>
            <div className="city-progress"><span className="progress-step done">✓</span><i/><span className="progress-step done">✓</span><i/><span className="progress-step active">3</span><i/><span className="progress-step">4</span></div>
            <div className="city-progress-labels"><span>Reported</span><span>Reviewed</span><span>In progress</span><span>Resolved</span></div>
            <div className="city-photo-strip"><span>Road repair</span><span>Waste services</span><span>Public spaces</span></div>
          </div>
          <div className="floating-chip chip-top"><span>◎</span> Location aware reports</div>
          <div className="floating-chip chip-bottom"><span>✓</span> Every update in one place</div>
        </div>
      </section>

      <section className="landing-stats" aria-label="Live city statistics">
        <div><strong>{stats ? stats.total_reports.toLocaleString() : '—'}</strong><span>Reports submitted</span></div>
        <div><strong>{stats ? stats.in_progress_reports.toLocaleString() : '—'}</strong><span>In progress</span></div>
        <div><strong>{stats ? stats.completed_reports.toLocaleString() : '—'}</strong><span>Marked resolved</span></div>
        <div><strong>{stats ? stats.published_reviews.toLocaleString() : '—'}</strong><span>Published reviews</span></div>
        <small>Live figures from Nagrik records. Dashes mean the service is unavailable.</small>
      </section>

      <section className="landing-how" id="how-it-works">
        <div className="landing-section-heading"><span className="landing-eyebrow">A SIMPLE, OPEN PROCESS</span><h2>From a local concern to visible progress.</h2><p>Every step stays connected to the original report.</p></div>
        <div className="landing-feature-grid">
          <article><span className="feature-number">01</span><div className="feature-icon">◎</div><h3>Share what you see</h3><p>Add a photo, place, and short description to tell the city team what needs attention.</p></article>
          <article><span className="feature-number">02</span><div className="feature-icon">↗</div><h3>Follow the response</h3><p>See review, assignment, and work updates together in your complaint timeline.</p></article>
          <article><span className="feature-number">03</span><div className="feature-icon">✓</div><h3>See the outcome</h3><p>Check completion details and before-and-after evidence when work is marked complete.</p></article>
        </div>
      </section>

      <section className="landing-community" id="community">
        <div className="landing-community-copy"><span className="landing-eyebrow">THE COMMUNITY VOICE</span><h2>A better civic platform starts with listening.</h2><p>Share a review of your experience and help make the service more useful for everyone.</p>
          <button type="button" className="landing-secondary" onClick={() => { setMode('login'); document.getElementById('account-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}>Join the conversation <span aria-hidden="true">→</span></button>
        </div>
        <div className="landing-review-preview">
          {reviews.length ? reviews.map(review => <article key={review.id} className="landing-review-card"><RatingStars value={review.rating} size="small"/><b>{review.author}</b><p>{review.review_text}</p></article>) : <div className="landing-review-empty"><span>“</span><p>Citizen reviews will appear here after they’re shared and approved.</p></div>}
        </div>
      </section>

      <section className="landing-account-wrap" id="account-card">
        <div className="landing-account-intro"><span className="landing-eyebrow">YOUR CIVIC SPACE</span><h2>{mode === 'login' ? 'Welcome back.' : 'Make your voice count.'}</h2><p>Sign in to follow your reports, or create a free citizen account to get started.</p></div>
        <section className="landing-account-card" aria-label="Account access">
          <div className="landing-tabs" role="tablist" aria-label="Choose account action">
            <button type="button" role="tab" aria-selected={mode === 'login'} className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>Sign in</button>
            <button type="button" role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>Create account</button>
          </div>
          {error && <div className="api-error" role="alert">{error}</div>}
          {mode === 'login' ? <form className="landing-auth-form welcome-auth-form" onSubmit={onSignIn}>
            <label>Username or email<input value={loginName} onChange={event => setLoginName(event.target.value)} autoComplete="username" required/></label>
            <label>Password<input type="password" value={loginPassword} onChange={event => setLoginPassword(event.target.value)} autoComplete="current-password" required/></label>
            <button className="primary-btn">Sign in to Nagrik</button>
          </form> : <form className="landing-auth-form welcome-auth-form" onSubmit={onRegister}>
            <label>Username<input value={registerName} onChange={event => setRegisterName(event.target.value)} autoComplete="username" minLength="3" maxLength="80" required/></label>
            <label>Email<input type="email" value={registerEmail} onChange={event => setRegisterEmail(event.target.value)} autoComplete="email" required/></label>
            <label>Password <small>(12 characters minimum)</small><input type="password" value={registerPassword} onChange={event => setRegisterPassword(event.target.value)} autoComplete="new-password" minLength="12" maxLength="256" required/></label>
            <button className="primary-btn">Create citizen account</button>
          </form>}
          <div className="landing-or"><span>OR</span></div><button type="button" className="landing-guest" onClick={onGuest}>Continue as guest</button>
        </section>
      </section>
      <footer className="nagrik-footer"><a className="nagrik-brand" href="#top"><span className="nagrik-mark">N</span><span><b>Nagrik</b><small>Your City. Your Voice. Your Nagrik.</small></span></a><span>Public service starts with a voice.</span><a href="#top">Back to top ↑</a></footer>
    </main>
  );
}
