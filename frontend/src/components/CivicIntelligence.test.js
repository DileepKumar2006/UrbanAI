import React from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot } from 'react-dom/client';
import CivicIntelligence from './CivicIntelligence';

describe('Civic Intelligence panel', () => {
  let root, container;
  const analytics={success:true,possible_duplicate_matches:3,confirmed_related_reports:2,active_issue_groups:1,frequently_grouped_categories:[],frequently_grouped_locations:[]};
  const routes={items:[{category:'Pothole / Road Damage',department:'Public Works Department',custom:false}]};
  const model={available:false,architecture:'MobileNetV3-Small',model_version:null,dataset_reference:null,evaluation:null,message:'No trained and validated classifier is installed.'};
  beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;container=document.createElement('div');document.body.appendChild(container);global.fetch=jest.fn(async url=>({ok:true,json:async()=>String(url).includes('duplicates')?analytics:String(url).includes('department-routes')?routes:{model}}));});
  afterEach(()=>{if(root)act(()=>root.unmount());container.remove();});
  test('shows live duplicate counts and truthfully reports a missing model',async()=>{await act(async()=>{root=createRoot(container);root.render(<CivicIntelligence api="" adminKey="admin" onToast={jest.fn()}/>)});expect(container.textContent).toContain('3');expect(container.textContent).toContain('No trained and validated classifier is installed.');expect(container.textContent).toContain('No measured evaluation results are installed.');expect(container.querySelectorAll('.route-setting')).toHaveLength(1);});
  test('saves a department route through the protected API',async()=>{const updated={category:'Pothole / Road Damage',department:'Roads and Infrastructure',custom:true};global.fetch=jest.fn(async(url,options={})=>{if(options.method==='PUT')return{ok:true,json:async()=>({success:true,item:updated})};return{ok:true,json:async()=>String(url).includes('duplicates')?analytics:String(url).includes('department-routes')?routes:{model}}});const onToast=jest.fn();await act(async()=>{root=createRoot(container);root.render(<CivicIntelligence api="" adminKey="admin" onToast={onToast}/>)});const form=container.querySelector('.route-setting');form.elements.department.value=updated.department;await act(async()=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));expect(global.fetch).toHaveBeenCalledWith('/api/admin/department-routes',expect.objectContaining({method:'PUT',headers:expect.objectContaining({'X-Admin-Key':'admin'})}));expect(onToast).toHaveBeenCalledWith('Department route saved.');});
});
