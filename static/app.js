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
  reserva_creada: 'Reserva creada', reserva_cancelada: 'Reserva cancelada', servicios_actualizados: 'Servicios actualizados',
};
const state = {user: null, rows: [], services: []};
const money = value => new Intl.NumberFormat('es-CL',{style:'currency',currency:'CLP',maximumFractionDigits:0}).format(value);

function localDate(offset=0){const d=new Date();d.setDate(d.getDate()+offset);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function message(target, text='', type='ok'){const el=$(target);el.textContent=text;el.dataset.type=type;}
async function busy(button, task){button.disabled=true;button.setAttribute('aria-busy','true');try{return await task();}finally{button.disabled=false;button.removeAttribute('aria-busy');}}

async function api(path, data){
  const response = await fetch('/api/'+path, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
  let result = {};
  try { result = await response.json(); } catch { /* respuesta sin JSON */ }
  // Sesión vencida o ausente: se vuelve a la pantalla de acceso.
  if (response.status === 401 && !['login','me','register'].includes(path)) showAuth(result.error);
  if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
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
  $('#greeting').textContent=`Hola, ${user.name.split(' ')[0]}`;
  $('#role-eyebrow').textContent=`PANEL DE ${(ROLE_NAMES[role]||role).toUpperCase()}`;
  $('#role-intro').textContent=ROLE_INTRO[role]||'';
  // Cada rol ve solo lo que puede usar; el servidor valida los mismos permisos.
  const canCreate=role==='cliente'||role==='recepcion';
  $('#side').hidden=!canCreate;$('#layout').classList.toggle('single',!canCreate);
  $('#client-field').hidden=role!=='recepcion';$('#client-card').hidden=role!=='recepcion';
  $('#guest-field').hidden=role==='cliente';$('#guest').disabled=role==='cliente';
  $('#search-box').hidden=role==='cliente';
  $('#list-title').textContent=role==='cliente'?'Mis reservas':'Registro de reservas';
  $('#audit-card').hidden=role!=='gerente';
  ['#message','#list-message','#client-message'].forEach(t=>message(t));
  $('#arrival').min=localDate();$('#arrival').value=localDate();$('#departure').min=localDate(1);$('#departure').value=localDate(1);
  updateStayHint();
  try{
    const [hotels,services]=await Promise.all([api('hotels'),api('services')]);
    state.services=services;
    renderServicePicker('#booking-service-options',services,[]);
    const regions=[...new Set(hotels.map(h=>h.region))];
    $('#hotels').innerHTML=regions.map(r=>`<optgroup label="${escapeHTML(r)}">${hotels.filter(h=>h.region===r).map(h=>`<option value="${h.id}">${escapeHTML(h.name)}</option>`).join('')}</optgroup>`).join('');
    const tasks=[refresh()];
    if(role==='recepcion')tasks.push(loadClients());
    if(role==='gerente')tasks.push(loadAudit());
    await Promise.all(tasks);
  }catch(error){message('#list-message',error.message,'error');}
}

/* ---------- Reservas ---------- */
async function refresh(){state.rows=await api('reservations');render();}
function render(){
  const rows=state.rows;
  const active=rows.filter(r=>r.status==='Confirmada');
  $('#stat-active').textContent=active.length;
  $('#stat-cancelled').textContent=rows.length-active.length;
  $('#stat-nights').textContent=active.reduce((sum,r)=>sum+nightsBetween(r.arrival,r.departure),0);
  const filter=$('input[name=filter]:checked').value;
  const term=$('#search-box').hidden?'':$('#search').value.trim().toLowerCase();
  const visible=rows.filter(r=>(filter==='all'||r.status===filter)&&
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
        <p class="reservation-total">Alojamiento ${money(r.lodging_total)} · Servicios ${money(r.services_total)} · <strong>Total ${money(r.total)}</strong></p>
      </div>
      <div class="reservation-side">
        <span class="badge${cancelled?' cancelled':''}">${escapeHTML(r.status)}</span>
        <div class="reservation-actions">
          <button type="button" class="text-action" data-action="receipt" data-id="${r.id}">Comprobante</button>
          ${cancelled?'':`<button type="button" class="text-action" data-action="services" data-id="${r.id}">Servicios</button><button type="button" class="cancel" data-action="cancel" data-id="${r.id}">Cancelar</button>`}
        </div>
      </div>
    </article>`;}).join('');
}
$$('input[name=filter]').forEach(i=>i.addEventListener('change',render));
$('#search').addEventListener('input',render);

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

function renderServicePicker(target,services,selected){
  const quantities=Object.fromEntries(selected.map(service=>[service.id,service.quantity]));
  $(target).innerHTML=services.map(service=>`<label class="service-option">
    <span>${escapeHTML(service.name)} <small>${money(service.unit_price)} c/u</small></span>
    <input type="number" min="0" max="10" step="1" value="${quantities[service.id]||0}" data-service-id="${escapeHTML(service.id)}" aria-label="Cantidad de ${escapeHTML(service.name)}">
  </label>`).join('');
}
function selectedServices(target){
  return [...$(target).querySelectorAll('[data-service-id]')]
    .map(input=>({id:input.dataset.serviceId,quantity:Number(input.value)})).filter(item=>item.quantity>0);
}
function showReceipt(reservation){
  const lines=[{name:`Alojamiento · ${reservation.nights} ${reservation.nights===1?'noche':'noches'}`,
    quantity:reservation.nights,unit_price:reservation.nightly_rate,total:reservation.lodging_total},
    ...reservation.services.map(service=>({...service,total:service.quantity*service.unit_price}))];
  $('#receipt-content').innerHTML=`<p class="receipt-meta">ALT-${String(reservation.id).padStart(4,'0')} · ${escapeHTML(reservation.guest)}<br>${escapeHTML(reservation.hotel)}<br>${dateText(reservation.arrival)} → ${dateText(reservation.departure)}</p>
    <div class="receipt-lines">${lines.map(line=>`<div><span>${escapeHTML(line.name)}${line.quantity>1?` × ${line.quantity}`:''}</span><strong>${money(line.total)}</strong></div>`).join('')}
    <div class="receipt-total"><span>Total (CLP)</span><strong>${money(reservation.total)}</strong></div></div>
    <p class="hint">Comprobante informativo. No se realizó ningún pago.</p>`;
  $('#receipt-dialog').showModal();
}
function showServicesEditor(reservation){
  $('#services-reservation').textContent=`ALT-${String(reservation.id).padStart(4,'0')} · ${reservation.guest}`;
  renderServicePicker('#services-editor',state.services,reservation.services);
  $('#services-dialog').dataset.reservationId=reservation.id;
  message('#services-message');
  $('#services-dialog').showModal();
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
  await busy($('#save'),async()=>{
    try{const data=Object.fromEntries(new FormData(event.target));data.services=selectedServices('#booking-service-options');const result=await api('reservations',data);message('#message',result.message);resetGuest(event.target);renderServicePicker('#booking-service-options',state.services,[]);await refresh();}
    catch(error){message('#message',error.message,'error');}
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
  if(button.dataset.action==='receipt'){showReceipt(row);return;}
  if(button.dataset.action==='services'){showServicesEditor(row);return;}
  if(button.dataset.action!=='cancel'||!await confirmCancel(row))return;
  await busy(button,async()=>{
    try{const result=await api('cancel',{id:row.id});message('#list-message',result.message);await refresh();if(state.user?.role==='gerente')await loadAudit();}
    catch(error){message('#list-message',error.message,'error');}
  });
});
$('#services-close').addEventListener('click',()=>$('#services-dialog').close());
$('#services-form').addEventListener('submit',async event=>{
  event.preventDefault();
  await busy($('#services-save'),async()=>{
    try{
      const result=await api('reservation-services',{id:Number($('#services-dialog').dataset.reservationId),services:selectedServices('#services-editor')});
      message('#list-message',result.message);$('#services-dialog').close();await refresh();
    }catch(error){message('#services-message',error.message,'error');}
  });
});
$('#refresh').addEventListener('click',()=>refresh().catch(error=>message('#list-message',error.message,'error')));
$('#refresh-audit').addEventListener('click',()=>loadAudit().catch(error=>message('#list-message',error.message,'error')));

/* ---------- Inicio ---------- */
(async()=>{
  try{await showApp(await api('me'));}catch{if(!state.user)showAuth();}
})();
