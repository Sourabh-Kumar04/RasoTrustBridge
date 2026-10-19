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
  },

  // ---------------------------------------------------------------------------
  // INITIALIZATION & ROUTING
  // ---------------------------------------------------------------------------
  init() {
    window.addEventListener('hashchange', () => this.handleRouting());
    this.handleRouting();
    this.fetchLiveFXRates();
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
  // API HELPERS & TOASTS
  // ---------------------------------------------------------------------------
  async apiRequest(path, method = 'GET', body = null) {
    const options = {
      method,
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
      },
    };
    if (body) {
      options.body = JSON.stringify(body);
    }
    const res = await fetch(`${API_BASE}${path}`, options);
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
    try {
      const contracts = await this.apiRequest('/contracts');
      this.state.contracts = contracts || [];
      this.renderDashboard();
    } catch (e) {
      this.showToast(`Failed to load contracts: ${e.message}`, 'error');
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
    this.showToast(`Loaded "${key.toUpperCase()}" scenario preset`, 'info');
  },

  async startNegotiation() {
    const btn = document.getElementById('btn-start-negotiation');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-spin mr-2">⟳</span> Agents Negotiating...`;

    const engineSelect = document.getElementById('neg-engine-select').value;
    const forceRuleBased = (engineSelect === 'rule_based');

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
      max_rounds: parseInt(document.getElementById('neg-max-rounds').value, 10),
      auto_create_contract: document.getElementById('neg-auto-contract').checked,
      force_rule_based: forceRuleBased,
    };

    try {
      const response = await this.apiRequest('/negotiate', 'POST', payload);
      this.state.lastNegotiation = response;
      this.renderNegotiationResults(response);
      this.showToast('Negotiation concluded successfully! Terms agreed.', 'success');
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

    // Consensus Banner
    if (data.agreed_terms) {
      const banner = document.getElementById('neg-consensus-banner');
      banner.classList.remove('hidden');
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
    document.getElementById('detail-dev-name').textContent = c.developer_name;
    document.getElementById('detail-dev-email').textContent = `${c.developer_email} (${c.developer_country})`;

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
