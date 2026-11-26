import '../i18n';
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import RatingStars, { ratingColor } from './RatingStars';

const PER_PAGE = 6;

function useReveal() {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!ref.current || !('IntersectionObserver' in window)) { setVisible(true); return undefined; }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setVisible(true); observer.disconnect(); }
    }, { threshold: 0.12 });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, visible];
}

function ReviewCard({ review, index, moderation, onModerate }) {
  const { t } = useTranslation();
  const [ref, visible] = useReveal();
  return <article ref={ref} className={`review-card ${visible ? 'is-visible' : ''}`} style={{ '--review-delay': `${Math.min(index, 5) * 70}ms` }}>
    <div className="review-card-top"><RatingStars value={review.rating} size="small"/><span className="review-date">{review.created_at ? new Date(review.created_at).toLocaleDateString() : t('reviews.dateUnavailable', {defaultValue:'Date unavailable'})}</span></div>
    <p className="review-copy">{review.review_text}</p>
    <div className="review-author-row"><span className="review-avatar" aria-hidden="true">{(review.author || 'C').slice(0, 1).toUpperCase()}</span><div><b>{review.author || t('reviews.citizen', {defaultValue:'Citizen'})}</b><small>{moderation ? t(`status.${review.moderation_status}`, {defaultValue:review.moderation_status}) : t('reviews.nagrikCitizen', {defaultValue:'Nagrik citizen'})}</small></div></div>
    {moderation && <div className="review-moderation-row">
      {review.moderation_status !== 'published' && <button type="button" className="review-mod-publish" onClick={() => onModerate(review, 'published')}>{t('reviews.publish')}</button>}
      {review.moderation_status !== 'hidden' && <button type="button" className="review-mod-hide" onClick={() => onModerate(review, 'hidden')}>{t('reviews.hide')}</button>}
      {review.user_id && <span className="review-private-id">Citizen #{review.user_id}</span>}
    </div>}
  </article>;
}

export default function ReviewsPage({ api, authToken, authUser, adminKey, onAccount, onToast }) {
  const { t } = useTranslation();
  const isAdmin = authUser?.role === 'admin' || Boolean(adminKey);
  const isCitizen = Boolean(authToken && authUser?.role === 'citizen');
  const [summary, setSummary] = useState({ average_rating: null, total_reviews: 0, rating_distribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 } });
  const [items, setItems] = useState([]);
  const [mine, setMine] = useState(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [search, setSearch] = useState('');
  const [ratingFilter, setRatingFilter] = useState('');
  const [sort, setSort] = useState('newest');
  const [moderationFilter, setModerationFilter] = useState('pending');
  const [rating, setRating] = useState(0);
  const [hoverRating, setHoverRating] = useState(0);
  const [reviewText, setReviewText] = useState('');
  const [editing, setEditing] = useState(false);
  const [filterSearch, setFilterSearch] = useState('');
  const [savingModeration, setSavingModeration] = useState(null);
  const [summaryRef, summaryVisible] = useReveal();
  const [composeRef, composeVisible] = useReveal();

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const params = new URLSearchParams({ page: String(page), per_page: String(PER_PAGE) });
    if (filterSearch.trim()) params.set('search', filterSearch.trim());
    if (ratingFilter) params.set('rating', ratingFilter);
    params.set('sort', sort);
    let endpoint;
    if (isAdmin) {
      endpoint = `${api}/api/admin/reviews?${params.toString()}&status=${encodeURIComponent(moderationFilter)}`;
    } else {
      endpoint = `${api}/api/reviews?${params.toString()}`;
    }
    fetch(endpoint, { headers: authToken ? { Authorization: `Bearer ${authToken}` } : adminKey ? { 'X-Admin-Key': adminKey } : {}, signal: controller.signal })
      .then(async response => { const value = await response.json(); if (!response.ok) throw Error(value.error || t('reviews.loadError', {defaultValue:'Could not load reviews.'})); return value; })
      .then(value => { setItems(value.items || []); setSummary({ average_rating: value.average_rating, total_reviews: value.total_reviews || 0, rating_distribution: value.rating_distribution || {} }); setPages(Math.max(1, value.pagination?.pages || 1)); setError(''); })
      .catch(value => { if (value.name !== 'AbortError') setError(value.message); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [api, authToken, adminKey, isAdmin, page, filterSearch, ratingFilter, sort, moderationFilter]);

  useEffect(() => {
    if (!isCitizen) { setMine(null); return undefined; }
    const controller = new AbortController();
    fetch(`${api}/api/reviews/mine`, { headers: { Authorization: `Bearer ${authToken}` }, signal: controller.signal })
      .then(async response => { const value = await response.json(); if (!response.ok) throw Error(value.error || t('reviews.mineLoadError', {defaultValue:'Could not load your review.'})); return value; })
      .then(value => setMine(value.review || null))
      .catch(value => { if (value.name !== 'AbortError') onToast(value.message); });
    return () => controller.abort();
  }, [api, authToken, isCitizen]);

  useEffect(() => {
    if (!mine || editing) return;
    setRating(mine.rating);
    setReviewText(mine.review_text);
  }, [mine, editing]);

  async function submitReview(event) {
    event.preventDefault();
    if (!rating) { setError(t('reviews.chooseRating', {defaultValue:'Choose a star rating before submitting.'})); return; }
    setSubmitting(true); setError('');
    try {
      const response = await fetch(`${api}/api/reviews/mine`, {
        method: mine ? 'PUT' : 'POST',
        headers: { Authorization: `Bearer ${authToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, review_text: reviewText.trim() }),
      });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || t('reviews.saveError', {defaultValue:'Your review could not be saved.'}));
      setMine(value.review); setEditing(false); setPage(1); setFilterSearch(''); setRatingFilter('');
      onToast(value.review.moderation_status === 'pending' ? t('reviews.savedPending', {defaultValue:'Review saved and sent for moderation.'}) : t('reviews.saved', {defaultValue:'Review saved.'}));
    } catch (value) { setError(value.message); }
    finally { setSubmitting(false); }
  }

  async function deleteReview() {
    if (!window.confirm('Delete your website review? This cannot be undone.')) return;
    setSubmitting(true);
    try {
      const response = await fetch(`${api}/api/reviews/mine`, { method: 'DELETE', headers: { Authorization: `Bearer ${authToken}` } });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || t('reviews.deleteError', {defaultValue:'Your review could not be deleted.'}));
      setMine(null); setRating(0); setReviewText(''); setEditing(true); onToast(t('reviews.deleted', {defaultValue:'Your review was deleted.'}));
    } catch (value) { setError(value.message); }
    finally { setSubmitting(false); }
  }

  async function moderate(review, moderation_status) {
    setSavingModeration(review.id);
    try {
      const response = await fetch(`${api}/api/admin/reviews/${review.id}/moderation`, {
        method: 'PATCH',
        headers: { ...(authToken ? { Authorization: `Bearer ${authToken}` } : { 'X-Admin-Key': adminKey }), 'Content-Type': 'application/json' },
        body: JSON.stringify({ moderation_status }),
      });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || t('reviews.moderationError', {defaultValue:'Moderation action failed.'}));
      setItems(current => moderationFilter === 'all' ? current.map(item => item.id === review.id ? value.review : item) : current.filter(item => item.id !== review.id));
      setSummary({ average_rating: value.average_rating, total_reviews: value.total_reviews, rating_distribution: value.rating_distribution });
      onToast(moderation_status === 'published' ? t('reviews.publishedToast', {defaultValue:'Review published.'}) : t('reviews.hiddenToast', {defaultValue:'Review hidden from public view.'}));
    } catch (value) { setError(value.message); }
    finally { setSavingModeration(null); }
  }

  function applySearch(event) { event.preventDefault(); setPage(1); setFilterSearch(search); }
  const average = summary.average_rating;
  const liveRating = hoverRating || rating;

  return <div className="reviews-page">
    <header className="reviews-page-heading"><div><span className="landing-eyebrow">{t('reviews.community')}</span><h1>{t('reviews.headline')}</h1><p>{t('reviews.intro')}</p></div>
      {isAdmin && <label className="review-admin-filter">{t('reviews.status')}<select value={moderationFilter} onChange={event => { setModerationFilter(event.target.value); setPage(1); }}><option value="pending">{t('reviews.needsReview')}</option><option value="published">{t('status.published')}</option><option value="hidden">{t('status.hidden')}</option><option value="all">{t('reviews.allReviews')}</option></select></label>}
    </header>

    <section ref={summaryRef} className="review-summary-grid" aria-label={t('reviews.summaryLabel')}>
      <div className="review-overview-card"><span className="review-card-eyebrow">{t('reviews.overall')}</span>
        {average == null ? <><strong className="review-average-empty">— <small>/ 5</small></strong><p>{t('reviews.noReviews')}</p></> : <><strong className="review-average" style={{ '--rating-color': ratingColor(average) }}>{average.toFixed(1)}<small>/ 5</small></strong><RatingStars value={average} size="large"/><p>{summary.total_reviews} {t('reviews.total')}</p></>}
      </div>
      <div className="review-distribution-card"><div className="review-card-eyebrow">{t('reviews.distribution')}</div>{[5, 4, 3, 2, 1].map(stars => {
        const count = Number(summary.rating_distribution?.[String(stars)] || 0);
        const width = summary.total_reviews ? `${(count / summary.total_reviews) * 100}%` : '0%';
        return <div className="rating-distribution-row" key={stars}><span>{stars} <b aria-hidden="true">★</b></span><div className="rating-bar-track"><i style={{ width, backgroundColor: ratingColor(stars), transform: summaryVisible ? 'scaleX(1)' : 'scaleX(0)' }}/></div><span className="rating-count">{count}</span></div>;
      })}</div>
    </section>

    <section ref={composeRef} className={`review-compose-card ${composeVisible ? 'is-visible' : ''}`}>
      <div className="review-compose-intro"><span className="landing-eyebrow">{t('reviews.experienceMatters')}</span><h2>{mine && !editing ? t('reviews.yourNagrikReview') : t('reviews.howWorked')}</h2>
        {mine && !editing ? <><p>{t('reviews.reviewStatus')} <b className={`moderation-label status-${mine.moderation_status}`}>{t(`status.${mine.moderation_status}`, {defaultValue:mine.moderation_status})}</b>. {mine.moderation_status === 'pending' ? t('reviews.willModerate') : mine.moderation_status === 'hidden' ? t('reviews.currentlyHidden') : t('reviews.thanksCommunity')}</p></> : <p>{t('reviews.separateFeedback')}</p>}
      </div>
      {isCitizen ? (mine && !editing ? <div className="review-mine-summary"><RatingStars value={mine.rating} size="medium"/><p>{mine.review_text}</p><div><button type="button" className="review-secondary-btn" onClick={() => { setEditing(true); setRating(mine.rating); setReviewText(mine.review_text); }}>{t('reviews.edit')}</button><button type="button" className="review-delete-btn" onClick={deleteReview} disabled={submitting}>{t('reviews.delete')}</button></div></div> : <form className="review-form" onSubmit={submitReview}>
        <div className="review-rating-field"><div><b>{t('reviews.yourRating')}</b><small>{liveRating ? ['','Not good','Could be better','Good','Very good','Excellent'][liveRating] : t('reviews.selectStars')}</small></div>
          <div className="review-rating-picker"><RatingStars value={liveRating} interactive onChange={setRating} onHover={setHoverRating}/><span className="review-rating-number" style={{ '--rating-color': ratingColor(liveRating) }}>{liveRating || '—'}<small>/ 5</small></span></div>
        </div>
        <label className="review-text-label">{t('reviews.yourReview')}<textarea value={reviewText} onChange={event => setReviewText(event.target.value.slice(0, 2000))} maxLength="2000" rows="4" placeholder={t('reviews.reviewPrompt')} required/></label>
        <div className="review-form-footer"><small>{reviewText.length} / 2000 {t('reviews.characters')}</small><div>{editing&&<button type="button" className="review-secondary-btn" onClick={() => { setEditing(false); setError(''); }}>{t('reviews.cancel')}</button>}<button className="primary-btn" disabled={submitting}>{submitting ? t('reviews.saving') : mine ? t('reviews.saveChanges') : t('reviews.submit')}</button></div></div>
      </form>) : <div className="review-signin-prompt"><p>{t('reviews.login')}</p><button type="button" className="primary-btn" onClick={onAccount}>{t('reviews.signInOrCreate')}</button></div>}
    </section>

    <section className="review-list-section"><div className="review-list-heading"><div><span className="landing-eyebrow">{t('reviews.communityNotes')}</span><h2>{isAdmin ? t('reviews.moderation') : t('reviews.published')}</h2><p>{isAdmin ? t('reviews.moderationPrivate') : t('reviews.approvedOnly')}</p></div>
      <div className="review-list-controls"><form className="review-search-form" onSubmit={applySearch}><label className="visually-hidden" htmlFor="review-search">{t('reviews.search')}</label><input id="review-search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t('reviews.search')} maxLength="100"/><button type="submit" aria-label={t('reviews.search')}>⌕</button></form>
        <label className="visually-hidden" htmlFor="review-rating-filter">{t('reviews.ratingFilter')}</label><select id="review-rating-filter" value={ratingFilter} onChange={event => { setRatingFilter(event.target.value); setPage(1); }}><option value="">{t('reviews.allRatings')}</option>{[5, 4, 3, 2, 1].map(stars => <option value={stars} key={stars}>{stars} {t('reviews.stars')}</option>)}</select>
        <><label className="visually-hidden" htmlFor="review-sort">{t('reviews.sort')}</label><select id="review-sort" value={sort} onChange={event => { setSort(event.target.value); setPage(1); }}><option value="newest">{t('reviews.newest')}</option><option value="oldest">{t('reviews.oldest')}</option><option value="highest">{t('reviews.highest')}</option><option value="lowest">{t('reviews.lowest')}</option></select></>
      </div>
    </div>
    {error && <div className="api-error" role="alert">{error}</div>}
    {loading ? <div className="reviews-loading" aria-live="polite"><i/><i/><i/></div> : items.length ? <div className="review-card-grid">{items.map((review, index) => <ReviewCard key={review.id} review={review} index={index} moderation={isAdmin} onModerate={moderate}/>)}</div> : <div className="review-empty-state"><span aria-hidden="true">✳</span><h3>{isAdmin && moderationFilter === 'pending' ? t('reviews.caughtUp') : t('reviews.firstReviewAhead')}</h3><p>{isAdmin && moderationFilter === 'pending' ? t('reviews.noPending') : t('reviews.publicEmpty')}</p></div>}
    {!loading && pages > 1 && <nav className="review-pagination" aria-label={t('reviews.pages')}><button type="button" onClick={() => setPage(value => Math.max(1, value - 1))} disabled={page === 1}>← {t('reviews.previous')}</button><span>{t('reviews.page')} {page} / {pages}</span><button type="button" onClick={() => setPage(value => Math.min(pages, value + 1))} disabled={page === pages}>{t('reviews.next')} →</button></nav>}
    </section>
  </div>;
}
