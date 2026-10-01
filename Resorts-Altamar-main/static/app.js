const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateText = value => new Date(value+'T12:00:00').toLocaleDateString('es-CL', {day:'numeric', month:'short', year:'numeric'});
const nightsBetween = (a, b) => Math.round((new Date(b+'T12:00:00') - new Date(a+'T12:00:00')) / 86400000);
const ROLE_NAMES = {cliente:'Cliente', recepcion:'Recepción', gerente:'Gerente'};
const ROLE_INTRO = {
  cliente: 'Reserva y gestiona tus estadías en la cadena Altamar.',
  recepcion: 'Registra huéspedes, crea reservas y administra la ocupación de la cadena.',
  gerente: 'Supervisa las reservas de la cadena y la actividad de seguridad.',
};
const AUDIT_NAMES = {
  inicio_sesion: 'Inicio de sesión', cierre_sesion: 'Cierre de sesión', acceso_fallido: 'Acceso fallido',
  acceso_bloqueado: 'Acceso bloqueado', registro_cliente: 'Registro de cliente', cliente_creado: 'Cliente creado por recepción',
  reserva_creada: 'Reserva creada', reserva_cancelada: 'Reserva cancelada',
  fechas_modificadas: 'Fechas modificadas', checkin: 'Check-in', checkout: 'Check-out',
  servicio_creado: 'Servicio creado', servicio_actualizado: 'Servicio actualizado', servicio_retirado: 'Servicio retirado',
  servicio_reactivado: 'Servicio reactivado', servicio_contratado: 'Servicio contratado', servicio_quitado: 'Servicio quitado',
};
const state = {user: null, rows: [], hotels: []};
const clp = n => '$' + Number(n||0).toLocaleString('es-CL');
const code = id => `ALT-${String(id).padStart(4,'0')}`;

function localDate(offset=0){const d=new Date();d.setDate(d.getDate()+offset);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function message(target, text='', type='ok'){const el=$(target);el.textContent=text;el.dataset.type=type;}
async function busy(button, task){button.disabled=true;button.setAttribute('aria-busy','true');try{return await task();}finally{button.disabled=false;button.removeAttribute('aria-busy');}}

async function api(path, data){
  const response = await fetch('/api/'+path, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
  let result = {};
  try { result = await response.json(); } catch { /* respuesta sin JSON */ }
  // Sesión vencida o ausente: se vuelve a la pantalla de acceso.
  if (response.status === 401 && !['login','me','register'].includes(path)) showAuth(result.error);
  if (!response.ok) {const error=new Error(result.error || 'No se pudo completar la operación.');error.alternatives=result.alternatives;throw error;}
  return result;
}

/* ---------- RUT (la validación definitiva la hace el servidor) ---------- */
function rutCheckDigit(body){let total=0,factor=2;for(const d of [...body].reverse()){total+=Number(d)*factor;factor=factor===7?2:factor+1;}const r=11-total%11;return r===11?'0':r===10?'K':String(r);}
function parseRut(value){
  const m=String(value).trim().toUpperCase().replace(/[.\s]/g,'').match(/^(\d{7,8})-?([\dK])$/);
  if(!m) return null;
  const body=m[1].replace(/^0+/,'');
  return body.length>=7 && rutCheckDigit(body)===m[2] ? {body, dv:m[2]} : null;
}
const formatRut = ({body, dv}) => body.replace(/\B(?=(\d{3})+(?!\d))/g,'.')+'-'+dv;
$$('.rut-input').forEach(input=>{
  const check=()=>input.setCustomValidity(!input.value.trim()||parseRut(input.value)?'':'Revisa el RUT: el dígito verificador no coincide.');
  input.addEventListener('input', check);
  input.addEventListener('blur', ()=>{const rut=parseRut(input.value);if(rut)input.value=formatRut(rut);check();});
});

/* ---------- Contraseñas: mostrar/ocultar y reglas en vivo ---------- */
$$('.reveal').forEach(button=>button.addEventListener('click',()=>{
  const input=document.getElementById(button.getAttribute('aria-controls'));
  const show=input.type==='password';
  input.type=show?'text':'password';
  button.setAttribute('aria-pressed',String(show));
  button.textContent=show?'Ocultar':'Mostrar';
  input.focus();
}));
$$('.password-rules-input').forEach(input=>{
  const rules=document.getElementById(input.getAttribute('aria-describedby'));
  input.addEventListener('input',()=>{
    const v=input.value;
    const ok={length:v.length>=8&&v.length<=64, letter:/\p{L}/u.test(v), digit:/\d/.test(v)};
    rules.querySelectorAll('li').forEach(li=>li.classList.toggle('ok',ok[li.dataset.rule]));
  });
});
// Mantiene aria-invalid sincronizado con el estado visual :user-invalid.
const syncAria=el=>{if(el.matches?.('input,select'))el.setAttribute('aria-invalid',el.matches(':user-invalid')?'true':'false');};
document.addEventListener('blur',e=>syncAria(e.target),true);
document.addEventListener('input',e=>{if(e.target.hasAttribute?.('aria-invalid'))syncAria(e.target);});

/* ---------- Pestañas de acceso ---------- */
function selectTab(tab){
  $$('[role=tab]').forEach(t=>{const on=t===tab;t.setAttribute('aria-selected',String(on));t.tabIndex=on?0:-1;document.getElementById(t.getAttribute('aria-controls')).hidden=!on;});
  message('#login-message');message('#register-message');
}
$$('[role=tab]').forEach(tab=>{
  tab.addEventListener('click',()=>selectTab(tab));
  tab.addEventListener('keydown',e=>{
    if(!['ArrowLeft','ArrowRight'].includes(e.key))return;
    const tabs=$$('[role=tab]');const next=tabs[(tabs.indexOf(tab)+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length];
    selectTab(next);next.focus();
  });
});

/* ---------- Vistas ---------- */
function showAuth(text=''){
  $('#dates-dialog').close();$('#folio-dialog').close();
  state.user=null;
  $('#app-view').hidden=true;$('#auth-view').hidden=false;
  if(text){selectTab($('#tab-login'));message('#login-message',text,'error');}
}
async function showApp(user){
  state.user=user;
  $('#auth-view').hidden=true;$('#app-view').hidden=false;
  const role=user.role;
  $('#user-name').textContent=user.name;
  $('#user-role').textContent=ROLE_NAMES[role]||role;$('#user-role').dataset.role=role;
  $('#avatar').textContent=user.name.split(' ').filter(Boolean).slice(0,2).map(p=>p[0]).join('').toUpperCase();
  $('#greeting').textContent=role==='cliente'?'Tus próximas estadías':role==='recepcion'?'Libro de recepción':'La cadena, en perspectiva';
  $('#role-eyebrow').textContent=`PANEL DE ${(ROLE_NAMES[role]||role).toUpperCase()}`;
  $('#role-intro').textContent=ROLE_INTRO[role]||'';
  // Cada rol ve solo lo que puede usar; el servidor valida los mismos permisos.
  const canCreate=role==='cliente'||role==='recepcion';
  $('#side').hidden=!canCreate;$('#layout').classList.toggle('single',!canCreate);
  $('#client-field').hidden=role!=='recepcion';$('#client-card').hidden=role!=='recepcion';
  $('#guest-field').hidden=role==='cliente';$('#guest').disabled=role==='cliente';
  $('#search-box').hidden=role==='cliente';
  $('#list-title').textContent=role==='cliente'?'Mis reservas':'Registro de reservas';
  $('#audit-card').hidden=role!=='gerente';$('#services-card').hidden=role!=='gerente';
  $('#hotel-filter-box').hidden=role!=='gerente';
  // RF-05 / RNF-03: el personal ve el hotel que detectó su estación y cuánto tardó.
  const station=user.station;
  $('#station-badge').hidden=role==='cliente';
  $('#station-badge').textContent=station?`Estación: ${station.name} · ${station.region} — detectada en ${station.ms} ms`:'Estación sin hotel configurado (revisa estacion.json): se muestra toda la cadena.';
  $('#station-badge').dataset.ok=station?'yes':'no';
  if(role==='recepcion'&&station)$('#role-intro').textContent=`Registra huéspedes, reservas, check-in y check-out de ${station.name}.`;
  ['#message','#list-message','#client-message','#services-message'].forEach(t=>message(t));
  $('#arrival').min=localDate();$('#arrival').value=localDate();$('#departure').min=localDate(1);$('#departure').value=localDate(1);
  updateStayHint();
  try{
    const hotels=await api('hotels');state.hotels=hotels;
    const regions=[...new Set(hotels.map(h=>h.region))];
    const grouped=(list)=>regions.map(r=>{const items=list.filter(h=>h.region===r);return items.length?`<optgroup label="${escapeHTML(r)}">${items.map(h=>`<option value="${h.id}">${escapeHTML(h.name)}</option>`).join('')}</optgroup>`:'';}).join('');
    // La recepción solo reserva en el hotel de su estación; el servidor aplica la misma regla.
    const localOnly=role==='recepcion'&&station;
    $('#hotels').innerHTML=grouped(localOnly?hotels.filter(h=>h.id===station.id):hotels);
    if(role==='gerente'){
      $('#hotel-filter').innerHTML='<option value="">Toda la cadena</option>'+grouped(hotels);
      $('#hotel-filter').value=station?String(station.id):'';
      $('#services-hotel').innerHTML=grouped(hotels);
      $('#services-hotel').value=String(station?.id||hotels[0]?.id||'');
    }
    const tasks=[refresh()];
    if(role==='recepcion')tasks.push(loadClients());
    if(role==='gerente')tasks.push(loadAudit(),loadServices());
    await Promise.all(tasks);
  }catch(error){message('#list-message',error.message,'error');}
}

/* ---------- Reservas ---------- */
async function refresh(){state.rows=await api('reservations');render();}
function render(){
  const rows=state.rows;
  const active=rows.filter(r=>['Confirmada','Alojado'].includes(r.status));
  $('#stat-active').textContent=active.length;
  $('#stat-cancelled').textContent=rows.filter(r=>r.status==='Cancelada').length;
  $('#stat-nights').textContent=active.reduce((sum,r)=>sum+nightsBetween(r.arrival,r.departure),0);
  const filter=$('input[name=filter]:checked').value;
  const term=$('#search-box').hidden?'':$('#search').value.trim().toLowerCase();
  const hotelFilter=$('#hotel-filter-box').hidden?'':$('#hotel-filter').value;
  const visible=rows.filter(r=>(filter==='all'||r.status===filter)&&(!hotelFilter||String(r.hotel_id)===hotelFilter)&&
    (!term||`${r.guest} ALT-${String(r.id).padStart(4,'0')} ${r.hotel} ${r.region}`.toLowerCase().includes(term)));
  if(!rows.length){$('#reservations').innerHTML=`<div class="empty"><strong>Aún no hay reservas</strong><span>${state.user?.role==='gerente'?'Aparecerán aquí cuando clientes o recepción las creen.':'Crea la primera con el formulario.'}</span></div>`;return;}
  if(!visible.length){$('#reservations').innerHTML='<div class="empty"><strong>Sin resultados</strong><span>Prueba con otro filtro o búsqueda.</span></div>';return;}
  $('#reservations').innerHTML=visible.map(r=>{
    const nights=nightsBetween(r.arrival,r.departure);const cancelled=r.status==='Cancelada';
    return `<article class="reservation${cancelled?' is-cancelled':''}">
      <div class="reservation-main">
        <div class="reservation-title"><h3>${escapeHTML(r.guest)}</h3><span class="code">ALT-${String(r.id).padStart(4,'0')}</span></div>
        <p class="reservation-place">${escapeHTML(r.hotel)} <span>· ${escapeHTML(r.region)} · Habitación ${r.room}</span></p>
        <p class="reservation-dates"><span>${dateText(r.arrival)}</span><span aria-hidden="true">→</span><span>${dateText(r.departure)}</span><span class="nights">${nights} ${nights===1?'noche':'noches'}</span></p>
        ${r.status==='Finalizada'&&r.total!=null?`<p class="charged">Cobrado: <b>${clp(r.total)}</b></p>`:''}
      </div>
      <div class="reservation-side">
        <span class="badge${cancelled?' cancelled':''}" data-status="${escapeHTML(r.status)}">${escapeHTML(r.status)}</span>
        ${cancelled?'':`<button type="button" class="secondary" data-action="folio" data-id="${r.id}">${['Confirmada','Alojado'].includes(r.status)?'Servicios y cuenta':'Ver cuenta'}</button>`}
        ${r.status==='Confirmada'?`<button type="button" class="secondary" data-action="dates" data-id="${r.id}">Modificar fechas</button><button type="button" class="cancel" data-action="cancel" data-id="${r.id}">Cancelar</button>`:''}
        ${state.user.role!=='cliente'&&r.status==='Confirmada'?`<button type="button" class="secondary" data-action="checkin" data-id="${r.id}">Check-in</button>`:''}
        ${state.user.role!=='cliente'&&r.status==='Alojado'?`<button type="button" class="secondary" data-action="checkout" data-id="${r.id}">Check-out</button>`:''}
      </div>
    </article>`;}).join('');
}
$$('input[name=filter]').forEach(i=>i.addEventListener('change',render));
$('#search').addEventListener('input',render);
$('#hotel-filter').addEventListener('change',render);

async function loadClients(){
  const clients=await api('clients');
  const current=$('#client').value;
  $('#client').innerHTML='<option value="">Huésped sin cuenta</option>'+clients.map(c=>`<option value="${c.id}">${escapeHTML(c.name)} · ${escapeHTML(c.rut)}</option>`).join('');
  $('#client').value=clients.some(c=>String(c.id)===current)?current:'';
  syncGuestField();
}
function syncGuestField(){
  if(state.user?.role!=='recepcion')return;
  const withAccount=$('#client').value!=='';
  $('#guest-field').hidden=withAccount;$('#guest').disabled=withAccount;
}
$('#client').addEventListener('change',syncGuestField);

function updateStayHint(){
  const n=nightsBetween($('#arrival').value,$('#departure').value);
  $('#stay-hint').textContent=n>0?`Estadía de ${n} ${n===1?'noche':'noches'}. Se asignará una habitación disponible (5 por hotel).`:'Se asignará una habitación disponible. Cada hotel tiene 5 habitaciones de demostración.';
}
$('#arrival').addEventListener('change',()=>{
  const d=new Date($('#arrival').value+'T12:00:00');d.setDate(d.getDate()+1);
  const next=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  $('#departure').min=next;if($('#departure').value<next)$('#departure').value=next;updateStayHint();
});
$('#departure').addEventListener('change',updateStayHint);

// Limpia el nombre sin marcarlo como error: reset() borra el estado de interacción y luego se restauran los demás campos.
function resetGuest(form){
  const keep=['client','hotels','arrival','departure'].map(id=>[id,document.getElementById(id).value]);
  form.reset();keep.forEach(([id,value])=>{document.getElementById(id).value=value;});
  syncGuestField();updateStayHint();
}

/* ---------- Bitácora (gerente) ---------- */
async function loadAudit(){
  const rows=await api('audit');
  $('#audit-rows').innerHTML=rows.length?rows.map(r=>{
    const alert=['acceso_fallido','acceso_bloqueado'].includes(r.action);
    const when=new Date(r.at).toLocaleString('es-CL',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
    return `<tr><td class="nowrap">${escapeHTML(when)}</td><td><span class="event${alert?' warn':''}">${escapeHTML(AUDIT_NAMES[r.action]||r.action)}</span></td><td>${r.name?`${escapeHTML(r.name)} <span class="muted">· ${escapeHTML(ROLE_NAMES[r.role]||r.role)}</span>`:'<span class="muted">Desconocido</span>'}</td><td>${escapeHTML(r.detail)}</td></tr>`;
  }).join(''):'<tr><td colspan="4" class="muted">Sin eventos registrados.</td></tr>';
}

/* ---------- Formularios ---------- */
$('#login-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;message('#login-message');
  await busy(form.querySelector('[type=submit]'),async()=>{
    try{const result=await api('login',Object.fromEntries(new FormData(form)));form.reset();await showApp(result.user);}
    catch(error){message('#login-message',error.message,'error');}
  });
});
$('#register-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;message('#register-message');
  await busy(form.querySelector('[type=submit]'),async()=>{
    try{const result=await api('register',Object.fromEntries(new FormData(form)));form.reset();form.querySelectorAll('.rules li').forEach(li=>li.classList.remove('ok'));await showApp(result.user);}
    catch(error){message('#register-message',error.message,'error');}
  });
});
$('#logout').addEventListener('click',async()=>{
  try{await api('logout',{});}catch{/* se cierra igual en pantalla */}
  selectTab($('#tab-login'));showAuth();
});
$('#reservation-form').addEventListener('submit',async event=>{
  event.preventDefault();message('#message');
  $('#alternatives').innerHTML='';
  await busy($('#save'),async()=>{
    try{const result=await api('reservations',Object.fromEntries(new FormData(event.target)));message('#message',result.message);resetGuest(event.target);await refresh();}
    catch(error){
      message('#message',error.message,'error');
      const local=state.user?.role==='recepcion'&&state.user.station;
      showAlternatives('#alternatives',error.alternatives,!local,local?'El huésped puede reservar en esos hoteles desde el portal de clientes o en su recepción.':'');
    }
  });
});
$('#client-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;message('#client-message');
  await busy(form.querySelector('[type=submit]'),async()=>{
    try{
      const result=await api('clients',Object.fromEntries(new FormData(form)));
      form.reset();form.querySelectorAll('.rules li').forEach(li=>li.classList.remove('ok'));
      message('#client-message',result.message+' Ya puedes elegirlo en “Nueva reserva”.');
      await loadClients();$('#client').value=String(result.client.id);syncGuestField();
    }catch(error){message('#client-message',error.message,'error');}
  });
});

/* ---------- Cancelación con diálogo de confirmación ---------- */
const dialog=$('#confirm-dialog');
function confirmCancel(r){
  $('#confirm-text').textContent=`ALT-${String(r.id).padStart(4,'0')} · ${r.guest} · ${r.hotel}, ${dateText(r.arrival)} → ${dateText(r.departure)}.`;
  dialog.returnValue='';dialog.showModal();
  return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='yes'),{once:true}));
}
$('#reservations').addEventListener('click',async event=>{
  const button=event.target.closest('[data-id]');if(!button)return;
  const row=state.rows.find(r=>String(r.id)===button.dataset.id);
  if(!row)return;
  const action=button.dataset.action;
  if(action==='dates'){openDates(row);return;}
  if(action==='folio'){openFolio(row);return;}
  if(action==='cancel'&&!await confirmCancel(row))return;
  await busy(button,async()=>{
    try{const result=await api(action,{id:row.id});message('#list-message',result.message);await refresh();if(state.user?.role==='gerente')await loadAudit();}
    catch(error){message('#list-message',error.message,'error');}
  });
});
$('#refresh').addEventListener('click',()=>refresh().catch(error=>message('#list-message',error.message,'error')));
$('#refresh-audit').addEventListener('click',()=>loadAudit().catch(error=>message('#list-message',error.message,'error')));

/* ---------- Fechas y alternativas: solo los controles necesarios ---------- */
function showAlternatives(target, hotels, selectable=false, note=''){
  if(!hotels)return;
  const tail=note||(selectable?'':'Tu reserva original se conserva. Para cambiar de hotel, crea otra reserva y luego cancela la anterior.');
  $(target).innerHTML=hotels.length?`<p class="hint">Hoteles con cupo en la misma región:</p>${hotels.map(h=>selectable?`<button type="button" class="secondary alternative" data-hotel="${h.id}">${escapeHTML(h.name)} · ${escapeHTML(h.region)}</button>`:`<p class="hint">${escapeHTML(h.name)} · ${escapeHTML(h.region)}</p>`).join('')}${tail?`<p class="hint">${escapeHTML(tail)}</p>`:''}`:'<p class="hint">Tampoco hay cupos en otros hoteles de la región. Prueba otras fechas.</p>';
}
$('#alternatives').addEventListener('click',event=>{
  const button=event.target.closest('[data-hotel]');if(!button)return;
  $('#hotels').value=button.dataset.hotel;$('#alternatives').innerHTML='';
  message('#message','Hotel alternativo seleccionado. Presiona Crear reserva para confirmar.');
});
function openDates(row){
  $('#dates-id').value=row.id;
  $('#dates-summary').textContent=`ALT-${String(row.id).padStart(4,'0')} · ${row.hotel}`;
  $('#edit-arrival').min=localDate();$('#edit-arrival').value=row.arrival;
  $('#edit-departure').min=localDate(1);$('#edit-departure').value=row.departure;
  message('#dates-message');$('#dates-alternatives').innerHTML='';$('#dates-dialog').showModal();
}
$('#close-dates').addEventListener('click',()=>$('#dates-dialog').close());
$('#dates-form').addEventListener('submit',async event=>{
  event.preventDefault();message('#dates-message');$('#dates-alternatives').innerHTML='';
  await busy(event.target.querySelector('[type=submit]'),async()=>{
    try{
      const result=await api('modify-dates',Object.fromEntries(new FormData(event.target)));
      $('#dates-dialog').close();message('#list-message',result.message);await refresh();
      if(state.user?.role==='gerente')await loadAudit();
    }catch(error){message('#dates-message',error.message,'error');showAlternatives('#dates-alternatives',error.alternatives);}
  });
});

/* ---------- Servicios y cuenta de una reserva (RF-04, RF-06, RF-07) ---------- */
async function openFolio(row){
  state.folioRow=row;$('#folio-id').value=row.id;message('#folio-message');
  $('#folio-summary').textContent=`${code(row.id)} · ${row.guest} · ${row.hotel}`;
  $('#folio-dialog').showModal();
  await loadFolio();
}
async function loadFolio(){
  const row=state.folioRow;
  try{
    const f=await api(`folio?id=${row.id}`);
    const open=['Confirmada','Alojado'].includes(f.status);
    $('#folio-rows').innerHTML=`<tr><td>Alojamiento<span class="sub">${f.nights} ${f.nights===1?'noche':'noches'} × ${clp(f.rate)}</span></td><td>${f.nights}</td><td class="money">${clp(f.lodging)}</td><td></td></tr>`+
      f.services.map(s=>`<tr><td>${escapeHTML(s.name)}<span class="sub">${clp(s.price)} c/u</span></td><td>${s.quantity}</td><td class="money">${clp(s.subtotal)}</td><td>${open?`<button type="button" class="cancel" data-line="${s.id}">Quitar</button>`:''}</td></tr>`).join('');
    const closed=f.status==='Finalizada';
    $('#folio-total-label').textContent=closed?'Total cobrado en el check-out':'Total estimado (se cobra en el check-out)';
    $('#folio-total').textContent=clp(closed&&f.charged!=null?f.charged:f.total);
    $('#folio-form').hidden=!open;
    if(open){
      const services=await api(`services?hotel_id=${f.hotel_id}`);
      const offered=services.filter(s=>s.active);
      $('#folio-service').innerHTML=offered.length?offered.map(s=>`<option value="${s.id}">${escapeHTML(s.name)} · ${clp(s.price)}</option>`).join(''):'<option value="">Sin servicios disponibles</option>';
    }
  }catch(error){message('#folio-message',error.message,'error');}
}
$('#folio-form').addEventListener('submit',async event=>{
  event.preventDefault();message('#folio-message');
  await busy(event.target.querySelector('[type=submit]'),async()=>{
    try{const result=await api('reservation-services',Object.fromEntries(new FormData(event.target)));$('#folio-qty').value=1;await loadFolio();message('#folio-message',result.message);}
    catch(error){message('#folio-message',error.message,'error');}
  });
});
$('#folio-rows').addEventListener('click',async event=>{
  const button=event.target.closest('[data-line]');if(!button)return;
  await busy(button,async()=>{
    try{const result=await api('reservation-services/remove',{id:Number(button.dataset.line)});await loadFolio();message('#folio-message',result.message);}
    catch(error){message('#folio-message',error.message,'error');}
  });
});
$('#close-folio').addEventListener('click',()=>$('#folio-dialog').close());

/* ---------- Gestión de servicios por hotel (gerencia, RF-07) ---------- */
async function loadServices(){
  const hotel=$('#services-hotel').value;if(!hotel)return;
  const services=await api(`services?hotel_id=${hotel}`);
  $('#services-rows').innerHTML=services.length?services.map(s=>`<tr data-service="${s.id}">
    <td><label class="sr-only" for="svc-name-${s.id}">Nombre</label><input id="svc-name-${s.id}" class="svc-name" value="${escapeHTML(s.name)}" maxlength="60"></td>
    <td><label class="sr-only" for="svc-price-${s.id}">Precio</label><input id="svc-price-${s.id}" class="svc-price" value="${s.price}" inputmode="numeric" pattern="[0-9]{1,7}"></td>
    <td><span class="badge${s.active?'':' cancelled'}">${s.active?'Disponible':'Retirado'}</span></td>
    <td class="row-actions"><button type="button" class="secondary" data-svc="save">Guardar</button><button type="button" class="cancel" data-svc="${s.active?'retire':'restore'}">${s.active?'Retirar':'Reactivar'}</button></td>
  </tr>`).join(''):'<tr><td colspan="4" class="muted">Este hotel aún no tiene servicios.</td></tr>';
}
$('#services-hotel').addEventListener('change',()=>{message('#services-message');loadServices().catch(error=>message('#services-message',error.message,'error'));});
$('#services-rows').addEventListener('click',async event=>{
  const button=event.target.closest('[data-svc]');if(!button)return;
  const tr=button.closest('tr');const id=Number(tr.dataset.service);const kind=button.dataset.svc;
  const payload=kind==='save'?{id,name:tr.querySelector('.svc-name').value,price:tr.querySelector('.svc-price').value.trim()}:{id,active:kind==='restore'};
  await busy(button,async()=>{
    try{const result=await api('services/update',payload);await loadServices();message('#services-message',result.message);loadAudit().catch(()=>{});}
    catch(error){message('#services-message',error.message,'error');}
  });
});
$('#service-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;message('#services-message');
  await busy(form.querySelector('[type=submit]'),async()=>{
    try{const result=await api('services',{...Object.fromEntries(new FormData(form)),hotel_id:$('#services-hotel').value});form.reset();await loadServices();message('#services-message',result.message);loadAudit().catch(()=>{});}
    catch(error){message('#services-message',error.message,'error');}
  });
});

/* ---------- Inicio ---------- */
(async()=>{
  try{await showApp(await api('me'));}catch{if(!state.user)showAuth();}
})();
