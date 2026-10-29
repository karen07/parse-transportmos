/* Local schematic graph visualization. Animation does not imply a progress percentage. */
const bootUI = {
    overlay: document.getElementById('boot-overlay'),
    canvas: document.getElementById('boot-network'),
    stage: document.getElementById('boot-stage'),
    percent: document.getElementById('boot-percent'),
    meter: document.getElementById('boot-meter'),
    fill: document.getElementById('boot-meter-fill'),
    details: document.getElementById('boot-details'),
    error: document.getElementById('boot-error'),
    errorMessage: document.getElementById('boot-error-message'),
};
const bootReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let bootStopAnimation = () => {};

(() => {
    'use strict';
    const canvas = bootUI.canvas;
    const ctx = canvas.getContext('2d');
    const spokes = 10;
    const radii = [0.26, 0.53, 0.81];
    const nodes = [{ radius: 0, angle: 0, ring: -1, spoke: -1 }];
    const edges = [];
    const nodeId = (ring, spoke) => 1 + ring * spokes + spoke;
    for (let ring = 0; ring < radii.length; ring++) {
        for (let spoke = 0; spoke < spokes; spoke++) {
            nodes.push({
                radius: radii[ring],
                angle: -Math.PI / 2 + (spoke * Math.PI * 2) / spokes,
                ring,
                spoke,
            });
        }
    }
    const addEdge = (a, b, weight, ring = false) => edges.push({ a, b, weight, ring });
    for (let spoke = 0; spoke < spokes; spoke++) {
        addEdge(0, nodeId(0, spoke), 1.8);
        for (let ring = 0; ring < radii.length; ring++) {
            if (ring < radii.length - 1)
                addEdge(nodeId(ring, spoke), nodeId(ring + 1, spoke), 2.25 + ring * 0.4);
            addEdge(
                nodeId(ring, spoke),
                nodeId(ring, (spoke + 1) % spokes),
                1.18 + ring * 1.0,
                true,
            );
        }
    }
    let source = nodeId(1, 7);
    let distances = [];
    function shortestPaths() {
        distances = Array(nodes.length).fill(Infinity);
        const visited = new Set();
        distances[source] = 0;
        for (let i = 0; i < nodes.length; i++) {
            let best = -1;
            for (let n = 0; n < nodes.length; n++) {
                if (!visited.has(n) && (best === -1 || distances[n] < distances[best])) best = n;
            }
            if (best === -1 || !isFinite(distances[best])) break;
            visited.add(best);
            for (const edge of edges) {
                const other = edge.a === best ? edge.b : edge.b === best ? edge.a : -1;
                if (other >= 0 && distances[other] > distances[best] + edge.weight) {
                    distances[other] = distances[best] + edge.weight;
                }
            }
        }
    }
    shortestPaths();
    let w = 0,
        h = 0,
        dpr = 1,
        scale = 0,
        cx = 0,
        cy = 0;
    let start = performance.now();
    let animationFrame = null;
    let stopped = false;
    let lastDraw = 0;
    const duration = 8700;
    // Wave reaches only part of the network, then fades and starts again.
    // This is decorative animation, not a measure of actual loading.
    const farthestDistance = () => Math.max(...distances.filter(Number.isFinite));
    const waveLimit = () => farthestDistance() * 0.62;
    const smoothstep = (a, b, x) => {
        const t = clamp((x - a) / (b - a), 0, 1);
        return t * t * (3 - 2 * t);
    };
    const clamp = (value, a, b) => Math.max(a, Math.min(b, value));
    function resize() {
        const rect = canvas.getBoundingClientRect();
        w = rect.width;
        h = rect.height;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        cx = w / 2;
        cy = h / 2;
        scale = Math.min(h * 0.525, w * 0.395);
    }
    window.addEventListener('resize', resize, { passive: true });
    resize();
    function coord(n) {
        if (n.radius === 0) return { x: cx, y: cy };
        return {
            x: cx + Math.cos(n.angle) * n.radius * scale,
            y: cy + Math.sin(n.angle) * n.radius * scale,
        };
    }
    function edgePath(e, from, to) {
        ctx.beginPath();
        if (e.ring) {
            const a = nodes[e.a],
                b = nodes[e.b];
            const radius = a.radius * scale;
            const first = a.angle;
            const delta = (Math.PI * 2) / spokes;
            ctx.arc(cx, cy, radius, first + from * delta, first + to * delta);
        } else {
            const a = coord(nodes[e.a]),
                b = coord(nodes[e.b]);
            ctx.moveTo(a.x + (b.x - a.x) * from, a.y + (b.y - a.y) * from);
            ctx.lineTo(a.x + (b.x - a.x) * to, a.y + (b.y - a.y) * to);
        }
    }
    function strokeEdge(edge, from, to, color, width) {
        if (to <= from) return;
        edgePath(edge, from, to);
        ctx.lineWidth = width;
        ctx.strokeStyle = color;
        ctx.stroke();
    }
    function drawFrame(now) {
        if (stopped || bootUI.overlay.hidden) return;
        if (!bootReducedMotion && now - lastDraw < 66) {
            animationFrame = requestAnimationFrame(drawFrame);
            return;
        }
        lastDraw = now;
        const time = bootReducedMotion ? start + 4600 : now;
        const elapsed = (((time - start) % duration) + duration) % duration;
        const cycle = elapsed / duration;
        const fraction = smoothstep(0.02, 0.85, cycle);
        const distance = waveLimit() * fraction;
        const trail = waveLimit() * 0.34;
        const opacity = 1 - smoothstep(0.86, 0.99, cycle);
        ctx.clearRect(0, 0, w, h);
        // The only background decoration is a faint baseline through the schematic.
        ctx.beginPath();
        ctx.moveTo(cx - scale, cy);
        ctx.lineTo(cx + scale, cy);
        ctx.strokeStyle = '#151e31';
        ctx.lineWidth = 1;
        ctx.stroke();
        for (const edge of edges) strokeEdge(edge, 0, 1, '#29334b', 1.7);
        ctx.lineCap = 'round';
        ctx.globalAlpha = opacity;
        // A moving band illuminates edges, then they become dark again.
        // At no time does the diagram imply that 100% of the map was processed.
        for (const edge of edges) {
            for (const [arrival, reverse] of [
                [distances[edge.a], false],
                [distances[edge.b], true],
            ]) {
                const from = clamp((distance - trail - arrival) / edge.weight, 0, 1);
                const to = clamp((distance - arrival) / edge.weight, 0, 1);
                if (reverse) {
                    strokeEdge(edge, 1 - to, 1 - from, '#bd8290', 2.4);
                } else {
                    strokeEdge(edge, from, to, '#bd8290', 2.4);
                }
                const brightFrom = clamp((distance - 0.75 - arrival) / edge.weight, 0, 1);
                if (reverse) {
                    strokeEdge(edge, 1 - to, 1 - brightFrom, '#ffe5c9', 3.3);
                } else {
                    strokeEdge(edge, brightFrom, to, '#ffe5c9', 3.3);
                }
            }
        }
        ctx.globalAlpha = 1;
        for (let i = 0; i < nodes.length; i++) {
            const p = coord(nodes[i]);
            const isActive = distances[i] <= distance && distances[i] >= distance - trail;
            const isSource = i === source;
            const size = isSource ? 6.2 : isActive ? 3.5 : 2.6;
            ctx.beginPath();
            ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
            ctx.fillStyle = isSource
                ? '#fff2e4'
                : isActive && opacity > 0.2
                  ? '#f4b99b'
                  : '#596680';
            ctx.fill();
            if (isSource) {
                ctx.strokeStyle = '#e9a38e';
                ctx.lineWidth = 2;
                ctx.beginPath();
                const pulse = (elapsed % 1700) / 1700;
                ctx.arc(p.x, p.y, 11 + pulse * 15, 0, 2 * Math.PI);
                ctx.globalAlpha = 0.45 * (1 - pulse);
                ctx.stroke();
                ctx.globalAlpha = 1;
            }
        }
        if (!bootReducedMotion) animationFrame = requestAnimationFrame(drawFrame);
    }
    bootStopAnimation = () => {
        stopped = true;
        if (animationFrame !== null) cancelAnimationFrame(animationFrame);
    };
    animationFrame = requestAnimationFrame(drawFrame);
})();

function bootPhase(stage, detail) {
    bootUI.stage.textContent = stage;
    // Non-download phases have no measurable percentage. Do not fake progress.
    bootUI.meter.classList.add('is-indeterminate');
    bootUI.meter.removeAttribute('aria-valuenow');
    bootUI.percent.hidden = true;
    bootUI.percent.textContent = '';
    if (detail) bootUI.details.textContent = detail;
}
function bootProgress(received, total) {
    if (bootUI.stage.textContent !== 'FETCHING DATA') return;
    if (total > 0) {
        const percent = Math.max(0, Math.min(100, Math.floor((received * 100) / total)));
        bootUI.meter.classList.remove('is-indeterminate');
        bootUI.fill.style.width = percent + '%';
        bootUI.meter.setAttribute('aria-valuenow', String(percent));
        bootUI.percent.hidden = false;
        bootUI.percent.textContent = percent + '%';
        bootUI.details.textContent =
            (received / 1048576).toFixed(1) +
            ' / ' +
            (total / 1048576).toFixed(1) +
            ' MiB downloaded';
    } else {
        bootUI.meter.classList.add('is-indeterminate');
        bootUI.meter.removeAttribute('aria-valuenow');
        bootUI.percent.hidden = true;
        bootUI.percent.textContent = '';
        bootUI.details.textContent = (received / 1048576).toFixed(1) + ' MiB downloaded';
    }
}
function bootComplete() {
    bootUI.overlay.hidden = true;
    bootStopAnimation();
}
function bootFailed(error) {
    // Keep the scene animated so a failed request cannot freeze the page.
    bootUI.overlay.hidden = false;
    bootUI.stage.textContent = 'BOOT FAILED';
    bootUI.percent.hidden = true;
    bootUI.error.hidden = false;
    bootUI.errorMessage.textContent = String((error && error.message) || error);
}
document.getElementById('boot-retry').addEventListener('click', () => location.reload());
// Transport data decoding alone does not mean the visual base map is ready.
// Wait for *visible* vector features rather than an idle event: a single failed
// tile, a style transition, or a missed sourcedata callback can prevent idle
// indefinitely, even when the actual map has been painted. A bounded fallback
// accepts a visible (possibly partially loaded) base map without hiding the
// overlay on a still-empty WebGL canvas.
function waitForBaseMapReady(layer, timeoutMs = 30000) {
    const gl = layer.getMaplibreMap();
    if (!gl) return Promise.reject(new Error('MapLibre map was not initialized'));
    return new Promise((resolve, reject) => {
        let finished = false;
        let firstVisibleAt = null;
        let lastCheckAt = 0;
        let lastError = '';
        const timeout = setTimeout(() => {
            const detail = lastError ? ' Last map error: ' + lastError : '';
            fail(
                new Error(
                    'Map tiles were not visible after ' +
                        timeoutMs / 1000 +
                        's.' +
                        detail +
                        ' Check the tile server, network and WebGL support.',
                ),
            );
        }, timeoutMs);
        // Poll as well as listening to render: MapLibre can stop emitting idle
        // when one tile fails or the style keeps repainting.
        const poll = setInterval(check, 200);
        function cleanup() {
            clearTimeout(timeout);
            clearInterval(poll);
            gl.off('load', check);
            gl.off('render', check);
            gl.off('idle', check);
            gl.off('error', onError);
        }
        function fail(error) {
            if (finished) return;
            finished = true;
            cleanup();
            reject(error);
        }
        function onError(event) {
            lastError = String(event.error?.message || event.error || 'unknown error');
            console.warn('[map] BASE MAP RESOURCE ERROR:', lastError);
        }
        function check() {
            if (finished) return;
            const now = Date.now();
            if (now - lastCheckAt < 180) return; // queryRenderedFeatures is costly.
            lastCheckAt = now;
            const canvas = gl.getCanvas();
            if (!canvas || !canvas.width || !canvas.height) return;
            let features;
            try {
                // Unlike 'load', 'idle' and areTilesLoaded(), this proves that
                // a real vector layer has made it into a rendered frame.
                features = gl.queryRenderedFeatures();
            } catch (_) {
                return;
            } // Style is not ready for querying yet.
            if (!features || features.length === 0) {
                firstVisibleAt = null;
                return;
            }
            if (firstVisibleAt === null) firstVisibleAt = now;
            const tilesLoaded = typeof gl.areTilesLoaded === 'function' && gl.areTilesLoaded();
            const visibleFor = now - firstVisibleAt;
            // Prefer fully loaded tiles, but don't block forever on an idle
            // event or an unrelated tile that never finishes. Allow the map
            // some time to fill the viewport before accepting partial tiles.
            if (!(tilesLoaded && visibleFor >= 400) && visibleFor < 2500) return;
            finished = true;
            cleanup();
            console.info(
                '[map] BASE MAP PAINTED',
                tilesLoaded ? 'tiles loaded' : 'visible tiles; other tiles pending',
            );
            // Let the compositing frame hit the screen before removing overlay.
            let settled = false;
            const done = () => {
                if (settled) return;
                settled = true;
                clearTimeout(frameFallback);
                resolve();
            };
            // A hidden Safari tab may suspend requestAnimationFrame entirely.
            const frameFallback = setTimeout(done, 500);
            requestAnimationFrame(() => requestAnimationFrame(done));
        }
        gl.on('load', check);
        gl.on('render', check);
        gl.on('idle', check);
        gl.on('error', onError);
        check(); // Cached map may be rendered already.
    });
}
function loadTransportData() {
    bootPhase('FETCHING DATA', 'Starting download...');
    return new Promise((resolve, reject) => {
        const worker = new Worker(new URL(transportConfig.data_worker_file, document.baseURI));
        worker.onmessage = (event) => {
            const message = event.data;
            if (message.type === 'progress') bootProgress(message.received, message.total);
            else if (message.type === 'phase') bootPhase(message.stage, message.detail);
            else if (message.type === 'ready') {
                console.info('[map] WORKER decode', message.decodeMs, 'ms');
                worker.terminate();
                resolve(message.data);
            } else if (message.type === 'error') {
                worker.terminate();
                reject(new Error(message.message));
            }
        };
        worker.onerror = (event) => {
            worker.terminate();
            reject(new Error(event.message || 'Data Worker could not start'));
        };
        worker.postMessage({ url: new URL(transportConfig.data_file, document.baseURI).href });
    });
}
