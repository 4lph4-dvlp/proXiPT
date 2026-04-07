// ProxiPT Dashboard App

const API_BASE = '';

// Elements
const elTabs = document.querySelectorAll('.nav-item');
const elPanes = document.querySelectorAll('.tab-pane');
const elPageTitle = document.getElementById('page-title');

const elUptime = document.getElementById('stat-uptime');
const elRequests = document.getElementById('stat-requests');
const elActiveCount = document.getElementById('active-count');

const elOverviewGrid = document.getElementById('overview-grid');
const elSettingsGrid = document.getElementById('settings-grid');
const elToasts = document.getElementById('toast-container');

// Chat Elements
const chatMessages = document.getElementById('chat-messages');
const chatTextarea = document.getElementById('chat-textarea');
const chatSendBtn = document.getElementById('chat-send-btn');
const chatClearBtn = document.getElementById('chat-clear-btn');
const chatModelSelect = document.getElementById('chat-model-select');
const chatSystemPrompt = document.getElementById('chat-system-prompt');

// State
let isSetupMode = new Set();
let currentData = null;
let chatHistory = [];
let isGenerating = false;

// Format Utils
function formatUptime(seconds) {
    const d = Math.floor(seconds / (3600*24));
    const h = Math.floor(seconds % (3600*24) / 3600);
    const m = Math.floor(seconds % 3600 / 60);
    const s = Math.floor(seconds % 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m ${s}s`;
}

function showToast(title, message, isError = false) {
    const t = document.createElement('div');
    t.className = `toast ${isError ? 'error' : 'success'}`;
    const icon = isError 
        ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`
        : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
    t.innerHTML = `<div style="margin-top:2px">${icon}</div><div><div style="font-weight:600;margin-bottom:4px">${title}</div><div style="color:var(--text-muted);font-size:0.8rem">${message}</div></div>`;
    elToasts.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 4000);
}

// Navigation
elTabs.forEach(tab => {
    tab.addEventListener('click', () => {
        const target = tab.dataset.tab;
        elTabs.forEach(t => t.classList.remove('active'));
        elPanes.forEach(p => p.classList.remove('active'));
        
        tab.classList.add('active');
        document.getElementById(`tab-${target}`).classList.add('active');
        elPageTitle.innerText = tab.innerText.trim();
    });
});

// APIs
async function apiCall(method, path) {
    try {
        const res = await fetch(API_BASE + path, { method });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'API Error');
        return data;
    } catch (err) {
        showToast('Error', err.message, true);
        throw err;
    }
}

async function handleSetup(name) { isSetupMode.add(name); renderGrids(); try { const d = await apiCall('POST', `/admin/setup/${name}`); showToast('GUI Opened', d.message); } catch(e) { isSetupMode.delete(name); } }
async function handleResolve(name) { isSetupMode.add(name); renderGrids(); try { const d = await apiCall('POST', `/admin/resolve/${name}`); showToast('Captcha GUI', d.message); } catch(e) { isSetupMode.delete(name); } }
async function handleSaveClose(name) { try { const d = await apiCall('POST', `/admin/close-gui/${name}`); showToast('Success', d.message); isSetupMode.delete(name); fetchStatus(); } catch(e) {} }
async function handleDeleteSession(name) { if(!confirm(`Delete session for ${name}?`)) return; try { await apiCall('DELETE', `/admin/sessions/${name}`); showToast('Deleted', `Session removed.`); fetchStatus(); } catch(e) {} }

window.toggleProvider = async function(e, name) {
    e.preventDefault();
    const isChecked = e.target.checked;
    e.target.disabled = true;
    try {
        const res = await apiCall('POST', `/admin/provider/${name}/toggle`);
        showToast(res.enabled ? 'Enabled' : 'Disabled', `${name} is now ${res.enabled ? 'active' : 'inactive'}`);
        fetchStatus();
    } catch(err) {
        e.target.checked = !isChecked; // Revert visually
    } finally {
        e.target.disabled = false;
    }
}

function renderGrids() {
    if (!currentData || !currentData.providers) return;
    
    // OVERVIEW: Only Active Providers
    const activeProviders = currentData.providers.filter(p => p.enabled);
    activeProviders.sort((a,b) => a.name.localeCompare(b.name));
    
    elActiveCount.innerText = activeProviders.length;
    elOverviewGrid.innerHTML = activeProviders.map(p => {
        let statusClass = 'healthy';
        let statusText = 'Healthy';
        
        if (!p.session_valid) { statusClass = 'error'; statusText = 'Needs Login'; }
        else if (!p.healthy) { statusClass = 'error'; statusText = p.last_error === 'captcha_required' ? 'Captcha' : 'Error'; }

        const isSetup = isSetupMode.has(p.name);
        
        let errorUI = p.last_error ? `<div class="error-msg">${p.last_error}</div>` : '';
        
        let actionsUI = '';
        if (isSetup) {
            actionsUI = `<button class="btn-primary" onclick="handleSaveClose('${p.name}')">Close GUI & Save</button>`;
        } else {
            if (!p.session_valid || statusText === 'Needs Login') { actionsUI += `<button class="btn-primary" onclick="handleSetup('${p.name}')">Login (GUI)</button>`; }
            else if (statusText === 'Captcha') { actionsUI += `<button class="btn-warning" onclick="handleResolve('${p.name}')">Solve Captcha</button>`; }
            else { actionsUI += `<button onclick="handleSetup('${p.name}')">Re-Login</button>`; }
            if (p.session_valid) { actionsUI += `<button class="btn-danger" onclick="handleDeleteSession('${p.name}')" title="Delete Session"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path></svg></button>`; }
        }

        return `
            <div class="card">
                <div class="card-header"><div class="provider-name">${p.name}</div><div class="status-badge ${statusClass}">${statusText}</div></div>
                <div class="card-stats"><div>Models:</div><span>${p.models.length||'-'}</span><div>Active Req:</div><span>${p.active_requests}</span></div>
                ${errorUI}<div class="card-actions">${actionsUI}</div>
            </div>`;
    }).join('') || '<div style="color:var(--text-muted);grid-column:1/-1;">No active providers. Enable them in Settings.</div>';

    // SETTINGS: All Providers with Toggles
    const sortedAll = [...currentData.providers].sort((a,b) => {
        if(a.enabled && !b.enabled) return -1;
        if(!a.enabled && b.enabled) return 1;
        return a.name.localeCompare(b.name);
    });
    
    elSettingsGrid.innerHTML = sortedAll.map(p => `
        <div class="card ${!p.enabled ? 'disabled' : ''}">
            <div class="card-header" style="margin-bottom:0">
                <div class="provider-name">${p.name}</div>
                <label class="switch">
                    <input type="checkbox" onchange="toggleProvider(event, '${p.name}')" ${p.enabled ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            </div>
            <div style="font-size:0.8rem; color:var(--text-muted); margin-top:10px;">Models: ${p.models.join(', ')}</div>
        </div>
    `).join('');

    elUptime.innerText = formatUptime(currentData.uptime_seconds);
    elRequests.innerText = currentData.total_requests;
}

async function fetchStatus() {
    try {
        const res = await fetch(API_BASE + '/admin/status');
        if (res.ok) {
            currentData = await res.json();
            renderGrids();
        }
    } catch(e) {}
}

async function fetchModels() {
    try {
        const res = await fetch(API_BASE + '/v1/models');
        if (res.ok) {
            const data = await res.json();
            const models = data.data.map(m => m.id);
            chatModelSelect.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join('');
            chatModelSelect.value = 'auto'; // default
        }
    } catch(e) {}
}

// Polling
fetchStatus();
fetchModels();
setInterval(fetchStatus, 3000);

// --- Chat Playground Logic ---
function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role === 'user' ? 'user-msg' : 'system-msg'}`;
    const avatar = role === 'user' ? 'You' : 'AI';
    div.innerHTML = `
        <div class="msg-avatar">${avatar}</div>
        <div class="msg-bubble">${escapeHTML(content)}</div>
    `;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div; // Return so we can update it in streaming
}

function updateMessage(div, newContent) {
    const bubble = div.querySelector('.msg-bubble');
    bubble.innerHTML = escapeHTML(newContent);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function escapeHTML(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function sendChat() {
    if (isGenerating) return;
    const text = chatTextarea.value.trim();
    if (!text) return;
    
    chatTextarea.value = '';
    
    const sysPrompt = chatSystemPrompt.value.trim();
    const model = chatModelSelect.value;
    
    // Add to history
    if (chatHistory.length === 0 && sysPrompt) {
        chatHistory.push({role: "system", content: sysPrompt});
    }
    chatHistory.push({role: "user", content: text});
    
    appendMessage("user", text);
    const aiMessageDiv = appendMessage("assistant", "..."); // placeholder
    isGenerating = true;
    chatSendBtn.disabled = true;
    
    let completeResponse = "";

    try {
        const response = await fetch(API_BASE + '/v1/chat/completions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                model: model,
                messages: chatHistory,
                stream: true
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.error?.message || 'Server error');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, {stream: true});
            const lines = chunk.split('\n');
            
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6);
                    if (dataStr.trim() === '[DONE]') continue;
                    try {
                        const data = JSON.parse(dataStr);
                        const delta = data.choices[0].delta?.content || "";
                        completeResponse += delta;
                        updateMessage(aiMessageDiv, completeResponse);
                    } catch(e) {}
                }
            }
        }
        
        chatHistory.push({role: "assistant", content: completeResponse});
    } catch(e) {
        updateMessage(aiMessageDiv, `Error: ${e.message}`);
    } finally {
        isGenerating = false;
        chatSendBtn.disabled = false;
    }
}

chatSendBtn.addEventListener('click', sendChat);
chatTextarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
    }
});

chatClearBtn.addEventListener('click', () => {
    chatHistory = [];
    chatMessages.innerHTML = `
        <div class="message system-msg">
            <div class="msg-avatar">ProxiPT</div>
            <div class="msg-bubble">Conversation cleared. Started a new session.</div>
        </div>
    `;
});
