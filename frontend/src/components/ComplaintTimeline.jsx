import '../i18n';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

const API=(process.env.REACT_APP_API_URL||(process.env.NODE_ENV==='production'?'':'http://localhost:5000')).replace(/\/$/,'');
const statusKey=value=>`status.${value}`;

export default function ComplaintTimeline({ issue, authToken, authUser, canManage, onUpdated, onToast }) {
  const { t, i18n } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [translation, setTranslation] = useState(issue.admin_translation || '');
  const [note, setNote] = useState('');
  const ownIssue=authUser?.role==='citizen' && issue.created_by===authUser.id;
  const locale=({en:'en-IN',kn:'kn-IN',ta:'ta-IN',hi:'hi-IN'})[i18n.resolvedLanguage]||'en-IN';
  const formatDate=value=>value?new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'—';

  async function verify(value) {
    setBusy(true);
    try {
      const response=await fetch(`${API}/api/issues/${issue.id}/verification`,{method:'POST',headers:{Authorization:`Bearer ${authToken}`,'Content-Type':'application/json'},body:JSON.stringify({verification:value,note})});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||'Could not save your resolution feedback.');
      onUpdated(result.issue); onToast(t(value==='resolved'?'timeline.thanksResolved':'timeline.unresolvedSaved')); setNote('');
    } catch(error) { onToast(error.message || t('timeline.verificationError')); } finally { setBusy(false); }
  }

  async function saveTranslation(event) {
    event.preventDefault(); setBusy(true);
    try {
      const response=await fetch(`${API}/api/issues/${issue.id}/translation`,{method:'PATCH',headers:{Authorization:`Bearer ${authToken}`,'Content-Type':'application/json'},body:JSON.stringify({translation})});
      const result=await response.json();
      if(!response.ok)throw new Error(result.error||'Could not save translation.');
      onUpdated(result.issue); onToast(t('timeline.translationSaved'));
    } catch(error) { onToast(error.message || t('timeline.translationError')); } finally { setBusy(false); }
  }

  return <section className="complaint-timeline" aria-label={t('timeline.title')}>
    <h3>{t('timeline.title')}</h3>
    <div className="timeline-events">{(issue.history||[]).map((event,index)=><article className="timeline-event" key={`${event.event_type}-${event.created_at}-${index}`}>
      <span className="timeline-marker" aria-hidden="true"/>
      <div className="timeline-event-content"><div className="timeline-event-head"><b>{event.event_type.replaceAll('_',' ')}</b>{event.new_status&&<span className={`status status-${event.new_status}`}>{t(statusKey(event.new_status),{defaultValue:event.new_status.replaceAll('_',' ')})}</span>}</div>
        <small>{formatDate(event.created_at)} · {event.actor_role||t('timeline.system')}</small>{event.details&&<p>{event.details}</p>}
        {event.image_url&&<img className="timeline-evidence" src={event.image_url.startsWith('http')?event.image_url:`${API}${event.image_url}`} alt={t('timeline.workEvidence')}/>}
      </div>
    </article>)}</div>
    {issue.description_language&&issue.description_language!=='en'&&<div className="translation-panel"><h4>{t('timeline.original')} · {issue.description_language.toUpperCase()}</h4><p>{issue.description}</p>{issue.admin_translation&&<><h4>{t('timeline.translation')}</h4><p>{issue.admin_translation}</p></>}
      {canManage&&<form onSubmit={saveTranslation}><label htmlFor="admin-translation">{t('timeline.translation')}</label><textarea id="admin-translation" value={translation} onChange={event=>setTranslation(event.target.value)} maxLength={5000} required/><button className="primary-btn" disabled={busy}>{t('timeline.saveTranslation')}</button><small>{t('timeline.staffTranslationHelp')}</small></form>}
    </div>}
    {ownIssue&&issue.status==='completed'&&<div className="verification-panel"><h4>{t('timeline.verify')}</h4><p>{t('timeline.verifyHelp')}</p><textarea value={note} onChange={event=>setNote(event.target.value)} maxLength={1000} placeholder={t('timeline.optionalNote')}/>
      <div className="verification-actions"><button className="primary-btn" disabled={busy} onClick={()=>verify('resolved')}>{t('action.verifyResolved')}</button><button className="secondary-btn" disabled={busy} onClick={()=>verify('still_unresolved')}>{t('action.verifyUnresolved')}</button></div>
      {issue.verification_status&&issue.verification_status!=='pending'&&<small>{t(statusKey(issue.verification_status),{defaultValue:issue.verification_status})} · {formatDate(issue.verified_at)}</small>}
    </div>}
  </section>;
}
