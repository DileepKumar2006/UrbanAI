import '../i18n';
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

export default function CivicIntelligence({ api, authToken, adminKey, onToast }) {
  const { t } = useTranslation();
  const [analytics, setAnalytics] = useState(null);
  const [routes, setRoutes] = useState([]);
  const [model, setModel] = useState(null);
  const [busyCategory, setBusyCategory] = useState('');
  const [error, setError] = useState('');
  const headers = { ...(authToken ? { Authorization: `Bearer ${authToken}` } : adminKey ? { 'X-Admin-Key': adminKey } : {}) };

  async function load() {
    setError('');
    const [duplicateResponse, routeResponse, modelResponse] = await Promise.all([
      fetch(`${api}/api/admin/duplicates/analytics`, { headers, cache: 'no-store' }),
      fetch(`${api}/api/admin/department-routes`, { headers, cache: 'no-store' }),
      fetch(`${api}/api/ai/model-info`, { cache: 'no-store' }),
    ]);
    const [duplicateData, routeData, modelData] = await Promise.all([
      duplicateResponse.json(), routeResponse.json(), modelResponse.json(),
    ]);
    if (!duplicateResponse.ok || !routeResponse.ok) {
      throw new Error(duplicateData.error || routeData.error || t('intelligence.loadError'));
    }
    setAnalytics(duplicateData);
    setRoutes(routeData.items || []);
    setModel(modelData.model || null);
  }

  useEffect(() => { load().catch(value => setError(value.message)); }, [api, authToken, adminKey]);

  async function saveRoute(event, category) {
    event.preventDefault();
    const department = new FormData(event.currentTarget).get('department')?.trim();
    if (!department) return;
    setBusyCategory(category);
    try {
      const response = await fetch(`${api}/api/admin/department-routes`, {
        method: 'PUT', headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, department }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || t('intelligence.saveError'));
      setRoutes(items => items.map(item => item.category === category ? result.item : item));
      onToast(t('intelligence.routeSaved'));
    } catch (value) { onToast(value.message); }
    finally { setBusyCategory(''); }
  }

  return <section className="intelligence-page">
    <header className="page-heading"><div><div className="eyebrow">{t('intelligence.eyebrow')}</div><h1>{t('intelligence.title')}</h1><p>{t('intelligence.subtitle')}</p></div>
      <button type="button" className="secondary-btn" onClick={() => load().catch(value => setError(value.message))}>{t('action.refresh')}</button></header>
    {error && <div className="api-error" role="alert">{error}</div>}

    <section className="intelligence-card"><div className="section-title"><div><h2>{t('intelligence.duplicates')}</h2><p>{t('intelligence.duplicatesHelp')}</p></div></div>
      {analytics ? <div className="stats-row">
        <IntelligenceStat label={t('intelligence.possibleMatches')} value={analytics.possible_duplicate_matches}/>
        <IntelligenceStat label={t('intelligence.relatedReports')} value={analytics.confirmed_related_reports}/>
        <IntelligenceStat label={t('intelligence.activeGroups')} value={analytics.active_issue_groups}/>
      </div> : <p>{t('ui.loading')}</p>}
      {!!analytics?.frequently_grouped_categories?.length && <div className="intelligence-breakdown"><h3>{t('intelligence.repeatedCategories')}</h3>{analytics.frequently_grouped_categories.map(item => <div key={item.category}><span>{item.category}</span><b>{item.reports}</b></div>)}</div>}
      {!!analytics?.frequently_grouped_locations?.length && <div className="intelligence-breakdown"><h3>{t('intelligence.repeatedAreas')}</h3>{analytics.frequently_grouped_locations.map(item => <div key={item.location}><span>{item.location}</span><b>{item.reports}</b></div>)}</div>}
      {analytics && analytics.active_issue_groups === 0 && <div className="empty-state"><h3>{t('intelligence.noGroups')}</h3><p>{t('intelligence.noGroupsHelp')}</p></div>}
    </section>

    <section className="intelligence-card"><div className="section-title"><div><h2>{t('intelligence.routing')}</h2><p>{t('intelligence.routingHelp')}</p><small className="route-workload-help">{t('intelligence.workloadHelp')}</small></div></div>
      <div className="route-settings-list">{routes.map(item => <form className="route-setting" key={item.category} onSubmit={event => saveRoute(event, item.category)}>
        <label><span>{item.category}</span><input name="department" defaultValue={item.department} placeholder="Roads Team A; Roads Team B" maxLength={120} required aria-label={`${t('intelligence.departmentFor')} ${item.category}`}/></label>
        <small>{item.custom ? t('intelligence.customRoute') : t('intelligence.defaultRoute')}</small><small className="route-workload">{(item.eligible_departments||[]).map(team=>`${team.name}: ${team.active_reports} ${t('intelligence.activeReports')}`).join(' · ')}</small>
        <button type="submit" className="secondary-btn" disabled={busyCategory === item.category}>{busyCategory === item.category ? t('ui.saving') : t('action.save')}</button>
      </form>)}</div>
    </section>

    <section className="intelligence-card model-status-card"><div className="section-title"><div><h2>{t('intelligence.aiModel')}</h2><p>{t('intelligence.aiModelHelp')}</p></div>
      <span className={`status ${model?.available ? 'status-completed' : 'status-pending'}`}>{model?.available ? t('intelligence.available') : t('intelligence.unavailable')}</span></div>
      {!model ? <p>{t('ui.loading')}</p> : <>
        <p>{model.message}</p>
        {model.architecture && <p><b>{t('intelligence.modelType')}:</b> {model.architecture} · <b>{t('intelligence.version')}:</b> {model.model_version || t('intelligence.unknown')}</p>}
        {model.trained_at && <p><b>{t('intelligence.trainedAt')}:</b> {model.trained_at}</p>}
        {model.dataset_reference && <p><b>{t('intelligence.datasetReference')}:</b> <code>{model.dataset_reference}</code></p>}
        {model.evaluation ? <pre className="model-evaluation">{JSON.stringify(model.evaluation, null, 2)}</pre> : <div className="empty-state"><p>{t('intelligence.noMeasuredMetrics')}</p></div>}
      </>}
    </section>
  </section>;
}

function IntelligenceStat({ label, value }) {
  return <article className="stat-card"><div className="stat-value">{Number(value || 0).toLocaleString()}</div><div className="stat-label">{label}</div></article>;
}
