/**
 * BSB Live Dashboard — WebSocket Client
 * Handles: transcript, agent_change, call_summary, api events.
 */
(function () {
    "use strict";

    const WS_URL = `ws://${window.location.host}/dashboard-ws`;
    const transcriptContainer = document.getElementById("transcript-container");
    const apiContainer        = document.getElementById("api-container");
    const statusBadge         = document.getElementById("connection-status");
    const activeAgentBadge    = document.getElementById("active-agent");
    const apiCountBadge       = document.getElementById("api-count");

    const summaryFields = {
        name:     document.getElementById("summary-name"),
        phone:    document.getElementById("summary-phone"),
        company:  document.getElementById("summary-company"),
        location: document.getElementById("summary-location"),
        material: document.getElementById("summary-material"),
    };

    const API_PHASES = {
        "Buyer Details API":     { phase: "CALL START", icon: "🔍", color: "#818cf8" },
        "Get Price API":         { phase: "DURING CALL", icon: "💰", color: "#34d399" },
        "Save Lead API":         { phase: "CALL END",   icon: "💾", color: "#fb923c" },
        "WhatsApp Template API": { phase: "CALL END",   icon: "💬", color: "#22d3ee" },
    };

    const AGENT_LABELS = {
        supervisor:         "⚙ Supervisor",
        receptionist_agent: "📞 Receptionist",
        sales_agent:        "💰 Sales",
        logistics_agent:    "🚛 Logistics",
        nbfc_agent:         "🏦 Trade Finance",
    };

    let ws            = null;
    let reconnectTimer = null;
    let apiCallCount  = 0;

    // ── WebSocket ────────────────────────────────────────────────────────────
    function connect() {
        ws = new WebSocket(WS_URL);

        ws.onopen = () => {
            statusBadge.textContent = "● Live";
            statusBadge.classList.add("connected");
            if (reconnectTimer) { clearInterval(reconnectTimer); reconnectTimer = null; }
        };

        ws.onclose = () => {
            statusBadge.textContent = "○ Offline";
            statusBadge.classList.remove("connected");
            if (!reconnectTimer) reconnectTimer = setInterval(connect, 3000);
        };

        ws.onerror = () => ws.close();

        ws.onmessage = (event) => {
            try { handleMessage(JSON.parse(event.data)); }
            catch (e) { console.error("WS parse error:", e); }
        };
    }

    function handleMessage(data) {
        switch (data.type) {
            case "transcript":   addTranscript(data.speaker, data.text); break;
            case "agent_change": updateActiveAgent(data.agent_name);     break;
            case "call_summary": updateCallSummary(data);                break;
            case "api":          addApiCall(data);                       break;
        }
    }

    // ── Transcript ───────────────────────────────────────────────────────────
    function addTranscript(speaker, text) {
        const isUser = speaker === "user";
        const wrapper = document.createElement("div");
        wrapper.className = `message ${isUser ? "user" : "agent"}`;

        const ts = now();
        const label = document.createElement("span");
        label.className = "message-sender";
        label.textContent = isUser ? `👤 Customer · ${ts}` : `🤖 Mohini · ${ts}`;

        const content = document.createElement("span");
        content.textContent = text;

        wrapper.appendChild(label);
        wrapper.appendChild(content);
        transcriptContainer.appendChild(wrapper);
        scrollBottom(transcriptContainer);
    }

    // ── Active Agent ─────────────────────────────────────────────────────────
    function updateActiveAgent(agentName) {
        const label = AGENT_LABELS[agentName] || agentName;
        activeAgentBadge.textContent = label;

        const sep = document.createElement("div");
        sep.className = "agent-sep";
        sep.textContent = `── transferred to ${label} ──`;
        transcriptContainer.appendChild(sep);
        scrollBottom(transcriptContainer);
    }

    // ── Call Summary ─────────────────────────────────────────────────────────
    function updateCallSummary(data) {
        const map = { name: "name", phone: "phone", company: "company",
                      location: "location", material: "material" };
        Object.entries(map).forEach(([key, dataKey]) => {
            if (data[dataKey] && summaryFields[key]) {
                summaryFields[key].textContent = data[dataKey];
                summaryFields[key].classList.remove("empty");
            }
        });
    }

    // ── API Monitor ──────────────────────────────────────────────────────────
    function addApiCall(data) {
        apiCallCount++;
        if (apiCountBadge) apiCountBadge.textContent = apiCallCount;

        const endpoint = data.endpoint || "API Call";
        const cfg      = API_PHASES[endpoint] || { phase: "CALL", icon: "🔗", color: "#94a3b8" };
        const isOk     = data.status >= 200 && data.status < 300;
        const ts       = now();
        const cardId   = `api-card-${apiCallCount}`;

        const card = document.createElement("div");
        card.className = "api-call";
        card.id = cardId;

        // ── Header row ────────────────────────────────────────────────────────
        const header = document.createElement("div");
        header.className = "api-header";
        header.innerHTML = `
            <span class="api-name">
                <span class="api-icon">${cfg.icon}</span>
                <span style="color:${cfg.color}">${endpoint}</span>
            </span>
            <span class="api-right">
                <span class="phase-badge" style="background:${cfg.color}22;color:${cfg.color};border-color:${cfg.color}55">${cfg.phase}</span>
                <span class="status-pill ${isOk ? "ok" : "err"}">${isOk ? "✓" : "✗"} ${data.status}</span>
            </span>
        `;

        // ── Timestamp ─────────────────────────────────────────────────────────
        const tsEl = document.createElement("div");
        tsEl.className = "api-ts";
        tsEl.textContent = `${ts}  ·  #${apiCallCount}`;

        // ── Request section ───────────────────────────────────────────────────
        const details = data.details || {};
        const reqData  = details.request  || null;
        const respData = details.response || null;

        card.appendChild(header);
        card.appendChild(tsEl);

        if (reqData) {
            card.appendChild(makeSection("REQUEST", reqData, "#60a5fa"));
        }
        if (respData) {
            card.appendChild(makeSection("RESPONSE", respData, isOk ? "#34d399" : "#f87171"));
        }
        // Fallback: if neither key exists just dump details raw
        if (!reqData && !respData && details && Object.keys(details).length) {
            card.appendChild(makeSection("DETAILS", details, "#94a3b8"));
        }

        apiContainer.prepend(card);   // newest on top
    }

    function makeSection(title, obj, color) {
        const wrap = document.createElement("div");
        wrap.className = "api-section";

        const titleEl = document.createElement("div");
        titleEl.className = "api-section-title";
        titleEl.style.color = color;
        titleEl.textContent = title;

        const body = document.createElement("div");
        body.className = "api-section-body";
        body.innerHTML = renderObj(obj);

        wrap.appendChild(titleEl);
        wrap.appendChild(body);
        return wrap;
    }

    function renderObj(obj, indent = 0) {
        if (obj === null || obj === undefined) return `<span class="val-null">null</span>`;
        if (typeof obj === "boolean")          return `<span class="val-bool">${obj}</span>`;
        if (typeof obj === "number")           return `<span class="val-num">${obj}</span>`;
        if (typeof obj === "string")           return `<span class="val-str">"${escHtml(obj)}"</span>`;
        if (Array.isArray(obj)) {
            if (obj.length === 0) return `<span class="val-null">[]</span>`;
            const items = obj.map(item =>
                `<div class="arr-item">${renderObj(item, indent + 1)}</div>`
            ).join("");
            return `<div class="arr-wrap">${items}</div>`;
        }
        if (typeof obj === "object") {
            const rows = Object.entries(obj)
                .filter(([, v]) => v !== null && v !== undefined && v !== "")
                .map(([k, v]) => {
                    const isComplex = typeof v === "object" && v !== null;
                    return `<div class="kv-row">
                        <span class="kv-key">${escHtml(k)}</span>
                        <span class="kv-sep">:</span>
                        ${isComplex
                            ? `<div class="kv-nested">${renderObj(v, indent + 1)}</div>`
                            : `<span class="kv-val">${renderObj(v, indent + 1)}</span>`
                        }
                    </div>`;
                }).join("");
            return rows || `<span class="val-null">{}</span>`;
        }
        return escHtml(String(obj));
    }

    function escHtml(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    // ── Utils ────────────────────────────────────────────────────────────────
    function now() {
        return new Date().toLocaleTimeString("en-IN", {
            hour: "2-digit", minute: "2-digit", second: "2-digit"
        });
    }
    function scrollBottom(el) { el.scrollTop = el.scrollHeight; }

    connect();
})();
