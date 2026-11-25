import React, { useEffect, useRef, useState } from 'react';
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
  const [ref, visible] = useReveal();
  return <article ref={ref} className={`review-card ${visible ? 'is-visible' : ''}`} style={{ '--review-delay': `${Math.min(index, 5) * 70}ms` }}>
    <div className="review-card-top"><RatingStars value={review.rating} size="small"/><span className="review-date">{review.created_at ? new Date(review.created_at).toLocaleDateString() : 'Date unavailable'}</span></div>
    <p className="review-copy">{review.review_text}</p>
    <div className="review-author-row"><span className="review-avatar" aria-hidden="true">{(review.author || 'C').slice(0, 1).toUpperCase()}</span><div><b>{review.author || 'Citizen'}</b><small>{moderation ? review.moderation_status : 'Nagrik citizen'}</small></div></div>
    {moderation && <div className="review-moderation-row">
      {review.moderation_status !== 'published' && <button type="button" className="review-mod-publish" onClick={() => onModerate(review, 'published')}>Publish review</button>}
      {review.moderation_status !== 'hidden' && <button type="button" className="review-mod-hide" onClick={() => onModerate(review, 'hidden')}>Hide review</button>}
      {review.user_id && <span className="review-private-id">Citizen #{review.user_id}</span>}
    </div>}
  </article>;
}

export default function ReviewsPage({ api, authToken, authUser, adminKey, onAccount, onToast }) {
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
      .then(async response => { const value = await response.json(); if (!response.ok) throw Error(value.error || 'Could not load reviews.'); return value; })
      .then(value => { setItems(value.items || []); setSummary({ average_rating: value.average_rating, total_reviews: value.total_reviews || 0, rating_distribution: value.rating_distribution || {} }); setPages(Math.max(1, value.pagination?.pages || 1)); setError(''); })
      .catch(value => { if (value.name !== 'AbortError') setError(value.message); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [api, authToken, adminKey, isAdmin, page, filterSearch, ratingFilter, sort, moderationFilter]);

  useEffect(() => {
    if (!isCitizen) { setMine(null); return undefined; }
    const controller = new AbortController();
    fetch(`${api}/api/reviews/mine`, { headers: { Authorization: `Bearer ${authToken}` }, signal: controller.signal })
      .then(async response => { const value = await response.json(); if (!response.ok) throw Error(value.error || 'Could not load your review.'); return value; })
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
    if (!rating) { setError('Choose a star rating before submitting.'); return; }
    setSubmitting(true); setError('');
    try {
      const response = await fetch(`${api}/api/reviews/mine`, {
        method: mine ? 'PUT' : 'POST',
        headers: { Authorization: `Bearer ${authToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, review_text: reviewText.trim() }),
      });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || 'Your review could not be saved.');
      setMine(value.review); setEditing(false); setPage(1); setFilterSearch(''); setRatingFilter('');
      onToast(value.review.moderation_status === 'pending' ? 'Review saved and sent for moderation.' : 'Review saved.');
    } catch (value) { setError(value.message); }
    finally { setSubmitting(false); }
  }

  async function deleteReview() {
    if (!window.confirm('Delete your website review? This cannot be undone.')) return;
    setSubmitting(true);
    try {
      const response = await fetch(`${api}/api/reviews/mine`, { method: 'DELETE', headers: { Authorization: `Bearer ${authToken}` } });
      const value = await response.json();
      if (!response.ok) throw Error(value.error || 'Your review could not be deleted.');
      setMine(null); setRating(0); setReviewText(''); setEditing(true); onToast('Your review was deleted.');
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
      if (!response.ok) throw Error(value.error || 'Moderation action failed.');
      setItems(current => moderationFilter === 'all' ? current.map(item => item.id === review.id ? value.review : item) : current.filter(item => item.id !== review.id));
      setSummary({ average_rating: value.average_rating, total_reviews: value.total_reviews, rating_distribution: value.rating_distribution });
      onToast(moderation_status === 'published' ? 'Review published.' : 'Review hidden from public view.');
    } catch (value) { setError(value.message); }
    finally { setSavingModeration(null); }
  }

  function applySearch(event) { event.preventDefault(); setPage(1); setFilterSearch(search); }
  const average = summary.average_rating;
  const liveRating = hoverRating || rating;

  return <div className="reviews-page">
    <header className="reviews-page-heading"><div><span className="landing-eyebrow">THE NAGRIK COMMUNITY</span><h1>Voices that help us improve.</h1><p>Share your experience with the Nagrik platform and hear from your community.</p></div>
      {isAdmin && <label className="review-admin-filter">Review status<select value={moderationFilter} onChange={event => { setModerationFilter(event.target.value); setPage(1); }}><option value="pending">Needs review</option><option value="published">Published</option><option value="hidden">Hidden</option><option value="all">All reviews</option></select></label>}
    </header>

    <section ref={summaryRef} className="review-summary-grid" aria-label="Published review summary">
      <div className="review-overview-card"><span className="review-card-eyebrow">COMMUNITY RATING</span>
        {average == null ? <><strong className="review-average-empty">— <small>/ 5</small></strong><p>No published reviews yet</p></> : <><strong className="review-average" style={{ '--rating-color': ratingColor(average) }}>{average.toFixed(1)}<small>/ 5</small></strong><RatingStars value={average} size="large"/><p>{summary.total_reviews} published {summary.total_reviews === 1 ? 'review' : 'reviews'}</p></>}
      </div>
      <div className="review-distribution-card"><div className="review-card-eyebrow">RATING DISTRIBUTION</div>{[5, 4, 3, 2, 1].map(stars => {
        const count = Number(summary.rating_distribution?.[String(stars)] || 0);
        const width = summary.total_reviews ? `${(count / summary.total_reviews) * 100}%` : '0%';
        return <div className="rating-distribution-row" key={stars}><span>{stars} <b aria-hidden="true">★</b></span><div className="rating-bar-track"><i style={{ width, backgroundColor: ratingColor(stars), transform: summaryVisible ? 'scaleX(1)' : 'scaleX(0)' }}/></div><span className="rating-count">{count}</span></div>;
      })}</div>
    </section>

    <section ref={composeRef} className={`review-compose-card ${composeVisible ? 'is-visible' : ''}`}>
      <div className="review-compose-intro"><span className="landing-eyebrow">YOUR EXPERIENCE MATTERS</span><h2>{mine && !editing ? 'Your Nagrik review' : 'How has Nagrik worked for you?'}</h2>
        {mine && !editing ? <><p>Your review is <b className={`moderation-label status-${mine.moderation_status}`}>{mine.moderation_status}</b>. {mine.moderation_status === 'pending' ? 'It will appear publicly after moderation.' : mine.moderation_status === 'hidden' ? 'It is currently hidden from the public review page.' : 'Thank you for sharing it with the community.'}</p></> : <p>Your review is separate from feedback about any individual complaint.</p>}
      </div>
      {isCitizen ? (mine && !editing ? <div className="review-mine-summary"><RatingStars value={mine.rating} size="medium"/><p>{mine.review_text}</p><div><button type="button" className="review-secondary-btn" onClick={() => { setEditing(true); setRating(mine.rating); setReviewText(mine.review_text); }}>Edit review</button><button type="button" className="review-delete-btn" onClick={deleteReview} disabled={submitting}>Delete review</button></div></div> : <form className="review-form" onSubmit={submitReview}>
        <div className="review-rating-field"><div><b>Your rating</b><small>{liveRating ? ['','Not good','Could be better','Good','Very good','Excellent'][liveRating] : 'Select 1 to 5 stars'}</small></div>
          <div className="review-rating-picker"><RatingStars value={liveRating} interactive onChange={setRating} onHover={setHoverRating}/><span className="review-rating-number" style={{ '--rating-color': ratingColor(liveRating) }}>{liveRating || '—'}<small>/ 5</small></span></div>
        </div>
        <label className="review-text-label">Your review<textarea value={reviewText} onChange={event => setReviewText(event.target.value.slice(0, 2000))} maxLength="2000" rows="4" placeholder="What felt useful? What could be clearer?" required/></label>
        <div className="review-form-footer"><small>{reviewText.length} / 2000 characters</small><div>{editing&&<button type="button" className="review-secondary-btn" onClick={() => { setEditing(false); setError(''); }}>Cancel</button>}<button className="primary-btn" disabled={submitting}>{submitting ? 'Saving…' : mine ? 'Save changes' : 'Submit review'}</button></div></div>
      </form>) : <div className="review-signin-prompt"><p>Sign in with a citizen account to write, edit, or delete your review.</p><button type="button" className="primary-btn" onClick={onAccount}>Sign in or create account</button></div>}
    </section>

    <section className="review-list-section"><div className="review-list-heading"><div><span className="landing-eyebrow">COMMUNITY NOTES</span><h2>{isAdmin ? 'Review moderation' : 'Published reviews'}</h2><p>{isAdmin ? 'Reviews stay private until they are published.' : 'Only reviews approved for public display appear here.'}</p></div>
      <div className="review-list-controls"><form className="review-search-form" onSubmit={applySearch}><label className="visually-hidden" htmlFor="review-search">Search reviews</label><input id="review-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search reviews" maxLength="100"/><button type="submit" aria-label="Search reviews">⌕</button></form>
        <label className="visually-hidden" htmlFor="review-rating-filter">Filter by rating</label><select id="review-rating-filter" value={ratingFilter} onChange={event => { setRatingFilter(event.target.value); setPage(1); }}><option value="">All ratings</option>{[5, 4, 3, 2, 1].map(stars => <option value={stars} key={stars}>{stars} stars</option>)}</select>
        <><label className="visually-hidden" htmlFor="review-sort">Sort reviews</label><select id="review-sort" value={sort} onChange={event => { setSort(event.target.value); setPage(1); }}><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="highest">Highest rated</option><option value="lowest">Lowest rated</option></select></>
      </div>
    </div>
    {error && <div className="api-error" role="alert">{error}</div>}
    {loading ? <div className="reviews-loading" aria-live="polite"><i/><i/><i/></div> : items.length ? <div className="review-card-grid">{items.map((review, index) => <ReviewCard key={review.id} review={review} index={index} moderation={isAdmin} onModerate={moderate}/>)}</div> : <div className="review-empty-state"><span aria-hidden="true">✳</span><h3>{isAdmin && moderationFilter === 'pending' ? 'You’re all caught up.' : 'The first review is still ahead.'}</h3><p>{isAdmin && moderationFilter === 'pending' ? 'New citizen reviews waiting for moderation will show up here.' : 'Once citizens share their experiences and reviews are published, they’ll appear here. No ratings are made up.'}</p></div>}
    {!loading && pages > 1 && <nav className="review-pagination" aria-label="Review pages"><button type="button" onClick={() => setPage(value => Math.max(1, value - 1))} disabled={page === 1}>← Previous</button><span>Page {page} of {pages}</span><button type="button" onClick={() => setPage(value => Math.min(pages, value + 1))} disabled={page === pages}>Next →</button></nav>}
    </section>
  </div>;
}
