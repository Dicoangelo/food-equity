/* A bounded projection of visible state. Reading this object never fetches or mutates.
 * Browser-provided facts are presentation context, never eligibility authority.
 */
const ViewContext = (() => {
  let offerRevision=0, viewRevision=0, evaluatedOfferRevision=null, receivedAt=null;
  let notify=null, expiryTimer=null;
  const id = value => (typeof value === 'string' || Number.isSafeInteger(value))
    && /^[A-Za-z0-9_.:-]{1,100}$/.test(String(value)) ? String(value) : null;
  const text = (value,max) => typeof value === 'string' && value.trim() ? value.trim().slice(0,max) : null;
  const numeric = (value,min,max) => Number.isFinite(value) && value>=min && value<=max ? value : null;
  const timestamp = value => typeof value === 'string' && value.length<=40 && /(Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value)) ? value : null;
  const evidence = value => Array.isArray(value) ? value.filter(item=>typeof item === 'string').slice(0,3).map(item=>item.slice(0,180)) : [];
  const statuses=['none','success','draft','no_viable_match','no_match','blocked'];
  function candidate(row) {
    const identifier=id(row?.recipient_id);
    if (!identifier) return null;
    const shortened=['reason_codes','elimination_reasons','confirmations_required','acceptance_evidence'].some(key=>
      Array.isArray(row[key]) && (row[key].length>3 || row[key].some(value=>typeof value==='string'&&value.length>180)));
    const confirmations=evidence(row.confirmations_required);
    if(shortened) confirmations.push('Evidence excerpts were shortened for this snapshot. Inspect the full profile before deciding.');
    return {recipient_id:identifier,name:text(row.name,160)||'Unknown recipient',
      constraint_status:['eligible','needs_confirmation','rejected','not_evaluated'].includes(row._decision) ? row._decision : 'not_evaluated',
      profile_source:['generated','planted'].includes(row.profile_source) ? row.profile_source : 'unknown',
      city:text(row.city,160),cold_storage:text(row.cold_storage,40),max_pallets:numeric(row.max_pallets,0,1000000),
      pickup_window_hours:numeric(row.pickup_window_hours,0,10000),time_margin_hours:numeric(row.time_margin_hours,-10000,10000),
      composite_score:row._decision==='eligible'?numeric(row.composite_score,0,1):null,
      reason_codes:evidence(row.reason_codes),elimination_reasons:evidence(row.elimination_reasons),
      confirmations_required:confirmations,acceptance_evidence:evidence(row.acceptance_evidence)};
  }
  function allRows() { return typeof RecipientExplorer!=='undefined'?RecipientExplorer.mergedRecipients():[]; }
  function resultProjection() {
    const result=currentPayload;
    if (!result || evaluatedOfferRevision!==offerRevision) {
      const selected=typeof RecipientExplorer!=='undefined'?id(RecipientExplorer.selectedId()):null;
      const profile=allRows().find(row=>id(row.recipient_id)===selected && row._decision==='not_evaluated');
      return {status:'none',winner_id:null,alternative_ids:[],evaluated_at:null,deadline_at:null,
        candidates:profile?[candidate(profile)]:[],candidates_truncated:false};
    }
    const state=statuses.includes(result.status)?result.status:'blocked';
    const all=allRows();
    const selected=typeof RecipientExplorer!=='undefined'?id(RecipientExplorer.selectedId()):null;
    // Prioritize actual proposals, then the selected profile, then remaining evaluated rows.
    const order=[id(result.winner?.recipient_id),...(result.runners_up||[]).slice(0,2).map(row=>id(row.recipient_id)),selected].filter(Boolean);
    const ordered=[...order.map(key=>all.find(row=>id(row.recipient_id)===key)),...all.filter(row=>row._decision!=='not_evaluated')].filter(Boolean);
    const unique=new Map();
    ordered.forEach(row=>{const value=candidate(row);if(value&&!unique.has(value.recipient_id))unique.set(value.recipient_id,value);});
    const candidates=[...unique.values()].slice(0,16);
    const eligible=key=>key && candidates.some(row=>row.recipient_id===key && row.constraint_status==='eligible');
    const hasProposal=['success','draft'].includes(state);
    const winner=id(result.winner?.recipient_id);
    const alternatives=[...new Set((result.runners_up||[]).slice(0,2).map(row=>id(row.recipient_id)))].filter(key=>key!==winner&&eligible(key));
    const projection={status:state,winner_id:hasProposal&&eligible(winner)?winner:null,
      alternative_ids:hasProposal?alternatives:[],evaluated_at:timestamp(result.evaluated_at||result.winner?.evaluated_at),
      deadline_at:timestamp(result.donor?.deadline_at||result.deadline_at||result.winner?.deadline_at),
      candidates,candidates_truncated:unique.size>candidates.length};
    // Reserve room for filters, identifiers and source envelopes within the wire budget.
    while(new TextEncoder().encode(JSON.stringify(projection)).byteLength>32000 && projection.candidates.length) {
      const removed=projection.candidates.pop().recipient_id;
      projection.candidates_truncated=true;
      projection.alternative_ids=projection.alternative_ids.filter(key=>key!==removed);
      if(projection.winner_id===removed) projection.winner_id=null;
    }
    return projection;
  }
  function sources() {
    const states=SourceStateUI.cityStates(cityPayload);
    const result=resultProjection();
    if (result.status!=='none') states.push(SourceStateUI.normalize({
      source_id:'match:current',source_kind:'match_result',fetched_at:receivedAt,
      expires_at:result.deadline_at,status:result.status==='blocked'?'unavailable':result.status==='draft'||result.candidates_truncated?'partial':'ready',
      cache_state:'none',provenance:'unknown',coverage:{returned:result.candidates.length,row_cap:16,
        matching_source_rows:null,truncated:result.candidates_truncated,skipped_invalid_or_duplicate:0},
      error_code:result.status==='blocked'?'data_unavailable':null,
    }));
    return states.slice(0,8);
  }
  function recipientSource(row) {
    const kind=row?._decision && row._decision!=='not_evaluated'?'match_result':'city_recipients';
    const base=sources().find(state=>state.source_kind===kind)||{status:'unavailable',error_code:'missing_source'};
    return SourceStateUI.normalize({...base,source_id:`recipient:${id(row?.recipient_id)||'unknown'}`,
      source_kind:'recipient_profile',provenance:['generated','planted'].includes(row?.profile_source)?row.profile_source:'unknown'});
  }
  function snapshot() {
    const visible=typeof RecipientExplorer!=='undefined'?RecipientExplorer.visibleRecipients():[];
    const identifiers=[...new Set(visible.map(row=>id(row.recipient_id)).filter(Boolean))];
    const value=(element,allowed)=>{const raw=document.getElementById(element)?.value;return allowed.includes(raw)?raw:allowed[0];};
    let position=null;
    if(map?.getCenter&&map?.getZoom) {
      const center=map.getCenter(),zoom=map.getZoom();
      if(numeric(center.lat,-90,90)!==null&&numeric(center.lng,-180,180)!==null&&numeric(zoom,0,22)!==null)
        position={latitude:center.lat,longitude:center.lng,zoom};
    }
    const result={schema_version:1,offer_revision:offerRevision,view_revision:viewRevision,evaluated_offer_revision:evaluatedOfferRevision,
      selected_recipient_id:typeof RecipientExplorer!=='undefined'?id(RecipientExplorer.selectedId()):null,
      visible_recipient_ids:identifiers.slice(0,50),visible_count:identifiers.length,truncated:identifiers.length>50,
      filters:{outcome:value('outcome-filter',['all','eligible','needs_confirmation','rejected','not_evaluated']),
        provenance:value('provenance-filter',['all','generated','planted','unknown']),storage:value('capability-filter',['all','cold','ambient','frozen']),
        search:(document.getElementById('recipient-search')?.value||'').trim().slice(0,100)},
      map:position,result:resultProjection(),sources:sources()};
    while(new TextEncoder().encode(JSON.stringify(result)).byteLength>48000 && result.result.candidates.length) {
      const removed=result.result.candidates.pop().recipient_id;
      result.result.candidates_truncated=true;
      result.result.alternative_ids=result.result.alternative_ids.filter(key=>key!==removed);
      if(result.result.winner_id===removed) result.result.winner_id=null;
      result.sources=result.sources.map(state=>state.source_kind==='match_result'?SourceStateUI.normalize({...state,
        status:state.status==='ready'?'partial':state.status,coverage:{...state.coverage,returned:result.result.candidates.length,truncated:true}}):state);
    }
    return result;
  }
  function changed(kind='view') {
    if(kind==='offer') { offerRevision+=1; evaluatedOfferRevision=null; receivedAt=null; }
    if(kind==='result') { evaluatedOfferRevision=offerRevision; receivedAt=new Date().toISOString(); }
    if(kind==='clear_result') { evaluatedOfferRevision=null; receivedAt=null; }
    viewRevision+=1;
    if(typeof ViewActionReceipts!=='undefined') ViewActionReceipts.invalidateEvidence();
    renderSources();
    scheduleExpiry();
    notify?.();
  }
  function renderSources() {
    SourceStateUI.render(document.getElementById('source-state-summary'),sources());
  }
  function scheduleExpiry() {
    if(expiryTimer) clearTimeout(expiryTimer);
    expiryTimer=null;
    const future=sources().map(state=>Date.parse(state.expires_at)).filter(value=>Number.isFinite(value)&&value>Date.now());
    if(future.length) expiryTimer=setTimeout(()=>{
      expiryTimer=null;changed('freshness');
      if(typeof RecipientExplorer!=='undefined') RecipientExplorer.refresh();
    },Math.min(2147483647,Math.max(10,Math.min(...future)-Date.now()+10)));
  }
  return {snapshot,sources,recipientSource,changed,renderSources,start:()=>{renderSources();scheduleExpiry();},
    dispose:()=>{if(expiryTimer)clearTimeout(expiryTimer);expiryTimer=null;notify=null;},onChange:callback=>{notify=callback;}};
})();

/* UI receipts have the same session/turn/view ownership as the server tools. */
const ViewActionReceipts = (() => {
  let owner=null, waiting=false, evidenceVisible=false;
  const calls=new Set();
  const panel=()=>document.getElementById('voice-action-receipt');
  const safeText=(value,max=300)=>typeof value==='string'?value.slice(0,max):'Unknown';
  function renderEvidence(target,detail) {
    const result=detail.reported_result;
    let profiles=[];
    if(detail.reported_recipient) {
      target.appendChild(textNode('h4','Selected profile evidence','font-semibold mt-2'));
      profiles=[detail.reported_recipient];
    } else if(Array.isArray(detail.reported_alternatives)) {
      target.appendChild(textNode('h4','Reported alternatives · existing order','font-semibold mt-2'));
      profiles=detail.reported_alternatives.slice(0,2);
      if(!profiles.length) target.appendChild(textNode('p','No alternatives are present in this display snapshot.'));
    } else if(result && typeof result==='object') {
      target.appendChild(textNode('h4',`Reported result: ${safeText(result.status,40).replaceAll('_',' ')}`,'font-semibold mt-2'));
      const candidates=Array.isArray(result.candidates)?result.candidates:[];
      const order=[result.winner_id,...(Array.isArray(result.alternative_ids)?result.alternative_ids.slice(0,2):[])].filter(Boolean);
      profiles=order.length?order.map(id=>candidates.find(row=>row.recipient_id===id)).filter(Boolean):candidates.slice(0,3);
      if(!result.winner_id) target.appendChild(textNode('p','No eligible winner is reported. Review the missing facts or rejected constraints below.'));
    }
    profiles.forEach((row,index)=>{
      const block=document.createElement('div');
      block.className='border-t border-line mt-2 pt-2';
      const role=detail.reported_alternatives?`Alternative ${index+1}`:result?.winner_id===row.recipient_id?'Reported winner'
        :result?.alternative_ids?.includes(row.recipient_id)?'Reported alternative':'Profile';
      block.appendChild(textNode('p',`${role}: ${safeText(row.name,160)} · ID ${safeText(row.recipient_id,100)}`,'font-semibold'));
      const labels={eligible:'Eligible proposal',needs_confirmation:'Needs confirmation',rejected:'Rejected for this offer',not_evaluated:'Not evaluated for this offer'};
      const provenance=['generated','planted'].includes(row.profile_source)?row.profile_source:'unknown';
      block.appendChild(textNode('p',`${labels[row.constraint_status]||'Decision unknown'} · ${provenance} profile · availability unconfirmed.`));
      const reasons=[...(Array.isArray(row.elimination_reasons)?row.elimination_reasons:[]),
        ...(Array.isArray(row.confirmations_required)?row.confirmations_required:[]),
        ...(Array.isArray(row.reason_codes)?row.reason_codes:[])].filter(value=>typeof value==='string');
      if(reasons.length) {
        const list=document.createElement('ul');list.className='list-disc pl-4 mt-1';
        [...new Set(reasons)].slice(0,6).forEach(reason=>list.appendChild(textNode('li',safeText(reason))));
        block.appendChild(list);
        if(reasons.length>6||reasons.some(reason=>reason.length>300))
          block.appendChild(textNode('p','Reason excerpts are shortened; inspect the full loaded profile before deciding.'));
      }
      target.appendChild(block);
    });
    const states=Array.isArray(detail.sources)?detail.sources.slice(0,8).map(state=>SourceStateUI.normalize(state)):[];
    target.appendChild(textNode('p',`Source status: ${SourceStateUI.aggregate(states)}. Source observation dates are unknown; operations remain unconfirmed.`,'mt-2'));
    if(states.length) {
      const sources=document.createElement('details');
      sources.appendChild(textNode('summary','Source evidence'));
      states.forEach(state=>sources.appendChild(textNode('p',SourceStateUI.describe(state),'mt-2')));
      target.appendChild(sources);
    }
  }
  const render=(message,detail)=>{
    const target=panel();if(!target)return;
    evidenceVisible=!!detail;
    target.replaceChildren(textNode('p',message));
    if(detail) {
      renderEvidence(target,detail);
      const serialized=JSON.stringify(detail,null,2);
      const technical=document.createElement('details');
      technical.className='mt-2';
      technical.appendChild(textNode('summary','Technical details · browser projection'));
      technical.appendChild(textNode('pre',serialized.slice(0,16000),'voice-transcript whitespace-pre-wrap break-words mt-2'));
      if(serialized.length>16000) technical.appendChild(textNode('p','This display excerpt was shortened. Inspect the loaded profiles for their full evidence.'));
      target.appendChild(technical);
    }
  };
  function validOwner(message) {
    const view=ViewContext.snapshot();
    return typeof message?.turn_id==='string'&&/^[A-Za-z0-9_.:-]{1,100}$/.test(message.turn_id)
      && message.expected_offer_revision===view.offer_revision && message.expected_view_revision===view.view_revision;
  }
  function begin(message) {
    if(owner || !validOwner(message)) { render('Turn ignored: the displayed view is no longer the one this response belongs to.');return false; }
    owner={turn_id:message.turn_id,expected_offer_revision:message.expected_offer_revision,expected_view_revision:message.expected_view_revision};
    waiting=false;return true;
  }
  function owns(message) {
    return !!owner && validOwner(message) && message.turn_id===owner.turn_id
      && message.expected_offer_revision===owner.expected_offer_revision && message.expected_view_revision===owner.expected_view_revision;
  }
  function accept(message) {
    if(!owns(message)) { render('Action ignored: its turn or view is stale. No form or result was changed.');return false; }
    if(typeof message.call_id!=='string'||message.call_id.length<1||message.call_id.length>128||calls.size>=30) {
      render('Action refused: a valid bounded call ID is required.');return false;
    }
    if(calls.has(message.call_id)) return false;
    calls.add(message.call_id);
    if(!['applied','refused','cancelled','stale'].includes(message.outcome)) { render('Action refused: an unknown outcome was returned.');return false; }
    if(message.outcome==='applied') {
      if(message.effect==='draft_proposed') {
        const draft=message.payload?.draft;
        if(message.resulting_revision!==draft?.draft_version || !renderVoiceDraft(draft)) {
          render('Draft proposal ignored: its revision is invalid or has already been superseded.');return false;
        }
        render('Draft proposal updated. Review and Apply it yourself; the offer form and match have not changed.');return true;
      }
      if(message.effect!=='read_only'||message.payload?.authority!=='untrusted_browser_projection'
          || message.resulting_revision!==owner.expected_view_revision) {
        render('Action refused: unsupported effect or missing display-context provenance.');return false;
      }
      render('Display evidence read. This is an unverified browser projection; operations remain unconfirmed. No new matching, data fetch or navigation occurred.',message.payload);
      return true;
    }
    render(`${message.outcome === 'refused' ? 'Action refused' : message.outcome === 'cancelled' ? 'Action cancelled' : 'Stale action ignored'}: ${String(message.message||'No change was applied.').slice(0,1000)}`);
    return true;
  }
  function finish(message) { if(!owns(message))return false;owner=null;waiting=false;return true; }
  function reset() { owner=null;waiting=false;calls.clear(); }
  function invalidateEvidence() {
    if(evidenceVisible) render('The workspace changed. Ask again for evidence from the current view.');
  }
  return {begin,owns,accept,finish,reset,invalidateEvidence,expectTurn:()=>{waiting=true;},busy:()=>waiting||!!owner,current:()=>!!owner&&validOwner(owner)};
})();
