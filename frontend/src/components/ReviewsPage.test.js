import React from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import ReviewsPage from './ReviewsPage';

const response = (body, ok = true) => Promise.resolve({ ok, json: async () => body });
const emptySummary = { success: true, average_rating: null, total_reviews: 0, rating_distribution: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 }, items: [], pagination: { page: 1, pages: 0, total: 0 } };

describe('Nagrik reviews page', () => {
  let root;
  let container;
  let onToast;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    onToast = jest.fn();
    container = document.createElement('div');
    document.body.appendChild(container);
    global.fetch = jest.fn(url => url.includes('/api/reviews/mine')
      ? response({ success: true, review: null })
      : response(emptySummary));
  });

  afterEach(() => {
    if (root) act(() => root.unmount());
    container.remove();
  });

  test('submits a real citizen review and reports moderation state', async () => {
    const pendingReview = { id: 7, rating: 5, review_text: 'The report updates are clear and useful.', author: 'resident', moderation_status: 'pending', created_at: '2026-11-21T10:00:00' };
    global.fetch = jest.fn((url, options = {}) => {
      if (url.includes('/api/reviews/mine') && options.method === 'POST') return response({ success: true, review: pendingReview });
      if (url.includes('/api/reviews/mine')) return response({ success: true, review: null });
      return response(emptySummary);
    });
    await act(async () => {
      root = createRoot(container);
      root.render(<ReviewsPage api="" authToken="citizen-token" authUser={{ id: 5, username: 'resident', role: 'citizen' }} adminKey="" onAccount={jest.fn()} onToast={onToast}/>);
    });
    await act(async () => container.querySelector('[aria-label="5 stars"]').click());
    const textarea = container.querySelector('textarea');
    await act(async () => {
      textarea.value = pendingReview.review_text;
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent.includes('Submit review')).click());
    expect(global.fetch).toHaveBeenCalledWith('/api/reviews/mine', expect.objectContaining({ method: 'POST' }));
    expect(container.textContent).toContain('Pending');
    expect(container.textContent).toContain(pendingReview.review_text);
    expect(onToast).toHaveBeenCalledWith('Review saved and sent for moderation.');
  });

  test('admin can publish reviews through the protected moderation endpoint', async () => {
    const review = { id: 11, user_id: 3, rating: 4, review_text: 'A helpful civic platform.', author: 'moderator-visible-name', moderation_status: 'pending' };
    global.fetch = jest.fn((url, options = {}) => {
      if (url.includes('/api/admin/reviews/11/moderation')) return response({ success: true, review: { ...review, moderation_status: 'published' }, average_rating: 4, total_reviews: 1, rating_distribution: { 1: 0, 2: 0, 3: 0, 4: 1, 5: 0 } });
      if (url.includes('/api/admin/reviews')) return response({ ...emptySummary, items: [review], total_reviews: 0 });
      return response(emptySummary);
    });
    await act(async () => {
      root = createRoot(container);
      root.render(<ReviewsPage api="" authToken="" authUser={null} adminKey="admin-key" onAccount={jest.fn()} onToast={onToast}/>);
    });
    await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent.includes('Publish review')).click());
    expect(global.fetch).toHaveBeenCalledWith('/api/admin/reviews/11/moderation', expect.objectContaining({ method: 'PATCH' }));
    expect(onToast).toHaveBeenCalledWith('Review published.');
  });
});
