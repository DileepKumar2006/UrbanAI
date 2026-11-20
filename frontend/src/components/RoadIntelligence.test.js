import React from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import RoadIntelligence from './RoadIntelligence';

describe('Predictive Road Intelligence Phase 6A', () => {
  let container;
  let root;
  const section = {
    id: 1, section_code: 'RS-01', road_name: 'Survey Road', ward_zone: null, road_type: null,
    length_meters: null, latitude: null, longitude: null, notes: null,
    created_at: '2026-11-20T12:00:00', updated_at: '2026-11-20T12:00:00',
    record_counts: { conditions: 0, inspections: 0, weather: 0, traffic: 0, maintenance: 0, images: 0 },
  };

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    global.fetch = jest.fn(async (url, options = {}) => {
      if (options.method === 'POST') return { ok: true, json: async () => ({ success: true, item: section }) };
      if (String(url).endsWith('/api/road-sections/1')) {
        return { ok: true, json: async () => ({ success: true, item: { ...section, conditions: [], inspections: [], weather_observations: [], traffic_observations: [], maintenance_records: [], images: [] } }) };
      }
      return { ok: true, json: async () => ({ success: true, items: [], total: 0 }) };
    });
    container = document.createElement('div');
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (root) act(() => root.unmount());
    container.remove();
  });

  test('shows prediction unavailable and does not invent road risk values', async () => {
    await act(async () => {
      root = createRoot(container);
      root.render(<RoadIntelligence adminKey="staff-key"/>);
    });
    expect(container.textContent).toContain('Prediction unavailable');
    expect(container.textContent).toContain('No sections have been entered.');
    expect(container.textContent).toContain('No measurements or risk percentages are generated here.');
    expect(container.textContent).not.toMatch(/\b(?:\d{1,3})% risk\b/i);
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/road-sections'), expect.objectContaining({
      headers: expect.objectContaining({ 'X-Admin-Key': 'staff-key' }),
    }));
  });

  test('submits a road section using entered metadata only', async () => {
    global.fetch = jest.fn(async (url, options = {}) => {
      if (options.method === 'POST') return { ok: true, json: async () => ({ success: true, item: section }) };
      if (String(url).endsWith('/api/road-sections/1')) {
        return { ok: true, json: async () => ({ success: true, item: { ...section, conditions: [], inspections: [], weather_observations: [], traffic_observations: [], maintenance_records: [], images: [] } }) };
      }
      return { ok: true, json: async () => ({ success: true, items: [section], total: 1 }) };
    });
    await act(async () => {
      root = createRoot(container);
      root.render(<RoadIntelligence adminKey="staff-key"/>);
    });
    const form = container.querySelector('.road-intel-sidebar form');
    form.elements.section_code.value = 'RS-01';
    form.elements.road_name.value = 'Survey Road';
    await act(async () => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    const [url, options] = global.fetch.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(url).toContain('/api/road-sections');
    expect(JSON.parse(options.body)).toMatchObject({ section_code: 'RS-01', road_name: 'Survey Road' });
    expect(container.textContent).toContain('Road section RS-01 added.');
  });
});
