'use strict';

const POSITIONS = ['top', 'jungle', 'middle', 'bottom', 'utility'];
const POS_CN = { top: '上单', jungle: '打野', middle: '中路', bottom: '下路', utility: '辅助' };

const app = {
    replayPath: new URLSearchParams(location.search).get('replay'),
    draft: null,
    selectedEnId: '',   // currently selected my champion
    selectedLane: '',
    enemyPositions: {}, // override map: enId -> lane
    tier: 'emerald_plus',
    _pollTimer: null,
    _adviceId: 0,       // race-condition guard

    async init() {
        // Persist token from URL into sessionStorage so subsequent fetches keep it
        const urlToken = new URLSearchParams(location.search).get('token');
        if (urlToken) sessionStorage.setItem('push_token', urlToken);
        this._token = sessionStorage.getItem('push_token') || '';

        document.getElementById('tier-select').addEventListener('change', e => {
            this.tier = e.target.value;
            this.loadAdvice();
        });
        document.getElementById('refresh-btn').addEventListener('click', () => this.loadAdvice());

        await this.loadDraft();
        if (!this.replayPath) {
            this._pollTimer = setInterval(() => this.loadDraft(), 3000);
        }
    },

    _apiUrl(path) {
        const u = new URL(path, location.origin);
        if (this._token) u.searchParams.set('token', this._token);
        if (this.replayPath && path === '/api/draft') {
            u.searchParams.set('replay', this.replayPath);
        }
        return u.toString();
    },

    async _apiFetch(path, options = {}) {
        const url = this._apiUrl(path);
        return fetch(url, options);
    },

    async loadDraft() {
        try {
            const r = await this._apiFetch('/api/draft');
            const data = await r.json();

            if (!data.in_champ_select) {
                const reason = data.reason || '未在选人室';
                const isWaiting = reason.includes('等待');
                this._setStatus(reason, isWaiting ? 'waiting' : 'warn');
                this._showWaiting(reason);
                return;
            }

            const changed = JSON.stringify(data) !== JSON.stringify(this.draft);
            this.draft = data;

            document.getElementById('hdr-version').textContent = `v${data.version}`;
            document.getElementById('hdr-mode').innerHTML =
                `<span class="badge">${data.is_custom ? '自定义' : (data.queue_id === 420 ? '排位赛' : '匹配')}</span>`;
            this._setStatus('', '');

            this._renderTeams();

            // Auto-select local player on first load
            if (!this.selectedEnId) {
                const local = data.my_team.find(m => m.is_local);
                if (local && local.en_id) {
                    this.selectedEnId = local.en_id;
                    this.selectedLane = local.effective_pos || local.position;
                    this.loadAdvice();
                }
            } else if (changed) {
                this.loadAdvice();
            }
        } catch (e) {
            this._setStatus(`连接失败: ${e.message}`, 'warn');
        }
    },

    _showWaiting(msg) {
        const waiting = document.getElementById('waiting-msg');
        const content = document.getElementById('advice-content');
        waiting.style.display = 'flex';
        content.style.display = 'none';
        waiting.querySelector('div:last-child').textContent = msg;
    },

    _setStatus(msg, type) {
        const el = document.getElementById('hdr-status');
        el.textContent = msg;
        el.style.color = type === 'warn' ? '#b07830' : '#4a9fd4';
    },

    _renderTeams() {
        this._renderTeamList('my-team-list', this.draft.my_team, false);
        this._renderTeamList('enemy-team-list', this.draft.enemy_team, true);
    },

    _renderTeamList(containerId, members, isEnemy) {
        const container = document.getElementById(containerId);
        container.innerHTML = '';

        for (const m of members) {
            const row = document.createElement('div');
            row.className = 'champ-row' + (m.is_local ? ' is-me' : '');
            if (!isEnemy && m.en_id === this.selectedEnId) row.classList.add('selected');

            const imgSrc = m.avatar_url || '';
            const posLabel = isEnemy
                ? (this.enemyPositions[m.en_id] || m.effective_pos || m.position || '?')
                : (m.effective_pos || m.position || '?');
            const posCn = POS_CN[posLabel] || posLabel;
            const iconClass = isEnemy ? 'enemy-icon' : (m.is_local ? 'me-icon' : '');
            const posClass = m.is_local ? 'me-pos' : '';
            const champName = m.zh_name || m.en_id || '未知';

            row.innerHTML = `
                <img class="champ-icon ${iconClass}" src="${imgSrc}" alt="${champName}" onerror="this.style.visibility='hidden'">
                <div class="champ-info">
                    <div class="champ-name">${champName}</div>
                    <div class="champ-pos ${posClass}">${posCn}${m.is_local ? ' (我)' : ''}</div>
                </div>
            `;

            if (isEnemy && m.en_id) {
                const sel = document.createElement('select');
                sel.className = 'pos-select';
                sel.title = '调整对手分路';
                for (const p of POSITIONS) {
                    const opt = document.createElement('option');
                    opt.value = p;
                    opt.textContent = POS_CN[p];
                    if (p === posLabel) opt.selected = true;
                    sel.appendChild(opt);
                }
                sel.addEventListener('change', e => {
                    this.enemyPositions[m.en_id] = e.target.value;
                    this._renderTeams();
                    this.loadAdvice();
                });
                row.appendChild(sel);
            } else if (!isEnemy && m.en_id) {
                row.addEventListener('click', () => {
                    this.selectedEnId = m.en_id;
                    this.selectedLane = m.effective_pos || m.position;
                    this._renderTeams();
                    this.loadAdvice();
                });
            }

            container.appendChild(row);
        }
    },

    // ── Build enemy team list with position overrides ──────────────────────────
    _enemyTeamPayload() {
        if (!this.draft) return [];
        return this.draft.enemy_team.map(m => ({
            en_id: m.en_id,
            zh_name: m.zh_name,
            lane: this.enemyPositions[m.en_id] || m.effective_pos || m.position || 'top',
        }));
    },

    _myTeamPayload() {
        if (!this.draft) return [];
        return this.draft.my_team.map(m => ({
            en_id: m.en_id,
            zh_name: m.zh_name,
            lane: m.effective_pos || m.position || 'top',
        }));
    },

    async loadAdvice() {
        if (!this.draft || !this.selectedEnId) return;

        const id = ++this._adviceId;

        const waiting = document.getElementById('waiting-msg');
        const content = document.getElementById('advice-content');
        waiting.style.display = 'none';
        content.style.display = 'block';

        // Reset tips to loading state
        this._setTipsLoading();

        const body = {
            my_en_id: this.selectedEnId,
            my_lane:  this.selectedLane,
            my_team:  this._myTeamPayload(),
            enemy_team: this._enemyTeamPayload(),
            tier:     this.tier,
        };

        // ── Step 1: fast build+runes (no Claude) ──────────────────────────────
        try {
            const r = await this._apiFetch('/api/advise/build', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            if (id !== this._adviceId) return;
            const data = await r.json();
            this._renderBuild(data);
        } catch (e) {
            if (id !== this._adviceId) return;
            document.getElementById('build-body').innerHTML = `<div class="no-data">出装加载失败: ${e.message}</div>`;
        }

        // ── Step 2: slow tips (Claude) ─────────────────────────────────────────
        try {
            const r = await this._apiFetch('/api/advise/tips', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            if (id !== this._adviceId) return;
            const data = await r.json();
            this._renderTips(data);
        } catch (e) {
            if (id !== this._adviceId) return;
            this._renderTipsError(`加载失败: ${e.message}`);
        }
    },

    // ── Render: Build + Runes + Opponent ──────────────────────────────────────

    _renderBuild(data) {
        // Opponent
        const opp = data.opponent;
        if (opp) {
            document.getElementById('opp-name').textContent =
                `${opp.zh_name || opp.en_id}（${opp.en_id}）`;
            const av = document.getElementById('opp-avatar');
            av.src = opp.avatar_url || '';
            av.style.display = opp.avatar_url ? '' : 'none';
        } else {
            document.getElementById('opp-name').textContent = '未检测到对线对手';
            document.getElementById('opp-avatar').style.display = 'none';
        }

        // Matchup WR
        const m = data.matchup;
        const wrEl = document.getElementById('wr-display');
        if (m && m.win_rate != null) {
            const wr = m.win_rate;
            let cls = 'wr-neutral';
            if (wr >= 52) cls = 'wr-good';
            else if (wr <= 48) cls = 'wr-bad';
            const stale = m.stale ? `<span class="stale-warn">⚠ 数据可能过期</span>` : '';
            wrEl.innerHTML = `<span class="${cls}">对位胜率 <b>${wr.toFixed(1)}%</b></span>
                <span class="sample-size">· ${m.sample_size ? m.sample_size.toLocaleString() : '?'} 局</span>${stale}`;
        } else {
            wrEl.textContent = '对位胜率：暂无数据';
        }

        // Build
        const b = data.build;
        const buildBody = document.getElementById('build-body');
        if (!b) {
            buildBody.innerHTML = '<div class="no-data">暂无出装数据</div>';
        } else {
            document.getElementById('build-source').textContent = b.source || '';
            const staleNote = b.stale ? `<div class="stale-warn" style="margin-bottom:8px">⚠ ${b.stale_reason || '数据可能过期'}</div>` : '';
            buildBody.innerHTML = staleNote + [
                { label: '起手', items: b.starter, sep: '→' },
                { label: '鞋子', items: b.boots,   sep: '→' },
                { label: '核心', items: b.core,     sep: '→' },
                { label: '按需', items: b.situational, sep: '/' },
            ].filter(r => r.items && r.items.length > 0)
             .map(r => this._buildRow(r.label, r.items, r.sep))
             .join('');
        }

        // Runes
        const ru = data.runes;
        const runesBody = document.getElementById('runes-body');
        if (!ru) {
            runesBody.innerHTML = '<div class="no-data">暂无符文数据</div>';
        } else {
            document.getElementById('runes-source').textContent = ru.source || '';
            runesBody.innerHTML = this._runesHtml(ru);
        }
    },

    _buildRow(label, items, sep) {
        const sepChar = sep === '→' ? '<span class="item-sep">→</span>' : '<span class="item-sep-slash">/</span>';
        const chips = items.map((it, i) =>
            `<div class="item-chip">
                <img class="item-img" src="${it.icon_url}" alt="${it.name}" onerror="this.style.visibility='hidden'">
                <span class="item-tooltip">${it.name}</span>
            </div>${i < items.length - 1 ? sepChar : ''}`
        ).join('');
        return `<div class="build-row">
            <span class="build-label">${label}</span>
            <div class="item-list">${chips}</div>
        </div>`;
    },

    _runesHtml(ru) {
        const rows = [];

        // Primary tree
        rows.push(`<div class="rune-row">
            <img class="rune-tree-icon" src="${ru.primary_tree.icon_url}" alt="" onerror="this.style.visibility='hidden'">
            <span class="rune-tree-name">${ru.primary_tree.name}</span>
            <div class="rune-list">
                ${this._runeChip(ru.keystone, 'keystone')}
                ${(ru.primary_perks || []).map(p => this._runeChip(p)).join('<span class="rune-sep">·</span>')}
            </div>
        </div>`);

        // Secondary tree
        rows.push(`<div class="rune-row">
            <img class="rune-tree-icon" src="${ru.secondary_tree.icon_url}" alt="" onerror="this.style.visibility='hidden'">
            <span class="rune-tree-name">${ru.secondary_tree.name}</span>
            <div class="rune-list">
                ${(ru.secondary_perks || []).map(p => this._runeChip(p)).join('<span class="rune-sep">·</span>')}
            </div>
        </div>`);

        // Stat shards
        if (ru.stat_shards && ru.stat_shards.length) {
            rows.push(`<div class="rune-row">
                <div class="rune-tree-icon"></div>
                <span class="rune-tree-name" style="color:#5a6a7a">碎片</span>
                <div class="rune-list">
                    ${ru.stat_shards.map(s =>
                        `<div class="rune-chip">
                            <img class="stat-shard-img" src="${s.icon_url}" alt="${s.name}" onerror="this.style.visibility='hidden'">
                            <span class="item-tooltip" style="bottom:26px">${s.name}</span>
                        </div>`
                    ).join('<span class="rune-sep">·</span>')}
                </div>
            </div>`);
        }

        return rows.join('');
    },

    _runeChip(perk, type) {
        if (!perk) return '';
        const cls = type === 'keystone' ? 'keystone-img' : 'rune-img';
        return `<div class="rune-chip">
            <img class="${cls}" src="${perk.icon_url}" alt="${perk.name}" onerror="this.style.visibility='hidden'">
            <span class="item-tooltip">${perk.name}</span>
        </div>`;
    },

    // ── Render: Claude tips ────────────────────────────────────────────────────

    _setTipsLoading() {
        document.getElementById('tips-spinner').style.display = 'inline-block';
        const loading = '<li><span class="tips-loading">Claude 生成中… <span class="spinner"></span></span></li>';
        document.getElementById('tips-lane').innerHTML = loading;
        document.getElementById('tips-teamfight').innerHTML = loading;
        document.getElementById('tips-comp').innerHTML = loading;
        document.getElementById('comp-section').style.display = '';
    },

    _renderTips(data) {
        document.getElementById('tips-spinner').style.display = 'none';

        if (data.tips_error) {
            this._renderTipsError(data.tips_error);
            return;
        }

        this._fillTipsList('tips-lane', data.lane_tips || [], '');
        this._fillTipsList('tips-teamfight', data.teamfight || [], '');
        this._fillTipsList('tips-comp', data.comp_adjust || [], 'comp');

        if (!data.comp_adjust || data.comp_adjust.length === 0) {
            document.getElementById('comp-section').style.display = 'none';
        }
    },

    _renderTipsError(msg) {
        document.getElementById('tips-spinner').style.display = 'none';
        const errHtml = `<li><span class="tips-error">⚠ ${msg}</span></li>`;
        document.getElementById('tips-lane').innerHTML = errHtml;
        document.getElementById('tips-teamfight').innerHTML = errHtml;
        document.getElementById('tips-comp').innerHTML = errHtml;
    },

    _fillTipsList(elId, tips, type) {
        const ul = document.getElementById(elId);
        if (!tips || tips.length === 0) {
            ul.innerHTML = '<li><span class="no-data">暂无建议</span></li>';
            return;
        }
        const numCls = type === 'comp' ? 'tips-num comp-num' : 'tips-num';
        ul.innerHTML = tips.map((t, i) =>
            `<li><span class="${numCls}">${i + 1}.</span><span>${t}</span></li>`
        ).join('');
    },
};

document.addEventListener('DOMContentLoaded', () => app.init());
