import React, { useEffect, useMemo, useState } from 'react';
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
    if(!response.ok)throw new Error(result.error||'Could not load complaint locations.');
    if(cancelled)return;
    setItems(current=>offset===0?result.items||[]:[...current,...(result.items||[])]);
    setHotspots(result.hotspots||[]);setDepartments(result.departments||[]);setTotal(result.total||0);
    setHasMore(Boolean(result.has_more));setMissingCoordinates(result.missing_coordinates||0);
    setApproximate(!result.precise_coordinates);setError('');
   }catch(reason){if(!cancelled)setError(reason.message||'Map data is unavailable.')}finally{if(!cancelled)setLoading(false)}
  }
  load();
  return()=>{cancelled=true};
 },[queryString,authToken,adminKey,refreshKey,offset,reloadKey]);

 function updateFilter(event){setFilters(current=>({...current,[event.target.name]:event.target.value}))}
 function applyFilters(event){event.preventDefault();setOffset(0);setApplied({...filters})}
 function loadMore(){setOffset(value=>value+300)}

 return <section className="city-map-page">
  <div className="map-page-heading"><div><span className="eyebrow">CITY OPERATIONS · GIS</span><h1>City complaint map</h1><p>Locations and status from real UrbanAI complaint records.</p></div><div className="map-view-toggle"><button className={view==='map'?'selected':''} onClick={()=>setView('map')}>Map view</button><button className={view==='list'?'selected':''} onClick={()=>setView('list')}>List view</button></div></div>
  <form className="map-filter-bar" onSubmit={applyFilters}>
   <label>Search<input name="search" value={filters.search} onChange={updateFilter} placeholder="Category or location" maxLength="120"/></label>
   <label>Category<select name="category" value={filters.category} onChange={updateFilter}><option value="all">All categories</option>{CATEGORIES.map(value=><option key={value}>{value}</option>)}</select></label>
   <label>Status<select name="status" value={filters.status} onChange={updateFilter}><option value="all">All statuses</option>{[['new_report','New report'],['pending','Pending'],['assigned','Assigned'],['in_progress','Work in progress'],['completed','Completed']].map(([value,label])=><option value={value} key={value}>{label}</option>)}</select></label>
   <label>Priority<select name="priority" value={filters.priority} onChange={updateFilter}><option value="all">All priorities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
   {departments.length>0&&<label>Department<select name="department" value={filters.department} onChange={updateFilter}><option value="all">All departments</option>{departments.map(value=><option key={value}>{value}</option>)}</select></label>}
   <label>From<input type="date" name="date_from" value={filters.date_from} onChange={updateFilter}/></label>
   <label>To<input type="date" name="date_to" value={filters.date_to} onChange={updateFilter}/></label>
   <button className="primary-btn" type="submit">Apply filters</button>
  </form>
  <div className="map-summary-row"><b>{total} located report{total===1?'':'s'}</b><span>{missingCoordinates} report{missingCoordinates===1?'':'s'} without usable GPS location are not mapped</span><button type="button" className={`text-button ${density?'density-active':''}`} onClick={()=>setDensity(value=>!value)}>{density?'Hide':'Show'} complaint density</button></div>
  {approximate&&<div className="map-privacy-note">Public locations are rounded to about 1 km for privacy. Exact residential coordinates are shown only to administrators, report owners, and the assigned department.</div>}
  {error&&<div className="api-error map-error">{error}<button type="button" className="text-button" onClick={()=>setReloadKey(value=>value+1)}>Retry</button></div>}
  {loading&&<div className="map-loading">Loading complaint locations…</div>}
  {!loading&&!error&&total===0&&<div className="empty-state map-empty"><h3>No located reports match these filters</h3><p>Reports without GPS coordinates remain in the complaint dashboard but cannot be placed on the map.</p></div>}
  <div className={`map-content-grid ${view==='list'?'list-only':''}`}>
   <div className="map-canvas"><MapContainer center={[20.5937,78.9629]} zoom={5} scrollWheelZoom className="urban-map">
    <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} maxZoom={19}/>
    <MapViewport items={items} visible={view==='map'}/>
    {!density&&<MarkerClusterGroup chunkedLoading showCoverageOnHover={false} maxClusterRadius={48}>
     {items.filter(hasCoordinates).map(item=><Marker key={item.id} position={[Number(item.latitude),Number(item.longitude)]} icon={markerIcon(item)}>
      <Popup><div className="map-popup"><div className="map-popup-ref">{item.reference}</div><h3>{item.category}</h3><span className={`status status-${item.status}`}>{statusLabel(item.status)}</span><p>{item.short_description||'Description is available to authorized viewers.'}</p><dl><div><dt>Priority</dt><dd>{item.priority}</dd></div><div><dt>Submitted</dt><dd>{when(item.created_at)}</dd></div>{item.assigned_department&&<div><dt>Department</dt><dd>{item.assigned_department}</dd></div>}{item.ai_suggested_category&&<div><dt>AI suggestion</dt><dd>{item.ai_suggested_category}</dd></div>}{item.location_approximate&&<div><dt>Location</dt><dd>Approximate area</dd></div>}</dl>{item.can_open_details&&<button type="button" className="text-button" onClick={()=>onOpenComplaint(item.id)}>Open complaint details →</button>}</div></Popup>
     </Marker>)}
    </MarkerClusterGroup>}
    {density&&hotspots.map((cell,index)=><CircleMarker key={`${cell.latitude}-${cell.longitude}-${index}`} center={[cell.latitude,cell.longitude]} radius={Math.min(28,8+Math.sqrt(cell.count)*4)} pathOptions={{color:'#7c3aed',fillColor:'#8b5cf6',fillOpacity:0.42,weight:1}}><Popup><b>{cell.count} complaint{cell.count===1?'':'s'}</b><br/>Reported in this approximate map cell</Popup></CircleMarker>)}
   </MapContainer><div className="map-legend"><b>Status</b><span><i style={{background:'#ea580c'}}/>Pending / new</span><span><i style={{background:'#2563eb'}}/>In progress</span><span><i style={{background:'#16a34a'}}/>Completed</span><span><i style={{background:'#dc2626'}}/>Escalated</span></div></div>
   <aside className="map-report-list"><div className="map-list-heading"><div><h2>Reports in view</h2><p>{items.length} loaded of {total}</p></div></div>{items.length===0?<div className="empty-state">No reports to list.</div>:items.map(item=><article className="map-list-card" key={item.id}><div className="map-list-top"><b>{item.reference}</b><span className={`status status-${item.status}`}>{statusLabel(item.status)}</span></div><h3>{item.category}</h3><p>{item.short_description||'Details visible to authorized viewers.'}</p><small>{item.location_approximate?'Approximate map area':item.assigned_department||'Location recorded'} · {when(item.created_at)}</small>{item.can_open_details&&<button type="button" className="text-button" onClick={()=>onOpenComplaint(item.id)}>Open complaint details →</button>}</article>)}{hasMore&&<button type="button" className="primary-btn map-more" onClick={loadMore} disabled={loading}>Load more reports</button>}</aside>
  </div>
  <p className="map-footnote">Density shows the concentration of submitted complaints, not the true severity or full extent of city problems. Base map: OpenStreetMap contributors. <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noreferrer">Report a map issue</a>.</p>
 </section>;
}
