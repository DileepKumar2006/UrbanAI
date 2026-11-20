import React from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import IssueMap from './IssueMap';

jest.mock('react-leaflet', () => {
  const ReactRuntime = require('react');
  return {
    MapContainer: ({ children, className }) => <div className={className} data-testid="leaflet-map">{children}</div>,
    TileLayer: ({ url }) => <div data-testid="tile-layer" data-url={url} />,
    Marker: ({ children, position }) => <div data-testid="complaint-marker" data-position={position.join(',')}>{children}</div>,
    CircleMarker: ({ children }) => <div data-testid="density-cell">{children}</div>,
    Popup: ({ children }) => <div>{children}</div>,
    useMap: () => ({ fitBounds: jest.fn(), setView: jest.fn(), invalidateSize: jest.fn() }),
  };
});

jest.mock('react-leaflet-cluster', () => ({ children }) => <div data-testid="marker-cluster">{children}</div>);

const report = {
  id: 17, reference: 'UP-0017', category: 'Pothole / Road Damage', issue_type: 'pothole',
  status: 'in_progress', priority: 'high', severity: 9, created_at: '2026-11-19T12:00:00',
  latitude: 12.35, longitude: 77.65, location_approximate: true, short_description: '',
  assigned_department: null, ai_suggested_category: null, can_open_details: false, is_escalated: false,
};

describe('IssueMap', () => {
  let container;
  let root;
  let requestUrls;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    requestUrls = [];
    global.fetch = jest.fn(url => {
      requestUrls.push(String(url));
      return Promise.resolve({
        ok: true,
        json: async () => ({ success: true, items: [report], total: 1, has_more: false,
          missing_coordinates: 0, precise_coordinates: false, departments: [],
          hotspots: [{ latitude: 12.34, longitude: 77.66, count: 2 }] }),
      });
    });
    container = document.createElement('div');
    document.body.appendChild(container);
  });

  afterEach(() => {
    if (root) act(() => root.unmount());
    container.remove();
  });

  async function settle(){ await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0))}); }

  test('loads real API reports, clusters markers, shows privacy and filters', async () => {
    await act(async()=>{root=createRoot(container);root.render(<IssueMap/>)});
    await settle();
    expect(container.querySelector('[data-testid="complaint-marker"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="marker-cluster"]')).not.toBeNull();
    expect(container.textContent).toContain('Public locations are rounded');
    expect(container.textContent).toContain('1 located report');
    expect(requestUrls[0]).toContain('/api/map/issues?');

    const category=container.querySelector('select[name="category"]');
    const valueSetter=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
    act(()=>{valueSetter.call(category,'Pothole / Road Damage');category.dispatchEvent(new Event('change',{bubbles:true}))});
    act(()=>container.querySelector('.map-filter-bar').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    await settle();
    expect(requestUrls.some(url=>url.includes('category=Pothole'))).toBe(true);
  });

  test('switches to list and renders real-data density cells', async () => {
    await act(async()=>{root=createRoot(container);root.render(<IssueMap/>)});
    await settle();
    const buttons=[...container.querySelectorAll('button')];
    act(()=>buttons.find(button=>button.textContent==='List view').click());
    expect(container.querySelector('.map-content-grid').classList.contains('list-only')).toBe(true);
    act(()=>[...container.querySelectorAll('button')].find(button=>button.textContent==='Show complaint density').click());
    expect(container.querySelector('[data-testid="density-cell"]')).not.toBeNull();
    expect(container.textContent).toContain('2 complaints');
  });

  test('offers authorized viewers a detail action from the marker popup', async () => {
    global.fetch=jest.fn(()=>Promise.resolve({ok:true,json:async()=>({success:true,items:[{...report,can_open_details:true,short_description:'Road surface issue'}],total:1,has_more:false,missing_coordinates:0,precise_coordinates:true,departments:[],hotspots:[]})}));
    const onOpenComplaint=jest.fn();
    await act(async()=>{root=createRoot(container);root.render(<IssueMap onOpenComplaint={onOpenComplaint}/>)});
    await settle();
    expect(container.textContent).toContain('Open complaint details');
    act(()=>[...container.querySelectorAll('button')].find(button=>button.textContent.includes('Open complaint details')).click());
    expect(onOpenComplaint).toHaveBeenCalledWith(17);
  });
});
