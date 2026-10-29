/* Static OSM SVG is already visible when this deferred script executes. */
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
const bootMainScript = document.currentScript.dataset.mapScript;
let bootStopAnimation = () => {};

(() => {
    'use strict';
    const canvas = bootUI.canvas;
    const svgPath = document.getElementById('boot-road-path');
    const geometry = svgPath.getAttribute('d').trim();
    // The separate Canvas is decorative only. The SVG paints without JS.
    if (!geometry || bootReducedMotion) return;
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx || typeof Path2D !== 'function') return;
    const road = new Path2D(geometry);
    let width = 1,
        height = 1,
        dpr = 1;
    let mask = null,
        front = null,
        raf = null,
        lastFrame = 0;
    let paused = false;
    const start = performance.now();
    const duration = 6800;

    function surface() {
        const offscreen = document.createElement('canvas');
        offscreen.width = canvas.width;
        offscreen.height = canvas.height;
        return offscreen;
    }

    function resize() {
        const bounds = canvas.getBoundingClientRect();
        width = Math.max(1, bounds.width);
        height = Math.max(1, bounds.height);
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        mask = surface();
        front = surface();
        const scale = Math.min(width / 800, height / 400);
        const x = (width - scale * 800) / 2;
        const y = (height - scale * 400) / 2;
        const painter = mask.getContext('2d');
        painter.setTransform(dpr * scale, 0, 0, dpr * scale, dpr * x, dpr * y);
        painter.lineWidth = 1.55;
        painter.lineCap = 'round';
        painter.lineJoin = 'round';
        painter.strokeStyle = '#fff';
        painter.stroke(road);
    }

    function frame(now) {
        raf = null;
        if (paused || bootUI.overlay.hidden || document.hidden) return;
        raf = requestAnimationFrame(frame);
        if (now - lastFrame < 50) return; // At most 20 FPS.
        lastFrame = now;
        ctx.clearRect(0, 0, width, height);
        const phase = ((now - start) % duration) / duration;
        const radius = 4 + phase * Math.hypot(width, height) * 0.43;
        const fade = Math.max(0, Math.min(1, phase * 9, (1 - phase) * 7));
        if (!fade) return;
        const f = front.getContext('2d');
        f.setTransform(dpr, 0, 0, dpr, 0, 0);
        f.clearRect(0, 0, width, height);
        f.drawImage(mask, 0, 0, width, height);
        f.globalCompositeOperation = 'source-in';
        const gradient = f.createRadialGradient(
            width / 2,
            height / 2,
            0,
            width / 2,
            height / 2,
            radius,
        );
        gradient.addColorStop(0, 'rgba(237,139,125,.2)');
        gradient.addColorStop(0.48, 'rgba(237,139,125,.06)');
        gradient.addColorStop(0.73, 'rgba(239,150,128,.28)');
        gradient.addColorStop(0.91, 'rgba(253,180,143,.96)');
        gradient.addColorStop(1, 'rgba(253,180,143,0)');
        f.fillStyle = gradient;
        f.fillRect(0, 0, width, height);
        f.globalCompositeOperation = 'source-over';
        ctx.globalAlpha = fade;
        ctx.drawImage(front, 0, 0, width, height);
        ctx.globalAlpha = 1;
    }

    resize();
    raf = requestAnimationFrame(frame);
    window.addEventListener('resize', resize, { passive: true });
    function onVisibility() {
        if (!document.hidden && !paused && raf === null) {
            raf = requestAnimationFrame(frame);
        }
    }
    document.addEventListener('visibilitychange', onVisibility);
    bootStopAnimation = () => {
        paused = true;
        if (raf !== null) cancelAnimationFrame(raf);
        window.removeEventListener('resize', resize);
        document.removeEventListener('visibilitychange', onVisibility);
    };
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

// Begin the expensive application only after the initial loader has painted.
// A timer prevents a permanently backgrounded tab from being stuck forever.
(() => {
    let started = false;
    function startMap() {
        if (started) return;
        started = true;
        const script = document.createElement('script');
        script.src = bootMainScript;
        script.onerror = () => bootFailed(new Error('Could not load the map script'));
        document.body.appendChild(script);
    }
    requestAnimationFrame(() => requestAnimationFrame(startMap));
    setTimeout(startMap, 300);
})();
