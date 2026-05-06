document.addEventListener('DOMContentLoaded', () => {
    // Tab Navigation
    const tabs = document.querySelectorAll('.nav-links li');
    const contents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            contents.forEach(c => c.classList.remove('active'));
            
            tab.classList.add('active');
            document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');

            if (tab.dataset.tab === 'history') {
                fetchHistory();
            } else if (tab.dataset.tab === 'metrics') {
                fetchIntents();
            }
        });
    });

    // Filter event listeners
    const filterPhone = document.getElementById('filter-phone');
    const filterAgent = document.getElementById('filter-agent');

    if (filterPhone) filterPhone.addEventListener('input', debounce(() => fetchHistory(), 500));
    if (filterAgent) filterAgent.addEventListener('change', () => fetchHistory());

    // Modal logic
    const modal = document.getElementById('transcript-modal');
    const closeBtn = document.querySelector('.close-modal');

    closeBtn.onclick = () => modal.classList.remove('show');
    window.onclick = (e) => {
        if (e.target === modal) modal.classList.remove('show');
    };

    // WebSocket Connection
    let ws;
    let wsReconnectTimeout;

    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Note: assumes dashboard is hosted on the same server
        const wsUrl = `${protocol}//${window.location.host}/dashboard-ws`;
        
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            document.getElementById('ws-status').innerText = 'System Online';
            document.querySelector('.status-indicator').classList.remove('offline');
            console.log('Dashboard WS Connected');
        };

        ws.onclose = () => {
            document.getElementById('ws-status').innerText = 'System Offline - Reconnecting...';
            document.querySelector('.status-indicator').classList.add('offline');
            clearTimeout(wsReconnectTimeout);
            wsReconnectTimeout = setTimeout(connectWebSocket, 3000);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleWsMessage(data);
            } catch (e) {
                console.error("Error parsing WS message", e);
            }
        };
    }

    connectWebSocket();

    // Initial Data Fetch
    fetchStats();
});

// --- UI Handlers ---

const ui = {
    transcript: document.getElementById('transcript'),
    typing: document.getElementById('typing-indicator'),
    agentBadge: document.getElementById('current-agent'),
    stats: {
        active: document.getElementById('stat-active'),
        completed: document.getElementById('stat-completed'),
        latency: document.getElementById('stat-latency')
    },
    latencyBars: {
        stt: { val: document.getElementById('val-stt'), bar: document.getElementById('bar-stt') },
        llm: { val: document.getElementById('val-llm'), bar: document.getElementById('bar-llm') },
        tts: { val: document.getElementById('val-tts'), bar: document.getElementById('bar-tts') },
        total: document.getElementById('val-total')
    }
};

let activeCallId = null;

function handleWsMessage(data) {
    if (data.type === 'call_state') {
        if (data.state === 'active') {
            if (activeCallId !== data.call_id) {
                // New call started, clear old transcript
                ui.transcript.innerHTML = '';
                activeCallId = data.call_id;
                ui.typing.classList.add('hidden');
            }
            fetchStats(); // Update active count
        } else if (data.state === 'completed') {
            activeCallId = null;
            ui.typing.classList.add('hidden');
            fetchStats(); // Update completed count
            
            // Add system message
            const div = document.createElement('div');
            div.className = 'empty-state';
            div.style.marginTop = '20px';
            div.innerText = 'Call Ended. Waiting for next call...';
            ui.transcript.appendChild(div);
            scrollToBottom(ui.transcript);
        }
    } 
    else if (data.type === 'transcript') {
        // Remove empty state if present
        const empty = ui.transcript.querySelector('.empty-state');
        if (empty) empty.remove();

        const div = document.createElement('div');
        div.className = `msg msg-${data.speaker}`;
        
        const now = new Date();
        const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        
        div.innerHTML = `${data.text} <span class="msg-time">${timeStr}</span>`;
        ui.transcript.appendChild(div);
        
        scrollToBottom(ui.transcript);

        if (data.speaker === 'user') {
            ui.typing.classList.remove('hidden');
        } else {
            ui.typing.classList.add('hidden');
        }
    }
    else if (data.type === 'metrics') {
        updateLatencyBars(data.stt_ms, data.llm_ms, data.tts_ms, data.total_ms);
    }
    else if (data.type === 'agent_change') {
        ui.agentBadge.innerText = data.agent_name.charAt(0).toUpperCase() + data.agent_name.slice(1);
    }
}

function updateLatencyBars(stt, llm, tts, total) {
    // Max scale 5000ms (5s) for visual proportion
    const scale = 5000;
    
    const setBar = (element, ms) => {
        element.val.innerText = `${Math.round(ms)}ms`;
        let pct = (ms / scale) * 100;
        if (pct > 100) pct = 100;
        element.bar.style.width = `${pct}%`;
    };

    setBar(ui.latencyBars.stt, stt);
    setBar(ui.latencyBars.llm, llm);
    setBar(ui.latencyBars.tts, tts);
    
    ui.latencyBars.total.innerText = `${(total / 1000).toFixed(2)}s`;
    ui.stats.latency.innerText = `${Math.round(total)} ms`;
}

function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
}

// --- REST API Fetchers ---

async function fetchStats() {
    try {
        const res = await fetch('/api/dashboard/stats');
        if (!res.ok) return;
        const data = await res.json();
        
        ui.stats.active.innerText = data.active_calls;
        ui.stats.completed.innerText = data.completed_calls;
    } catch (e) {
        console.error("Failed to fetch stats", e);
    }
}

async function fetchHistory() {
    const tbody = document.getElementById('history-table-body');
    tbody.innerHTML = '<tr><td colspan="6" class="text-center">Loading history...</td></tr>';
    
    const phone = document.getElementById('filter-phone')?.value || '';
    const agent = document.getElementById('filter-agent')?.value || 'all';
    
    try {
        let url = `/api/dashboard/calls?phone=${encodeURIComponent(phone)}&agent=${encodeURIComponent(agent)}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error("Failed to fetch");
        const calls = await res.json();
        
        window.lastFetchedCalls = calls; // Store for CSV export
        
        if (calls.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center">No calls found in database.</td></tr>';
            return;
        }

        tbody.innerHTML = '';
        calls.forEach(call => {
            const tr = document.createElement('tr');
            
            const time = new Date(call.created_at).toLocaleString();
            const badgeClass = call.stage === 'completed' ? 'completed' : 'active';
            
            // Format duration
            const mins = Math.floor(call.duration / 60);
            const secs = call.duration % 60;
            const durStr = `${mins}:${secs.toString().padStart(2, '0')}`;

            tr.innerHTML = `
                <td>${time}</td>
                <td>${call.phone_number || 'Unknown'}</td>
                <td>${call.agent_used}</td>
                <td>${durStr}</td>
                <td><span class="badge ${badgeClass}">${call.stage}</span></td>
                <td>
                    <button class="action-btn" onclick="viewTranscript('${call.call_id}')">
                        <i class="fa-solid fa-eye"></i> View
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center">Error loading history.</td></tr>';
        console.error(e);
    }
}

async function viewTranscript(callId) {
    const modal = document.getElementById('transcript-modal');
    const body = document.getElementById('modal-transcript-body');
    
    body.innerHTML = '<div class="empty-state">Loading transcript...</div>';
    modal.classList.add('show');
    
    try {
        const res = await fetch(`/api/dashboard/calls/${callId}/transcript`);
        if (!res.ok) throw new Error("Failed to fetch transcript");
        const data = await res.json();
        
        if (data.history.length === 0) {
            body.innerHTML = '<div class="empty-state">No transcript data available.</div>';
            return;
        }
        
        body.innerHTML = '';
        data.history.forEach(msg => {
            // Support LangGraph string tuple format ["user", "hello"] or dict
            let type = msg.type || msg[0] || 'human';
            let content = msg.content || msg[1] || '';
            
            const isUser = type === 'human' || type === 'user';
            
            const div = document.createElement('div');
            div.className = `msg ${isUser ? 'msg-user' : 'msg-agent'}`;
            div.innerText = content;
            body.appendChild(div);
        });
        
    } catch (e) {
        body.innerHTML = '<div class="empty-state">Error loading transcript.</div>';
        console.error(e);
    }
}

// --- Analytics & Export ---

let intentChart = null;

async function fetchIntents() {
    try {
        const res = await fetch('/api/dashboard/intents');
        if (!res.ok) return;
        const data = await res.json();
        renderChart(data);
    } catch (e) {
        console.error("Failed to fetch intents", e);
    }
}

function renderChart(data) {
    const ctx = document.getElementById('intentChart').getContext('2d');
    
    const labels = Object.keys(data).map(k => k.replace('_agent', '').toUpperCase());
    const values = Object.values(data);
    
    if (intentChart) intentChart.destroy();
    
    intentChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: [
                    '#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'
                ],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#94a3b8', padding: 20 }
                }
            }
        }
    });
}

function exportCallsToCSV() {
    const calls = window.lastFetchedCalls || [];
    if (calls.length === 0) return alert("No data to export");
    
    const headers = ["Time", "Phone", "Agent", "Duration(s)", "Stage", "Customer Info"];
    const rows = calls.map(c => [
        new Date(c.created_at).toLocaleString(),
        c.phone_number,
        c.agent_used,
        c.duration,
        c.stage,
        JSON.stringify(c.customer_info).replace(/"/g, '""')
    ]);
    
    let csvContent = "data:text/csv;charset=utf-8," 
        + headers.join(",") + "\n"
        + rows.map(r => r.map(cell => `"${cell}"`).join(",")).join("\n");
        
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `bsb_calls_export_${new Date().toISOString().slice(0,10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function debounce(func, wait) {
    let timeout;
    return function() {
        const context = this, args = arguments;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}

