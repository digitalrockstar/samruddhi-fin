/* Samruddhi Fin - shared Alpine components + helpers */

function formatCurrency(amount) {
  const n = Number(amount || 0);
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: n % 1 === 0 ? 0 : 2
  }).format(n);
}

function formatDate(value, withTime = true) {
  if (!value) return '';
  const d = new Date(value);
  const opts = withTime
    ? { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { day: '2-digit', month: 'short', year: 'numeric' };
  return d.toLocaleString('en-IN', opts);
}

function formatRelative(value) {
  if (!value) return '';
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return formatDate(value, false);
}

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* Which person am I? Stored in localStorage, no backend auth. */
function currentUser() {
  return {
    id: localStorage.getItem('current_person_id') ? Number(localStorage.getItem('current_person_id')) : null,
    name: localStorage.getItem('current_person_name') || 'Guest',

    get persons() {
      try { return JSON.parse(localStorage.getItem('all_persons') || '[]'); }
      catch (e) { return []; }
    },

    async load() {
      try {
        this.persons = await api('/api/persons');
        localStorage.setItem('all_persons', JSON.stringify(this.persons));
        if (!this.id && this.persons.length) {
          this.id = this.persons[0].id;
          this.name = this.persons[0].name;
          this.persist();
        }
      } catch (e) {
        console.error('Failed to load persons', e);
      }
    },

    persist() {
      localStorage.setItem('current_person_id', String(this.id));
      localStorage.setItem('current_person_name', this.name);
    },

    select(person) {
      this.id = person.id;
      this.name = person.name;
      this.persist();
      window.dispatchEvent(new CustomEvent('person-changed', { detail: person }));
      this.load();
    }
  };
}

/* Bootstrap on every page */
document.addEventListener('alpine:init', () => {
  Alpine.store('user', currentUser());
  Alpine.store('user').load();
});

document.addEventListener('DOMContentLoaded', () => {
  if (window.lucide) lucide.createIcons();
  document.body.addEventListener('htmx:afterSwap', () => {
    if (window.lucide) lucide.createIcons();
  });
  document.body.addEventListener('htmx:afterRequest', (evt) => {
    const msg = evt.detail.xhr.getResponseHeader('HX-Trigger');
    if (msg) {
      try {
        const data = JSON.parse(msg);
        if (data.toast) showToast(data.toast.message, data.toast.type);
      } catch (e) {}
    }
    if (!evt.detail.successful) {
      let msg2 = 'Request failed';
      try { msg2 = JSON.parse(evt.detail.xhr.responseText).detail || msg2; } catch (e) {}
      showToast(msg2, 'error');
    }
  });
});