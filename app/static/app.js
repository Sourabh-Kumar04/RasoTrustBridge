/**
 * TrustBridge — Phase 2 Frontend Core
 * Fast, lightweight reactive SPA for Programmable Freelance Escrow
 */

const API_BASE = '/api/v1';

const TrustBridgeApp = {
  state: {
    contracts: [],
    activeContract: null,
    fxRates: { USD: 1.0, INR: 84.50, EUR: 0.92, GBP: 0.78 },
    lastNegotiation: null,
    generatedDraft: null,
    user: null,
    token: localStorage.getItem('tb_access_token') || null,
    refreshToken: localStorage.getItem('tb_refresh_token') || null,
    notifications: [],
    unreadCount: 0,
  },

  // ---------------------------------------------------------------------------
  // INITIALIZATION & ROUTING
  // ---------------------------------------------------------------------------
  init() {
    // Check if auth_token passed in URL for automated testing / previews
    try {
      const urlParams = new URLSearchParams(window.location.search);
      const urlToken = urlParams.get('auth_token');
      if (urlToken) {
        this.state.token = urlToken;
        localStorage.setItem('tb_access_token', urlToken);
      }
    } catch (e) {}

    window.addEventListener('hashchange', () => this.handleRouting());
    this.initAuth();
    this.handleRouting();
    this.fetchLiveFXRates();

    // Close notification dropdown when clicking outside
    document.addEventListener('click', (e) => {
      const dropdown = document.getElementById('notif-dropdown');
      const bell = document.getElementById('notif-bell-btn');
      if (dropdown && bell && !dropdown.contains(e.target) && !bell.contains(e.target)) {
        dropdown.classList.add('hidden');
      }
    });

    // Notification polling every 30s
    setInterval(() => {
      if (this.state.token) {
        this.fetchNotifications();
      }
    }, 30000);

    // Sync negotiator bounds from briefs
    document.getElementById('buyer-brief')?.addEventListener('input', () => this.syncBoundsFromBriefs(false));
    document.getElementById('seller-brief')?.addEventListener('input', () => this.syncBoundsFromBriefs(false));
    this.syncBoundsFromBriefs(false);
  },

  handleRouting() {
    const hash = window.location.hash || '#/';
    const [route, param] = hash.split('/').filter(Boolean);

    // Hide all view panels
    document.querySelectorAll('.view-panel').forEach(el => el.classList.add('hidden'));

    // Reset active nav styles
    document.querySelectorAll('.nav-link').forEach(link => {
      link.classList.remove('text-slate-950', 'border-slate-900');
      link.classList.add('text-slate-500', 'border-transparent');
    });

    if (hash === '#/' || hash === '#' || !hash) {
      document.getElementById('view-landing')?.classList.remove('hidden');
      this.setActiveNav('nav-landing');
    } else if (hash.startsWith('#/dashboard')) {
      document.getElementById('view-dashboard')?.classList.remove('hidden');
      this.setActiveNav('nav-dashboard');
      this.refreshDashboard();
    } else if (hash.startsWith('#/negotiate')) {
      document.getElementById('view-negotiate')?.classList.remove('hidden');
      this.setActiveNav('nav-negotiate');
      this.loadRecentNegotiationIfAvailable();
    } else if (hash.startsWith('#/builder')) {
      document.getElementById('view-builder')?.classList.remove('hidden');
      this.setActiveNav('nav-builder');
    } else if (hash.startsWith('#/contracts/')) {
      const contractId = hash.replace('#/contracts/', '');
      document.getElementById('view-contract-detail')?.classList.remove('hidden');
      this.loadContractDetail(contractId);
    } else {
      document.getElementById('view-landing')?.classList.remove('hidden');
      this.setActiveNav('nav-landing');
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
  },

  setActiveNav(id) {
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('text-slate-500', 'border-transparent');
      el.classList.add('text-slate-950', 'border-slate-900');
    }
  },

  // ---------------------------------------------------------------------------
  // AUTHENTICATION & USER SESSIONS
  // ---------------------------------------------------------------------------
  setSession(accessToken, refreshToken, user = null) {
    this.state.token = accessToken;
    this.state.refreshToken = refreshToken;
    localStorage.setItem('tb_access_token', accessToken);
    localStorage.setItem('tb_refresh_token', refreshToken);
    if (user) {
      this.state.user = user;
    }
    this.updateNavAuthUI();
  },

  async initAuth() {
    if (this.state.token) {
      try {
        const user = await this.apiRequest('/auth/me');
        this.state.user = user;
        this.fetchNotifications();
      } catch (e) {
        console.warn('Session expired or invalid:', e.message);
        this.logout();
      }
    }
    this.updateNavAuthUI();
  },

  updateNavAuthUI() {
    const container = document.getElementById('auth-nav-container');
    if (!container) return;

    if (this.state.user) {
      const u = this.state.user;
      const initial = (u.display_name || u.email || 'U')[0].toUpperCase();
      const roleBadge = u.role === 'freelancer' ? 'Freelancer' : 'Client';
      container.innerHTML = `
        <div class="flex items-center space-x-2">
          <div class="w-7 h-7 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-xs">
            ${initial}
          </div>
          <div class="hidden sm:flex flex-col text-left text-xs leading-tight">
            <span class="font-medium text-slate-900">${this.escapeHtml(u.display_name)}</span>
            <span class="text-[10px] text-slate-500 font-mono">${roleBadge} • <span class="text-emerald-700 font-semibold">Trust: ${u.trust_score ?? 50}/100</span></span>
          </div>
          <button onclick="TrustBridgeApp.logout()" class="btn-subtle text-xs py-1 px-2 text-slate-500 hover:text-red-600">
            Log Out
          </button>
        </div>
      `;
    } else {
      container.innerHTML = `
        <button onclick="TrustBridgeApp.openAuthModal('login')" class="btn-secondary text-xs py-1.5 px-3">
          Sign In
        </button>
        <button onclick="TrustBridgeApp.openAuthModal('register')" class="btn-primary text-xs py-1.5 px-3 hidden sm:inline-flex">
          Register
        </button>
      `;
    }
  },

  openAuthModal(tab = 'login') {
    const modal = document.getElementById('modal-auth');
    if (modal) modal.classList.remove('hidden');
    this.switchAuthTab(tab);
    const err = document.getElementById('auth-error-alert');
    if (err) err.classList.add('hidden');
  },

  closeAuthModal() {
    const modal = document.getElementById('modal-auth');
    if (modal) modal.classList.add('hidden');
  },

  switchAuthTab(tab) {
    const tabLogin = document.getElementById('auth-tab-login');
    const tabRegister = document.getElementById('auth-tab-register');
    const formLogin = document.getElementById('form-login');
    const formRegister = document.getElementById('form-register');
    const err = document.getElementById('auth-error-alert');
    if (err) err.classList.add('hidden');

    if (tab === 'login') {
      tabLogin?.classList.add('text-slate-900', 'font-semibold', 'border-slate-900');
      tabLogin?.classList.remove('text-slate-400', 'border-transparent');
      tabRegister?.classList.remove('text-slate-900', 'font-semibold', 'border-slate-900');
      tabRegister?.classList.add('text-slate-400', 'border-transparent');
      formLogin?.classList.remove('hidden');
      formRegister?.classList.add('hidden');
    } else {
      tabRegister?.classList.add('text-slate-900', 'font-semibold', 'border-slate-900');
      tabRegister?.classList.remove('text-slate-400', 'border-transparent');
      tabLogin?.classList.remove('text-slate-900', 'font-semibold', 'border-slate-900');
      tabLogin?.classList.add('text-slate-400', 'border-transparent');
      formRegister?.classList.remove('hidden');
      formLogin?.classList.add('hidden');
    }
  },

  async executeLogin() {
    const email = document.getElementById('login-email')?.value;
    const password = document.getElementById('login-password')?.value;
    const errEl = document.getElementById('auth-error-alert');
    try {
      const res = await this.apiRequest('/auth/login', 'POST', { email, password });
      this.setSession(res.access_token, res.refresh_token, res.user);
      this.closeAuthModal();
      this.showToast(`Welcome back, ${res.user.display_name}!`, 'success');
      this.fetchNotifications();
      this.refreshDashboard();
    } catch (e) {
      if (errEl) {
        errEl.textContent = e.message;
        errEl.classList.remove('hidden');
      }
    }
  },

  async executeRegister() {
    const email = document.getElementById('reg-email')?.value;
    const password = document.getElementById('reg-password')?.value;
    const displayName = document.getElementById('reg-name')?.value;
    const role = document.getElementById('reg-role')?.value || 'client';
    const errEl = document.getElementById('auth-error-alert');
    try {
      const res = await this.apiRequest('/auth/register', 'POST', {
        email,
        password,
        display_name: displayName,
        role,
      });
      this.setSession(res.access_token, res.refresh_token, res.user);
      this.closeAuthModal();
      this.showToast(`Account created! Welcome, ${res.user.display_name}!`, 'success');
      this.fetchNotifications();
      this.refreshDashboard();
    } catch (e) {
      if (errEl) {
        errEl.textContent = e.message;
        errEl.classList.remove('hidden');
      }
    }
  },

  logout() {
    this.state.user = null;
    this.state.token = null;
    this.state.refreshToken = null;
    this.state.notifications = [];
    this.state.unreadCount = 0;
    localStorage.removeItem('tb_access_token');
    localStorage.removeItem('tb_refresh_token');
    this.updateNavAuthUI();
    this.renderNotifications();
    this.showToast('You have been logged out', 'info');
    this.refreshDashboard();
  },

  toggleMobileMenu() {
    const drawer = document.getElementById('mobile-nav-drawer');
    if (drawer) {
      drawer.classList.toggle('hidden');
    }
  },

  // ---------------------------------------------------------------------------
  // IN-APP NOTIFICATIONS
  // ---------------------------------------------------------------------------
  toggleNotificationsDropdown() {
    const dropdown = document.getElementById('notif-dropdown');
    if (!dropdown) return;
    const isHidden = dropdown.classList.contains('hidden');
    if (isHidden) {
      dropdown.classList.remove('hidden');
      this.fetchNotifications();
    } else {
      dropdown.classList.add('hidden');
    }
  },

  async fetchNotifications() {
    if (!this.state.token) {
      this.state.notifications = [];
      this.state.unreadCount = 0;
      this.renderNotifications();
      return;
    }
    try {
      const data = await this.apiRequest('/notifications');
      this.state.notifications = data.notifications || [];
      this.state.unreadCount = data.unread_count || 0;
      this.renderNotifications();
    } catch (e) {
      // Quiet fail if guest or token expired
    }
  },

  renderNotifications() {
    const badge = document.getElementById('notif-badge');
    const list = document.getElementById('notif-list');
    if (badge) {
      if (this.state.unreadCount > 0) {
        badge.textContent = this.state.unreadCount > 9 ? '9+' : this.state.unreadCount;
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }

    if (!list) return;
    if (!this.state.notifications.length) {
      list.innerHTML = `<div class="p-6 text-center text-xs text-slate-400 font-mono">No notifications yet</div>`;
      return;
    }

    list.innerHTML = this.state.notifications.map(n => {
      const isUnread = !n.read;
      const timeStr = new Date(n.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return `
        <div class="p-3 text-left transition hover:bg-slate-50 flex items-start justify-between gap-3 ${isUnread ? 'bg-blue-50/40' : ''}">
          <div class="space-y-1">
            <div class="flex items-center space-x-2">
              <span class="font-semibold text-xs text-slate-900">${this.escapeHtml(n.title)}</span>
              ${isUnread ? '<span class="w-1.5 h-1.5 rounded-full bg-blue-600 inline-block"></span>' : ''}
            </div>
            <p class="text-xs text-slate-600 leading-snug">${this.escapeHtml(n.body)}</p>
            <span class="font-mono text-[10px] text-slate-400">${timeStr}</span>
          </div>
          ${isUnread ? `
            <button onclick="TrustBridgeApp.markNotificationRead('${n.id}')" title="Mark as read" class="text-[11px] text-slate-400 hover:text-slate-700 font-mono">
              ✓
            </button>
          ` : ''}
        </div>
      `;
    }).join('');
  },

  async markNotificationRead(id) {
    try {
      await this.apiRequest(`/notifications/${id}/read`, 'POST');
      this.fetchNotifications();
    } catch (e) {
      console.error(e);
    }
  },

  async markAllNotificationsRead() {
    try {
      await this.apiRequest('/notifications/read-all', 'POST');
      this.fetchNotifications();
    } catch (e) {
      console.error(e);
    }
  },

  // ---------------------------------------------------------------------------
  // API HELPERS & TOASTS
  // ---------------------------------------------------------------------------
  async apiRequest(path, method = 'GET', body = null) {
    const headers = {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    };
    if (this.state.token) {
      headers['Authorization'] = `Bearer ${this.state.token}`;
    }
    const options = {
      method,
      headers,
    };
    if (body) {
      options.body = JSON.stringify(body);
    }
    let res = await fetch(`${API_BASE}${path}`, options);

    // If 401 and we have a refresh token, try refreshing token
    if (res.status === 401 && this.state.refreshToken && !path.startsWith('/auth/')) {
      try {
        const refRes = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: this.state.refreshToken }),
        });
        if (refRes.ok) {
          const newTokens = await refRes.json();
          this.setSession(newTokens.access_token, newTokens.refresh_token, newTokens.user);
          headers['Authorization'] = `Bearer ${newTokens.access_token}`;
          res = await fetch(`${API_BASE}${path}`, options);
        } else {
          this.logout();
        }
      } catch (e) {
        this.logout();
      }
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'API request failed');
    }
    return res.json();
  },

  showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    const colorClass = type === 'success' 
      ? 'bg-emerald-950 text-emerald-100 border-emerald-700' 
      : type === 'error'
      ? 'bg-red-950 text-red-100 border-red-700'
      : 'bg-slate-900 text-white border-slate-700';

    toast.className = `p-4 rounded text-xs font-mono border hairline-all shadow-lg flex items-center space-x-3 pointer-events-auto transition-all duration-300`;
    toast.innerHTML = `
      <span class="w-2 h-2 rounded-full ${type === 'success' ? 'bg-emerald-400' : type === 'error' ? 'bg-red-400' : 'bg-blue-400'}"></span>
      <span class="flex-grow">${message}</span>
    `;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  },

  // ---------------------------------------------------------------------------
  // FX ENGINE
  // ---------------------------------------------------------------------------
  async fetchLiveFXRates() {
    try {
      const quoteINR = await this.apiRequest('/fx/quote?base=USD&target=INR&amount=1');
      if (quoteINR && quoteINR.rate) {
        this.state.fxRates.INR = quoteINR.rate;
        const el = document.getElementById('ticker-usdinr');
        if (el) el.textContent = quoteINR.rate.toFixed(2);
        const rEl = document.getElementById('rate-usd-inr');
        if (rEl) rEl.textContent = quoteINR.rate.toFixed(2);
        const sEl = document.getElementById('dash-fx-inr-spot');
        if (sEl) sEl.textContent = quoteINR.rate.toFixed(2);
      }
    } catch (e) {
      console.warn('Using fallback FX rates:', e);
    }
  },

  // ---------------------------------------------------------------------------
  // DASHBOARD
  // ---------------------------------------------------------------------------
  async refreshDashboard() {
    if (!this.state.token) {
      this.state.contracts = [];
      this.renderDashboard();
      return;
    }
    try {
      const contracts = await this.apiRequest('/contracts');
      this.state.contracts = contracts || [];
      this.renderDashboard();
    } catch (e) {
      this.state.contracts = [];
      this.renderDashboard();
    }
  },

  renderDashboard() {
    const contracts = this.state.contracts;
    let totalEscrow = 0;
    let totalPayout = 0;
    let activeCount = 0;
    let releasedMilestones = 0;

    contracts.forEach(c => {
      totalEscrow += (c.total_amount || 0);
      const rate = c.locked_fx_rate || this.state.fxRates[c.payout_currency] || 84.50;
      totalPayout += (c.total_amount || 0) * rate;
      if (c.status === 'ACTIVE') activeCount++;
      (c.milestones || []).forEach(m => {
        if (m.status === 'RELEASED') releasedMilestones++;
      });
    });

    document.getElementById('dash-total-escrow').textContent = `$${totalEscrow.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    document.getElementById('dash-payout-exposure').textContent = `₹${totalPayout.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    document.getElementById('dash-active-contracts').textContent = activeCount;
    document.getElementById('dash-released-milestones').textContent = releasedMilestones;
    document.getElementById('dash-contract-count').textContent = contracts.length;

    const tbody = document.getElementById('contracts-table-body');
    if (!tbody) return;

    if (!this.state.token) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="text-center py-16 text-slate-500 font-mono text-xs">
            Sign in to view your personalized portfolio contracts and escrow status.
            <div class="pt-4 flex items-center justify-center space-x-3">
              <button onclick="TrustBridgeApp.openAuthModal('login')" class="btn-primary text-xs py-1.5 px-4">Sign In</button>
              <button onclick="TrustBridgeApp.openAuthModal('register')" class="btn-secondary text-xs py-1.5 px-4">Create Account</button>
            </div>
          </td>
        </tr>
      `;
      return;
    }

    if (contracts.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" class="text-center py-16 text-slate-400 font-mono text-xs">
            No escrow contracts registered yet. Launch an AI negotiation or create a contract from a brief.
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = contracts.map(c => {
      const totalMilestones = (c.milestones || []).length;
      const verifiedMilestones = (c.milestones || []).filter(m => m.status === 'VERIFIED' || m.status === 'RELEASED').length;
      const rate = c.locked_fx_rate || 84.50;
      const payoutVal = (c.total_amount * rate).toLocaleString('en-US', { maximumFractionDigits: 2 });

      let badgeClass = 'badge-draft';
      if (c.status === 'ACTIVE') badgeClass = 'badge-active';
      if (c.status === 'COMPLETED') badgeClass = 'badge-verified';
      if (c.status === 'DISPUTED') badgeClass = 'badge-disputed';

      return `
        <tr>
          <td>
            <div class="font-medium text-slate-900">${this.escapeHtml(c.title)}</div>
            <div class="text-xs text-slate-400 font-mono truncate max-w-xs">${this.escapeHtml(c.id)}</div>
          </td>
          <td>
            <div class="text-xs text-slate-900">${this.escapeHtml(c.client_name)} <span class="font-mono text-slate-400">(${c.client_country})</span></div>
            <div class="text-xs text-slate-500">→ ${this.escapeHtml(c.developer_name)} <span class="font-mono text-slate-400">(${c.developer_country})</span></div>
          </td>
          <td class="font-mono">
            <div class="font-semibold text-slate-900">$${c.total_amount.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${c.base_currency}</div>
            <div class="text-xs text-slate-400">≈ ${c.payout_currency} ${payoutVal}</div>
          </td>
          <td class="font-mono text-xs">
            <span class="text-slate-700">${c.fx_strategy}</span>
            <div class="text-slate-400">@ ${rate.toFixed(2)}</div>
          </td>
          <td>
            <div class="flex items-center space-x-2">
              <span class="font-mono text-xs font-medium text-slate-800">${verifiedMilestones}/${totalMilestones}</span>
              <div class="w-16 h-1.5 bg-slate-100 rounded overflow-hidden">
                <div class="h-full bg-slate-900" style="width: ${totalMilestones ? (verifiedMilestones/totalMilestones)*100 : 0}%"></div>
              </div>
            </div>
          </td>
          <td>
            <span class="badge ${badgeClass}">${c.status}</span>
          </td>
          <td class="text-right">
            <a href="#/contracts/${c.id}" class="btn-subtle text-xs hairline-all">Inspect →</a>
          </td>
        </tr>
      `;
    }).join('');
  },

  // ---------------------------------------------------------------------------
  // AGENT-TO-AGENT NEGOTIATOR
  // ---------------------------------------------------------------------------
  async loadRecentNegotiationIfAvailable() {
    if (this.state.lastNegotiation) {
      this.renderNegotiationResults(this.state.lastNegotiation);
      return;
    }
    try {
      const list = await this.apiRequest('/negotiate');
      if (list && list.length > 0) {
        this.state.lastNegotiation = list[0];
        this.renderNegotiationResults(list[0]);
      }
    } catch (e) {
      // ignore
    }
  },

  extractPriceFromBrief(text) {
    if (!text) return null;
    const matchDollar = text.match(/\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)/);
    if (matchDollar) return parseFloat(matchDollar[1].replace(/,/g, ''));
    const matchWord = text.match(/(?:budget|ask|price|rate|cost)\s*(?:is|of|:)?\s*([0-9]+(?:,[0-9]{3})*)/i);
    if (matchWord) return parseFloat(matchWord[1].replace(/,/g, ''));
    const matchDigits = text.match(/\b([0-9]{3,5})\b/);
    if (matchDigits) return parseFloat(matchDigits[1]);
    return null;
  },

  syncBoundsFromBriefs(force = false) {
    const buyerBrief = document.getElementById('buyer-brief')?.value || '';
    const sellerBrief = document.getElementById('seller-brief')?.value || '';
    const buyerCapInput = document.getElementById('buyer-max-budget');
    const sellerFloorInput = document.getElementById('seller-min-price');

    if (buyerCapInput && (force || !buyerCapInput.value)) {
      const p = this.extractPriceFromBrief(buyerBrief);
      if (p !== null) buyerCapInput.value = p;
    }
    if (sellerFloorInput && (force || !sellerFloorInput.value)) {
      const p = this.extractPriceFromBrief(sellerBrief);
      if (p !== null) sellerFloorInput.value = p;
    }
  },

  loadNegotiatorPreset(key) {
    if (key === 'fintech') {
      document.getElementById('buyer-brief').value = 'Need high-performance payment API built with FastAPI and PostgreSQL. Budget $3,000 USD, timeline 21 days.';
      document.getElementById('seller-brief').value = 'Senior fintech API developer. Rate $4,000 USD, delivery 28 days with complete test suite and PayPal sandbox integration.';
    } else if (key === 'saas') {
      document.getElementById('buyer-brief').value = 'Build full-stack multi-tenant SaaS billing dashboard with Stripe & PayPal hooks. Budget $4,500 USD, timeline 30 days.';
      document.getElementById('seller-brief').value = 'Full-stack senior architect with 8 years experience. Asking $5,800 USD, timeline 35 days with comprehensive docs.';
    } else if (key === 'mobile') {
      document.getElementById('buyer-brief').value = 'Cross-platform mobile app in React Native for logistics tracking. Budget $2,200 USD, timeline 14 days.';
      document.getElementById('seller-brief').value = 'Mobile engineering specialist. Rate is $3,000 USD for complete iOS and Android build with offline caching.';
    }
    this.syncBoundsFromBriefs(true);
    this.showToast(`Loaded "${key.toUpperCase()}" scenario preset`, 'info');
  },

  async startNegotiation() {
    const btn = document.getElementById('btn-start-negotiation');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-spin mr-2">⟳</span> Agents Negotiating...`;

    const engineSelect = document.getElementById('neg-engine-select').value;
    const forceRuleBased = (engineSelect === 'rule_based');

    const buyerCapInput = document.getElementById('buyer-max-budget');
    const sellerFloorInput = document.getElementById('seller-min-price');
    const stretchInput = document.getElementById('buyer-over-budget-pct');
    const walkAwayInput = document.getElementById('neg-walk-away-gap');

    const buyerCap = buyerCapInput && buyerCapInput.value !== '' ? parseFloat(buyerCapInput.value) : null;
    const sellerFloor = sellerFloorInput && sellerFloorInput.value !== '' ? parseFloat(sellerFloorInput.value) : null;
    const stretchPct = stretchInput && stretchInput.value !== '' ? parseFloat(stretchInput.value) : 0.0;
    const walkAwayGap = walkAwayInput && walkAwayInput.value !== '' ? parseFloat(walkAwayInput.value) : 40.0;

    const payload = {
      buyer_brief: document.getElementById('buyer-brief').value.trim(),
      seller_brief: document.getElementById('seller-brief').value.trim(),
      client_name: document.getElementById('buyer-name').value.trim(),
      client_email: document.getElementById('buyer-email').value.trim(),
      client_country: document.getElementById('buyer-country').value.trim(),
      developer_name: document.getElementById('seller-name').value.trim(),
      developer_email: document.getElementById('seller-email').value.trim(),
      developer_country: document.getElementById('seller-country').value.trim(),
      base_currency: document.getElementById('buyer-currency').value,
      payout_currency: document.getElementById('seller-currency').value,
      max_rounds: parseInt(document.getElementById('neg-max-rounds').value, 10) || 5,
      auto_create_contract: document.getElementById('neg-auto-contract').checked,
      force_rule_based: forceRuleBased,
      buyer_max_budget: !isNaN(buyerCap) ? buyerCap : null,
      seller_min_price: !isNaN(sellerFloor) ? sellerFloor : null,
      max_over_budget_pct: !isNaN(stretchPct) ? stretchPct : 0.0,
      walk_away_gap_pct: !isNaN(walkAwayGap) ? walkAwayGap : 40.0,
    };

    try {
      const response = await this.apiRequest('/negotiate', 'POST', payload);
      this.state.lastNegotiation = response;
      this.renderNegotiationResults(response);
      if (response.status === 'no_deal') {
        this.showToast('Negotiation concluded: NO DEAL (bounds exceeded).', 'warning');
      } else {
        this.showToast('Negotiation concluded successfully! Terms agreed.', 'success');
      }
    } catch (e) {
      this.showToast(`Negotiation failed: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  },

  renderNegotiationResults(data) {
    const container = document.getElementById('negotiation-results-container');
    container.classList.remove('hidden');

    document.getElementById('neg-rounds-count').textContent = `${data.total_rounds} Rounds Completed`;
    document.getElementById('neg-model-used-badge').textContent = `ENGINE: ${data.model_used}`;

    // Render Active Enforced Bounds Card above transcript
    if (data.bounds) {
      const b = data.bounds;
      const bCapEl = document.getElementById('neg-bound-buyer-cap');
      if (bCapEl) bCapEl.textContent = `$${b.effective_buyer_cap.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
      const stretchEl = document.getElementById('neg-bound-stretch');
      if (stretchEl) stretchEl.textContent = `Cap: $${b.buyer_max_budget.toLocaleString('en-US')} (+${b.max_over_budget_pct}% stretch)`;
      const sFloorEl = document.getElementById('neg-bound-seller-floor');
      if (sFloorEl) sFloorEl.textContent = `$${b.seller_min_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
      const gapEl = document.getElementById('neg-bound-gap');
      if (gapEl) gapEl.textContent = `${b.walk_away_gap_pct.toFixed(1)}%`;
      const roundsEl = document.getElementById('neg-bound-rounds');
      if (roundsEl) roundsEl.textContent = `${b.max_rounds} Rounds`;
      const stagEl = document.getElementById('neg-bound-stagnation');
      if (stagEl) stagEl.textContent = `Stagnation: ${Math.min(3, b.max_rounds)} rds`;
    }

    const consensusBanner = document.getElementById('neg-consensus-banner');
    const noDealBanner = document.getElementById('neg-no-deal-banner');

    if (data.status === 'no_deal') {
      if (consensusBanner) consensusBanner.classList.add('hidden');
      if (noDealBanner) {
        noDealBanner.classList.remove('hidden');
        document.getElementById('neg-no-deal-reason').textContent =
          data.termination_reason || 'Terms are irreconcilable under active bounds.';
      }
    } else if (data.agreed_terms) {
      if (noDealBanner) noDealBanner.classList.add('hidden');
      if (consensusBanner) {
        consensusBanner.classList.remove('hidden');
        document.getElementById('neg-agreed-headline').textContent = 
          `Agreed at $${data.agreed_terms.final_price.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${data.agreed_terms.base_currency}`;
        document.getElementById('neg-concession-summary').textContent = data.agreed_terms.concession_summary;

        if (data.contract_id) {
          const linkBox = document.getElementById('neg-contract-link-box');
          linkBox.classList.remove('hidden');
          document.getElementById('neg-created-contract-id').textContent = data.contract_id.substring(0, 8);
          document.getElementById('neg-view-contract-btn').href = `#/contracts/${data.contract_id}`;
        }
      }
    }

    // Render Transcript Feed
    const feed = document.getElementById('neg-transcript-feed');
    feed.innerHTML = (data.rounds || []).map(round => {
      const b = round.buyer_turn;
      const s = round.seller_turn;

      return `
        <div class="space-y-4 hairline-b pb-8">
          <div class="flex items-center justify-between font-mono text-xs text-slate-400">
            <span>ROUND 0${round.round_number} DELIBERATION</span>
            <span class="text-slate-600 font-medium">Price Spread: $${round.price_gap.toFixed(2)} (${round.status.toUpperCase()})</span>
          </div>

          <!-- BUYER TURN -->
          <div class="hairline-all p-5 bg-white space-y-3">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <span class="w-2.5 h-2.5 bg-blue-600 rounded-sm"></span>
                <span class="font-semibold text-xs text-slate-900">${this.escapeHtml(b.sender_label)}</span>
              </div>
              <div class="flex items-center space-x-3 font-mono text-xs">
                <span class="font-semibold text-blue-700 bg-blue-50 px-2 py-0.5 border border-blue-200 rounded">$${b.offer.price.toLocaleString('en-US', { minimumFractionDigits: 2 })} USD</span>
                <span class="text-slate-500">${b.offer.timeline_days} Days</span>
              </div>
            </div>

            <p class="text-sm text-slate-700 leading-relaxed">${this.escapeHtml(b.message)}</p>

            <details class="text-xs text-slate-500 pt-2 hairline-t">
              <summary class="cursor-pointer font-mono hover:text-slate-800 transition">Agent Internal Reasoning & Strategy</summary>
              <div class="p-3 mt-2 bg-[#FAFAFA] hairline-all font-mono text-[11px] text-slate-600 leading-normal">
                ${this.escapeHtml(b.reasoning)}
              </div>
            </details>
          </div>

          <!-- SELLER TURN -->
          <div class="hairline-all p-5 bg-white space-y-3">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <span class="w-2.5 h-2.5 bg-emerald-600 rounded-sm"></span>
                <span class="font-semibold text-xs text-slate-900">${this.escapeHtml(s.sender_label)}</span>
              </div>
              <div class="flex items-center space-x-3 font-mono text-xs">
                <span class="font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 border border-emerald-200 rounded">$${s.offer.price.toLocaleString('en-US', { minimumFractionDigits: 2 })} USD</span>
                <span class="text-slate-500">${s.offer.timeline_days} Days</span>
              </div>
            </div>

            <p class="text-sm text-slate-700 leading-relaxed">${this.escapeHtml(s.message)}</p>

            <details class="text-xs text-slate-500 pt-2 hairline-t">
              <summary class="cursor-pointer font-mono hover:text-slate-800 transition">Agent Internal Reasoning & Strategy</summary>
              <div class="p-3 mt-2 bg-[#FAFAFA] hairline-all font-mono text-[11px] text-slate-600 leading-normal">
                ${this.escapeHtml(s.reasoning)}
              </div>
            </details>
          </div>
        </div>
      `;
    }).join('');

    container.scrollIntoView({ behavior: 'smooth' });
  },

  // ---------------------------------------------------------------------------
  // CONTRACT BUILDER
  // ---------------------------------------------------------------------------
  async generateContractFromBrief() {
    const btn = document.getElementById('btn-generate-milestones');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-spin mr-2">⟳</span> Synthesizing Milestones...`;

    const payload = {
      brief: document.getElementById('builder-brief').value.trim(),
      client_name: document.getElementById('builder-client-name').value.trim(),
      client_country: document.getElementById('builder-client-country').value.trim(),
      developer_name: document.getElementById('builder-dev-name').value.trim(),
      developer_country: document.getElementById('builder-dev-country').value.trim(),
      budget: parseFloat(document.getElementById('builder-budget').value),
      base_currency: document.getElementById('builder-base-curr').value,
      payout_currency: document.getElementById('builder-payout-curr').value,
      fx_strategy: document.getElementById('builder-fx-strat').value,
    };

    try {
      const draft = await this.apiRequest('/ai/generate-contract', 'POST', payload);
      this.state.generatedDraft = { draft, payload };
      this.renderDraftPreview(draft, payload);
      this.showToast('Milestones generated successfully!', 'success');
    } catch (e) {
      this.showToast(`Generation failed: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  },

  renderDraftPreview(draft, payload) {
    const container = document.getElementById('builder-preview-container');
    container.classList.remove('hidden');

    document.getElementById('preview-contract-title').textContent = draft.suggested_title;
    document.getElementById('preview-total-amount').textContent = `$${draft.suggested_total_amount.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${payload.base_currency}`;
    document.getElementById('preview-fx-strategy').textContent = draft.recommended_fx_strategy;
    document.getElementById('preview-generator-source').textContent = draft.generator_source || 'AI Engine';
    document.getElementById('preview-dispute-summary').textContent = draft.dispute_protocol_summary;

    const list = document.getElementById('preview-milestones-list');
    list.innerHTML = (draft.milestones || []).map((m, idx) => `
      <div class="p-6 space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-3">
            <span class="w-6 h-6 rounded-sm bg-slate-900 text-white font-mono text-xs flex items-center justify-center font-bold">
              ${m.order_index || idx + 1}
            </span>
            <span class="font-semibold text-slate-900">${this.escapeHtml(m.title)}</span>
          </div>
          <div class="font-mono text-sm font-semibold text-slate-900">
            $${m.estimated_amount.toLocaleString('en-US', { minimumFractionDigits: 2 })} (${(m.amount_percentage * 100).toFixed(0)}%)
          </div>
        </div>

        <p class="text-sm text-slate-600 leading-relaxed">${this.escapeHtml(m.description)}</p>

        <div class="space-y-1.5 pt-2">
          <div class="font-mono text-xs text-slate-400 uppercase">Verifiable Acceptance Criteria:</div>
          <ul class="space-y-1 text-xs text-slate-700">
            ${(m.acceptance_criteria || []).map(crit => `
              <li class="flex items-center space-x-2">
                <span class="text-emerald-600 font-mono">✓</span>
                <span>${this.escapeHtml(crit.description)}</span>
                <span class="font-mono text-[10px] text-slate-400 uppercase">[${crit.verification_method}]</span>
              </li>
            `).join('')}
          </ul>
        </div>
      </div>
    `).join('');

    container.scrollIntoView({ behavior: 'smooth' });
  },

  async confirmAndDeployContract() {
    if (!this.state.generatedDraft) return;
    const { draft, payload } = this.state.generatedDraft;

    const contractPayload = {
      title: draft.suggested_title,
      brief: payload.brief,
      client_name: payload.client_name,
      client_email: `${payload.client_name.toLowerCase().replace(/\s+/g, '')}@client.com`,
      client_country: payload.client_country,
      developer_name: payload.developer_name,
      developer_email: `${payload.developer_name.toLowerCase().replace(/\s+/g, '')}@devstudio.com`,
      developer_country: payload.developer_country,
      base_currency: payload.base_currency,
      payout_currency: payload.payout_currency,
      total_amount: draft.suggested_total_amount,
      fx_strategy: draft.recommended_fx_strategy,
      locked_fx_rate: 84.50,
      milestones: (draft.milestones || []).map((m, idx) => ({
        title: m.title,
        description: m.description,
        amount: m.estimated_amount,
        order_index: m.order_index || idx + 1,
        verification_type: 'CODE_DELIVERY',
        acceptance_criteria: m.acceptance_criteria || [],
      })),
    };

    try {
      const created = await this.apiRequest('/contracts', 'POST', contractPayload);
      this.showToast('Escrow contract deployed to TrustBridge!', 'success');
      window.location.hash = `#/contracts/${created.id}`;
    } catch (e) {
      this.showToast(`Deployment failed: ${e.message}`, 'error');
    }
  },

  // ---------------------------------------------------------------------------
  // CONTRACT DETAIL
  // ---------------------------------------------------------------------------
  async loadContractDetail(contractId) {
    try {
      const contract = await this.apiRequest(`/contracts/${contractId}`);
      this.state.activeContract = contract;
      this.renderContractDetail(contract);
    } catch (e) {
      this.showToast(`Could not load contract: ${e.message}`, 'error');
    }
  },

  renderContractDetail(c) {
    document.getElementById('detail-contract-id').textContent = c.id;
    document.getElementById('detail-contract-title').textContent = c.title;

    const statusBadge = document.getElementById('detail-contract-status');
    statusBadge.textContent = c.status;
    statusBadge.className = `badge ${
      c.status === 'ACTIVE' ? 'badge-active' :
      c.status === 'COMPLETED' ? 'badge-verified' :
      c.status === 'DISPUTED' ? 'badge-disputed' : 'badge-draft'
    }`;

    document.getElementById('detail-client-name').textContent = c.client_name;
    document.getElementById('detail-client-email').textContent = `${c.client_email} (${c.client_country})`;
    const clientTrustEl = document.getElementById('detail-client-trust');
    if (clientTrustEl) clientTrustEl.textContent = `Trust Score: ${(c.client_trust_score ?? 50.0).toFixed(1)}/100`;

    document.getElementById('detail-dev-name').textContent = c.developer_name;
    document.getElementById('detail-dev-email').textContent = `${c.developer_email} (${c.developer_country})`;
    const devTrustEl = document.getElementById('detail-dev-trust');
    if (devTrustEl) devTrustEl.textContent = `Trust Score: ${(c.freelancer_trust_score ?? 50.0).toFixed(1)}/100`;

    const rate = c.locked_fx_rate || 84.50;
    const payoutEst = (c.total_amount * rate).toLocaleString('en-US', { maximumFractionDigits: 2 });
    document.getElementById('detail-total-amount').textContent = `$${c.total_amount.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${c.base_currency}`;
    document.getElementById('detail-fx-info').textContent = `${c.fx_strategy} @ ${rate.toFixed(2)} (≈ ${c.payout_currency} ${payoutEst})`;

    document.getElementById('detail-paypal-order').textContent = c.paypal_order_id || 'Not Yet Funded';
    document.getElementById('detail-funded-at').textContent = c.funded_at ? new Date(c.funded_at).toLocaleString() : 'Dual-Custody Pending';

    // Action bar buttons
    const actionBar = document.getElementById('detail-action-bar');
    actionBar.innerHTML = '';
    if (c.status === 'DRAFT') {
      const fundBtn = document.createElement('button');
      fundBtn.className = 'btn-primary text-xs';
      fundBtn.innerHTML = `<span>Fund Contract via PayPal Sandbox</span> <span>→</span>`;
      fundBtn.onclick = () => this.fundContractWithPayPal(c.id, c.total_amount);
      actionBar.appendChild(fundBtn);
    }

    // Milestones timeline
    const list = document.getElementById('detail-milestones-list');
    list.innerHTML = (c.milestones || []).map(m => {
      let mBadgeClass = 'badge-draft';
      if (m.status === 'FUNDED') mBadgeClass = 'badge-active';
      if (m.status === 'SUBMITTED') mBadgeClass = 'badge-draft';
      if (m.status === 'VERIFIED') mBadgeClass = 'badge-verified';
      if (m.status === 'RELEASED') mBadgeClass = 'badge-released';
      if (m.status === 'DISPUTED') mBadgeClass = 'badge-disputed';

      const payoutAmt = m.payout_amount ? `${c.payout_currency} ${m.payout_amount.toLocaleString('en-US', { maximumFractionDigits: 2 })}` : `≈ ${c.payout_currency} ${(m.amount * rate).toFixed(2)}`;

      return `
        <div class="p-6 space-y-4">
          <div class="flex flex-col md:flex-row md:items-center justify-between gap-2">
            <div class="flex items-center space-x-3">
              <span class="w-6 h-6 rounded-sm bg-slate-900 text-white font-mono text-xs flex items-center justify-center font-bold">
                ${m.order_index}
              </span>
              <span class="font-semibold text-slate-900">${this.escapeHtml(m.title)}</span>
              <span class="badge ${mBadgeClass}">${m.status}</span>
            </div>
            <div class="font-mono text-sm font-semibold text-slate-900">
              $${m.amount.toLocaleString('en-US', { minimumFractionDigits: 2 })} ${c.base_currency} <span class="text-xs text-slate-400">(${payoutAmt})</span>
            </div>
          </div>

          <p class="text-sm text-slate-600 leading-relaxed">${this.escapeHtml(m.description)}</p>

          <!-- ACCEPTANCE CRITERIA -->
          <div class="space-y-1.5 pt-1">
            <div class="font-mono text-xs text-slate-400 uppercase">Verification Criteria:</div>
            <ul class="space-y-1 text-xs text-slate-700">
              ${(m.acceptance_criteria || []).map(crit => `
                <li class="flex items-center space-x-2">
                  <span class="text-emerald-600 font-mono">✓</span>
                  <span>${this.escapeHtml(crit.description)}</span>
                  <span class="font-mono text-[10px] text-slate-400 uppercase">[${crit.verification_method}]</span>
                </li>
              `).join('')}
            </ul>
          </div>

          <!-- SUBMITTED EVIDENCE DOSSIER (IF ANY) -->
          ${m.evidence_url ? `
            <div class="hairline-all p-4 bg-[#FAFAFA] space-y-2 text-xs">
              <div class="flex items-center justify-between">
                <span class="font-mono text-slate-500 uppercase">Submitted Evidence Dossier</span>
                <span class="font-mono text-emerald-700 font-semibold">SCORE: ${((m.verification_score || 1.0) * 100).toFixed(0)}% VERIFIED</span>
              </div>
              <div class="font-mono text-slate-700 break-all">
                PR / Artifact: <a href="${m.evidence_url}" target="_blank" class="text-blue-600 underline">${m.evidence_url}</a>
              </div>
              ${m.evidence_notes ? `<div class="text-slate-600">${this.escapeHtml(m.evidence_notes)}</div>` : ''}
              ${m.paypal_payout_batch_id ? `
                <div class="font-mono text-purple-700 text-[11px] pt-1">
                  PAYPAL PAYOUT BATCH: ${m.paypal_payout_batch_id} (Captured)
                </div>
              ` : ''}
            </div>
          ` : ''}

          <!-- ACTION BUTTONS PER MILESTONE -->
          <div class="flex flex-wrap items-center gap-3 pt-2">
            ${(m.status === 'FUNDED' || m.status === 'DRAFT') ? `
              <button onclick="TrustBridgeApp.openSubmitModal('${m.id}')" class="btn-primary text-xs">
                <span>Submit Deliverable</span>
                <span>→</span>
              </button>
            ` : ''}

            ${m.status === 'SUBMITTED' ? `
              <button onclick="TrustBridgeApp.verifyMilestone('${m.id}')" class="btn-primary text-xs">
                <span>Run Cryptographic Verification</span>
                <span>→</span>
              </button>
            ` : ''}

            ${m.status === 'VERIFIED' ? `
              <button onclick="TrustBridgeApp.releaseMilestone('${m.id}')" class="btn-primary text-xs bg-emerald-700 border-emerald-700 hover:bg-emerald-800">
                <span>Approve & Release Funds</span>
                <span>→</span>
              </button>
            ` : ''}

            ${(m.status !== 'RELEASED' && m.status !== 'DISPUTED') ? `
              <button onclick="TrustBridgeApp.disputeMilestone('${m.id}')" class="btn-secondary text-xs text-red-600 hover:text-red-700">
                <span>Raise Dispute</span>
              </button>
            ` : ''}
          </div>
        </div>
      `;
    }).join('');

    // Audit logs
    const auditTbody = document.getElementById('detail-audit-logs');
    if (c.audit_logs && c.audit_logs.length > 0) {
      auditTbody.innerHTML = c.audit_logs.map(log => `
        <tr>
          <td class="font-mono text-xs text-slate-500">${new Date(log.timestamp).toLocaleString()}</td>
          <td class="font-mono text-xs text-slate-700 font-medium">${log.actor_role}</td>
          <td><span class="font-mono text-xs text-slate-900 font-semibold">${log.action}</span></td>
          <td class="text-xs text-slate-600">${this.escapeHtml(log.description)}</td>
        </tr>
      `).join('');
    } else {
      auditTbody.innerHTML = `<tr><td colspan="4" class="text-center py-6 text-slate-400 font-mono text-xs">No audit events recorded yet</td></tr>`;
    }
  },

  async fundContractWithPayPal(contractId, amount) {
    try {
      this.showToast('Creating PayPal sandbox order...', 'info');
      const orderRes = await this.apiRequest('/paypal/order/create', 'POST', {
        contract_id: contractId,
        amount: amount,
      });

      const orderId = orderRes.order_id;
      this.showToast(`Order created: ${orderId}. Locking escrow funds...`, 'info');

      await this.apiRequest(`/contracts/${contractId}/fund`, 'POST', {
        paypal_order_id: orderId,
      });

      this.showToast('Escrow vault funded! Contract is now ACTIVE.', 'success');
      this.loadContractDetail(contractId);
    } catch (e) {
      this.showToast(`Funding failed: ${e.message}`, 'error');
    }
  },

  openSubmitModal(milestoneId) {
    document.getElementById('modal-milestone-id').value = milestoneId;
    document.getElementById('modal-submit-evidence').classList.remove('hidden');
  },

  closeSubmitModal() {
    document.getElementById('modal-submit-evidence').classList.add('hidden');
  },

  async executeSubmitEvidence() {
    const milestoneId = document.getElementById('modal-milestone-id').value;
    const url = document.getElementById('modal-evidence-url').value.trim();
    const sha = document.getElementById('modal-evidence-sha').value.trim();
    const passed = parseInt(document.getElementById('modal-tests-passed').value, 10);
    const total = parseInt(document.getElementById('modal-tests-total').value, 10);
    const notes = document.getElementById('modal-evidence-notes').value.trim();

    try {
      await this.apiRequest(`/milestones/${milestoneId}/submit`, 'POST', {
        evidence_url: url,
        commit_sha: sha,
        evidence_notes: notes,
        evidence_payload: { tests_passed: passed, tests_total: total },
      });
      this.closeSubmitModal();
      this.showToast('Deliverable submitted and automatically verified!', 'success');
      this.fetchNotifications();
      if (this.state.activeContract) {
        this.loadContractDetail(this.state.activeContract.id);
      }
    } catch (e) {
      this.showToast(`Submission failed: ${e.message}`, 'error');
    }
  },

  async verifyMilestone(milestoneId) {
    try {
      await this.apiRequest(`/milestones/${milestoneId}/verify`, 'POST', {
        override_score: 1.0,
        notes: 'Objective criteria verified via test runner',
      });
      this.showToast('Milestone verified! Ready for release.', 'success');
      this.fetchNotifications();
      if (this.state.activeContract) {
        this.loadContractDetail(this.state.activeContract.id);
      }
    } catch (e) {
      this.showToast(`Verification failed: ${e.message}`, 'error');
    }
  },

  async releaseMilestone(milestoneId) {
    try {
      await this.apiRequest(`/milestones/${milestoneId}/release`, 'POST', {
        release_note: 'Approved by client. Programmatic payout executed.',
      });
      this.showToast('Funds released! Developer payout transmitted.', 'success');
      this.fetchNotifications();
      if (this.state.activeContract) {
        this.loadContractDetail(this.state.activeContract.id);
      }
    } catch (e) {
      this.showToast(`Release failed: ${e.message}`, 'error');
    }
  },

  async disputeMilestone(milestoneId) {
    const reason = prompt('Please enter dispute reason for PayPal mediation protocol:', 'Scope deliverables incomplete');
    if (!reason) return;

    try {
      await this.apiRequest(`/milestones/${milestoneId}/dispute`, 'POST', {
        reason: reason,
        evidence_notes: 'Evidence submitted for administrative dispute review',
      });
      this.showToast('Dispute initiated. Milestone frozen in DISPUTED state.', 'error');
      this.fetchNotifications();
      if (this.state.activeContract) {
        this.loadContractDetail(this.state.activeContract.id);
      }
    } catch (e) {
      this.showToast(`Dispute failed: ${e.message}`, 'error');
    }
  },

  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
};

document.addEventListener('DOMContentLoaded', () => {
  TrustBridgeApp.init();
});
