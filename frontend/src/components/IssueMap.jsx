import '../i18n';
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import L from 'leaflet';
import { CircleMarker, MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet';
import MarkerClusterGroup from 'react-leaflet-cluster';
import 'leaflet/dist/leaflet.css';

const API=(process.env.REACT_APP_API_URL||(process.env.NODE_ENV==='production'?'':'http://localhost:5000')).replace(/\/$/,'');
const TILE_URL=process.env.REACT_APP_OSM_TILE_URL||'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const TILE_ATTRIBUTION=process.env.REACT_APP_OSM_TILE_ATTRIBUTION||'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const CATEGORIES=['Pothole / Road Damage','Garbage Overflow','Drainage / Sewage Problem','Broken Streetlight','Water Leakage','Road Obstruction','Other Civic Issues','Wet Waste','Dry Waste','Mixed Waste','Improper Waste Segregation','Overflowing Garbage','Uncollected Waste'];
const CATEGORY_CODE={'Pothole / Road Damage':'P','Garbage Overflow':'G','Drainage / Sewage Problem':'D','Broken Streetlight':'L','Water Leakage':'W','Road Obstruction':'R','Other Civic Issues':'C','Wet Waste':'W','Dry Waste':'D','Mixed Waste':'M','Improper Waste Segregation':'I','Overflowing Garbage':'O','Uncollected Waste':'U'};
const STATUS_COLOR={new_report:'#ea580c',pending:'#ea580c',assigned:'#7c3aed',in_progress:'#2563eb',completed:'#16a34a'};
const statusLabel=value=>({new_report:'New report',pending:'Pending',assigned:'Assigned',in_progress:'Work in progress',completed:'Completed'}[value]||value);
const when=value=>value?new Date(value).toLocaleString():'—';
const hasCoordinates=item=>Number.isFinite(Number(item.latitude))&&Number.isFinite(Number(item.longitude))&&Number(item.latitude)>=-90&&Number(item.latitude)<=90&&Number(item.longitude)>=-180&&Number(item.longitude)<=180;

function markerIcon(item){
 const color=item.is_escalated?'#dc2626':STATUS_COLOR[item.status]||'#64748b';
 const label=CATEGORY_CODE[item.category]||'•';
 return L.divIcon({className:'urban-map-marker-wrap',html:`<span class="urban-map-marker" style="--marker-color:${color}"><i>${label}</i></span>`,iconSize:[34,42],iconAnchor:[17,38],popupAnchor:[0,-34]});
}

function MapViewport({items,visible}){
 const map=useMap();
 useEffect(()=>{
  if(!visible)return undefined;
  const points=items.filter(hasCoordinates).map(item=>[Number(item.latitude),Number(item.longitude)]);
  if(points.length>1)map.fitBounds(points,{padding:[28,28],maxZoom:14});
  else if(points.length===1)map.setView(points[0],14);
  else map.setView([20.5937,78.9629],5);
  const timer=setTimeout(()=>map.invalidateSize(),80);
  return()=>clearTimeout(timer);
 },[items,map,visible]);
 return null;
}

export default function IssueMap({authToken='',adminKey='',onOpenComplaint=()=>{},refreshKey=0}){
 const {t}=useTranslation();
 const [filters,setFilters]=useState({search:'',category:'all',status:'all',priority:'all',department:'all',date_from:'',date_to:''});
 const [applied,setApplied]=useState({});
 const [view,setView]=useState('map');
 const [density,setDensity]=useState(false);
 const [items,setItems]=useState([]);
 const [hotspots,setHotspots]=useState([]);
 const [departments,setDepartments]=useState([]);
 const [total,setTotal]=useState(0);
 const [offset,setOffset]=useState(0);
 const [hasMore,setHasMore]=useState(false);
 const [missingCoordinates,setMissingCoordinates]=useState(0);
 const [approximate,setApproximate]=useState(true);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const [reloadKey,setReloadKey]=useState(0);

 const queryString=useMemo(()=>{
  const params=new URLSearchParams({limit:'300',offset:String(offset)});
  Object.entries(applied).forEach(([key,value])=>{if(value&&value!=='all')params.set(key,value)});
  return params.toString();
 },[applied,offset]);

 useEffect(()=>{setOffset(0);setItems([])},[refreshKey]);

 useEffect(()=>{
  let cancelled=false;
  async function load(){
   setLoading(true);setError('');
   const headers=authToken?{Authorization:`Bearer ${authToken}`} : adminKey?{'X-Admin-Key':adminKey}:{};
   try{
    const response=await fetch(`${API}/api/map/issues?${queryString}`,{headers});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||t('map.loadError'));
    if(cancelled)return;
    setItems(current=>offset===0?result.items||[]:[...current,...(result.items||[])]);
    setHotspots(result.hotspots||[]);setDepartments(result.departments||[]);setTotal(result.total||0);
    setHasMore(Boolean(result.has_more));setMissingCoordinates(result.missing_coordinates||0);
    setApproximate(!result.precise_coordinates);setError('');
   }catch(reason){if(!cancelled)setError(reason.message||t('map.unavailable'))}finally{if(!cancelled)setLoading(false)}
  }
  load();
  return()=>{cancelled=true};
 },[queryString,authToken,adminKey,refreshKey,offset,reloadKey]);

 function updateFilter(event){setFilters(current=>({...current,[event.target.name]:event.target.value}))}
 function applyFilters(event){event.preventDefault();setOffset(0);setApplied({...filters})}
 function loadMore(){setOffset(value=>value+300)}

 return <section className="city-map-page">
  <div className="map-page-heading"><div><span className="eyebrow">{t('ui.cityOperations')} · GIS</span><h1>{t('map.title')}</h1><p>{t('map.subtitle')}</p></div><div className="map-view-toggle"><button className={view==='map'?'selected':''} onClick={()=>setView('map')}>{t('map.mapView')}</button><button className={view==='list'?'selected':''} onClick={()=>setView('list')}>{t('map.listView')}</button></div></div>
  <form className="map-filter-bar" onSubmit={applyFilters}>
   <label>{t('map.search')}<input name="search" value={filters.search} onChange={updateFilter} placeholder={t('map.searchPlaceholder')} maxLength="120"/></label>
   <label>{t('map.category')}<select name="category" value={filters.category} onChange={updateFilter}><option value="all">{t('map.allCategories')}</option>{CATEGORIES.map(value=><option key={value} value={value}>{t(`ui.category${({'Pothole / Road Damage':'Pothole','Garbage Overflow':'Garbage','Drainage / Sewage Problem':'Drainage','Broken Streetlight':'Streetlight','Water Leakage':'Water','Road Obstruction':'Obstruction','Other Civic Issues':'Other','Wet Waste':'Wet','Dry Waste':'Dry','Mixed Waste':'Mixed','Improper Waste Segregation':'Improper','Overflowing Garbage':'Overflow','Uncollected Waste':'Uncollected'})[value]}`,{defaultValue:value})}</option>)}</select></label>
   <label>{t('map.status')}<select name="status" value={filters.status} onChange={updateFilter}><option value="all">{t('map.allStatuses')}</option>{[['new_report','status.new_report'],['pending','status.pending'],['assigned','status.assigned'],['in_progress','status.in_progress'],['completed','status.completed']].map(([value,key])=><option value={value} key={value}>{t(key)}</option>)}</select></label>
   <label>{t('map.priority')}<select name="priority" value={filters.priority} onChange={updateFilter}><option value="all">{t('map.allPriorities')}</option><option value="high">{t('map.high')}</option><option value="medium">{t('map.medium')}</option><option value="low">{t('map.low')}</option></select></label>
   {departments.length>0&&<label>{t('map.department')}<select name="department" value={filters.department} onChange={updateFilter}><option value="all">{t('map.allDepartments')}</option>{departments.map(value=><option key={value}>{value}</option>)}</select></label>}
   <label>{t('map.from')}<input type="date" name="date_from" value={filters.date_from} onChange={updateFilter}/></label>
   <label>{t('map.to')}<input type="date" name="date_to" value={filters.date_to} onChange={updateFilter}/></label>
   <button className="primary-btn" type="submit">{t('map.apply')}</button>
  </form>
  <div className="map-summary-row"><b>{t('map.locatedCount',{count:total})}</b><span>{t('map.missingCoordinates',{count:missingCoordinates})}</span><button type="button" className={`text-button ${density?'density-active':''}`} onClick={()=>setDensity(value=>!value)}>{density?t('map.hideDensity'):t('map.showDensity')}</button></div>
  {approximate&&<div className="map-privacy-note">{t('map.privacyNote')}</div>}
  {error&&<div className="api-error map-error">{error}<button type="button" className="text-button" onClick={()=>setReloadKey(value=>value+1)}>Retry</button></div>}
  {loading&&<div className="map-loading">{t('map.loadingLocations')}</div>}
  {!loading&&!error&&total===0&&<div className="empty-state map-empty"><h3>{t('map.noLocatedMatches')}</h3><p>{t('map.noCoordinatesHelp')}</p></div>}
  <div className={`map-content-grid ${view==='list'?'list-only':''}`}>
   <div className="map-canvas"><MapContainer center={[20.5937,78.9629]} zoom={5} scrollWheelZoom className="urban-map">
    <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} maxZoom={19}/>
    <MapViewport items={items} visible={view==='map'}/>
    {!density&&<MarkerClusterGroup chunkedLoading showCoverageOnHover={false} maxClusterRadius={48}>
     {items.filter(hasCoordinates).map(item=><Marker key={item.id} position={[Number(item.latitude),Number(item.longitude)]} icon={markerIcon(item)}>
      <Popup><div className="map-popup"><div className="map-popup-ref">{item.reference}</div><h3>{t(`ui.category${({'Pothole / Road Damage':'Pothole','Garbage Overflow':'Garbage','Drainage / Sewage Problem':'Drainage','Broken Streetlight':'Streetlight','Water Leakage':'Water','Road Obstruction':'Obstruction','Other Civic Issues':'Other','Wet Waste':'Wet','Dry Waste':'Dry','Mixed Waste':'Mixed','Improper Waste Segregation':'Improper','Overflowing Garbage':'Overflow','Uncollected Waste':'Uncollected'})[item.category]}`,{defaultValue:item.category})}</h3><span className={`status status-${item.status}`}>{t(`status.${item.status}`,{defaultValue:item.status})}</span><p>{item.short_description||t('map.descriptionPrivate')}</p><dl><div><dt>{t('map.priorityLabel')}</dt><dd>{t(`map.${item.priority}`,{defaultValue:item.priority})}</dd></div><div><dt>{t('map.submitted')}</dt><dd>{when(item.created_at)}</dd></div>{item.assigned_department&&<div><dt>{t('map.department')}</dt><dd>{item.assigned_department}</dd></div>}{item.ai_suggested_category&&<div><dt>{t('map.aiSuggestion')}</dt><dd>{item.ai_suggested_category}</dd></div>}{item.location_approximate&&<div><dt>{t('map.location')}</dt><dd>{t('map.approximateArea')}</dd></div>}</dl>{item.can_open_details&&<button type="button" className="text-button" onClick={()=>onOpenComplaint(item.id)}>{t('map.openDetails')}</button>}</div></Popup>
     </Marker>)}
    </MarkerClusterGroup>}
    {density&&hotspots.map((cell,index)=><CircleMarker key={`${cell.latitude}-${cell.longitude}-${index}`} center={[cell.latitude,cell.longitude]} radius={Math.min(28,8+Math.sqrt(cell.count)*4)} pathOptions={{color:'#7c3aed',fillColor:'#8b5cf6',fillOpacity:0.42,weight:1}}><Popup><b>{t('map.complaintCount',{count:cell.count})}</b><br/>{t('map.cellReport')}</Popup></CircleMarker>)}
   </MapContainer><div className="map-legend"><b>{t('map.status')}</b><span><i style={{background:'#ea580c'}}/>{t('map.pending')}</span><span><i style={{background:'#2563eb'}}/>{t('status.in_progress')}</span><span><i style={{background:'#16a34a'}}/>{t('status.completed')}</span><span><i style={{background:'#dc2626'}}/>{t('map.escalated')}</span></div></div>
   <aside className="map-report-list"><div className="map-list-heading"><div><h2>{t('map.reportsInView')}</h2><p>{t('map.loadedCount',{loaded:items.length,total})}</p></div></div>{items.length===0?<div className="empty-state">{t('map.noReports')}</div>:items.map(item=><article className="map-list-card" key={item.id}><div className="map-list-top"><b>{item.reference}</b><span className={`status status-${item.status}`}>{t(`status.${item.status}`,{defaultValue:item.status})}</span></div><h3>{t(`ui.category${({'Pothole / Road Damage':'Pothole','Garbage Overflow':'Garbage','Drainage / Sewage Problem':'Drainage','Broken Streetlight':'Streetlight','Water Leakage':'Water','Road Obstruction':'Obstruction','Other Civic Issues':'Other','Wet Waste':'Wet','Dry Waste':'Dry','Mixed Waste':'Mixed','Improper Waste Segregation':'Improper','Overflowing Garbage':'Overflow','Uncollected Waste':'Uncollected'})[item.category]}`,{defaultValue:item.category})}</h3><p>{item.short_description||t('map.detailsPrivate')}</p><small>{item.location_approximate?t('map.approximateArea'):item.assigned_department||t('map.locationRecorded')} · {when(item.created_at)}</small>{item.can_open_details&&<button type="button" className="text-button" onClick={()=>onOpenComplaint(item.id)}>{t('map.openDetails')}</button>}</article>)}{hasMore&&<button type="button" className="primary-btn map-more" onClick={loadMore} disabled={loading}>{t('map.loadMore')}</button>}</aside>
  </div>
  <p className="map-footnote">{t('map.densityNote')} <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noreferrer">{t('map.reportIssue')}</a>.</p>
 </section>;
}
