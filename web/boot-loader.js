/* OSM major roads illustration. Animation is not a loading percentage. */
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
    // Real OSM road geometry, exported once by major_roads.py.
    // Animation is decorative, never a proxy for actual loading progress.
    const canvas = bootUI.canvas;
    const ctx = canvas.getContext('2d');
    const reducedMotion = bootReducedMotion;
    let w = 0;
    let h = 0;
    let dpr = 1;
    let network = null;
    let background = null;
    let roadMask = null;
    let highlight = null;
    let raf = null;
    let stopped = false;
    let lastDraw = 0;
    let waveStart = 0;
    // The road map is uniform regardless of OSM highway classification.
    const roadWidth = 1.55;
    const roadColor = '#39465f';
    const waveDuration = 6800;

    function makeCanvas() {
        const surface = document.createElement('canvas');
        surface.width = canvas.width;
        surface.height = canvas.height;
        return surface;
    }

    function updateSize() {
        const bounds = canvas.getBoundingClientRect();
        w = Math.max(1, bounds.width);
        h = Math.max(1, bounds.height);
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        if (network) prepare();
    }

    function prepare() {
        if (!network || !w || !h) return;
        const [mx, my] = network.extent;
        const scale = Math.min((w - 28) / (2 * mx), (h - 24) / (2 * my));
        roadMask = makeCanvas();
        highlight = makeCanvas();
        background = makeCanvas();
        const mask = roadMask.getContext('2d');
        mask.setTransform(dpr, 0, 0, dpr, 0, 0);
        mask.lineWidth = roadWidth;
        mask.lineCap = 'round';
        mask.lineJoin = 'round';
        mask.strokeStyle = '#fff';

        // Draw every way into one opaque mask. Where two carriageways or OSM
        // fragments overlap, they cannot become brighter or thicker through
        // alpha blending. Every named ring uses exactly the same stroke.
        for (const [, coords] of network.paths) {
            if (!Array.isArray(coords) || coords.length < 4) continue;
            mask.beginPath();
            mask.moveTo(w / 2 + coords[0] * scale, h / 2 + coords[1] * scale);
            for (let i = 2; i < coords.length; i += 2) {
                mask.lineTo(w / 2 + coords[i] * scale, h / 2 + coords[i + 1] * scale);
            }
            mask.stroke();
        }
        mask.globalCompositeOperation = 'source-in';
        mask.fillStyle = roadColor;
        mask.fillRect(0, 0, w, h);
        mask.globalCompositeOperation = 'source-over';

        const back = background.getContext('2d');
        back.setTransform(dpr, 0, 0, dpr, 0, 0);
        back.fillStyle = '#0b1020';
        back.fillRect(0, 0, w, h);
        back.drawImage(roadMask, 0, 0, w, h);
        waveStart = performance.now();
    }

    function drawWave(now) {
        // Animate a radial front from geographic central Moscow, not a random
        // road fragment. The cycle is decorative and never reaches every road.
        const phase = ((now - waveStart) % waveDuration) / waveDuration;
        const radius = 4 + phase * Math.hypot(w, h) * 0.43;
        const fade = Math.min(1, phase * 9, (1 - phase) * 7);
        if (radius < 2 || fade <= 0) return;

        const front = highlight.getContext('2d');
        front.setTransform(dpr, 0, 0, dpr, 0, 0);
        front.clearRect(0, 0, w, h);
        front.drawImage(roadMask, 0, 0, w, h);
        front.globalCompositeOperation = 'source-in';
        const gradient = front.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, radius);
        gradient.addColorStop(0, 'rgba(237, 139, 125, 0.2)');
        gradient.addColorStop(0.48, 'rgba(237, 139, 125, 0.06)');
        gradient.addColorStop(0.73, 'rgba(239, 150, 128, 0.28)');
        gradient.addColorStop(0.91, 'rgba(253, 180, 143, 0.96)');
        gradient.addColorStop(1, 'rgba(253, 180, 143, 0)');
        front.fillStyle = gradient;
        front.fillRect(0, 0, w, h);
        front.globalCompositeOperation = 'source-over';

        ctx.globalAlpha = fade;
        ctx.drawImage(highlight, 0, 0, w, h);
        ctx.globalAlpha = 1;
    }

    function animate(now) {
        if (stopped || bootUI.overlay.hidden) return;
        if (!reducedMotion && now - lastDraw < 50) {
            raf = requestAnimationFrame(animate);
            return;
        }
        lastDraw = now;
        ctx.clearRect(0, 0, w, h);
        if (background) ctx.drawImage(background, 0, 0, w, h);
        if (roadMask && highlight && !reducedMotion) drawWave(now);
        if (!reducedMotion) raf = requestAnimationFrame(animate);
    }

    window.addEventListener('resize', updateSize, { passive: true });
    updateSize();
    bootStopAnimation = () => {
        stopped = true;
        if (raf != null) cancelAnimationFrame(raf);
    };
    raf = requestAnimationFrame(animate);
    fetch('moscow-major-roads.json', { cache: 'no-cache' })
        .then((response) => {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        })
        .then((data) => {
            if (
                data.format !== 'moscow-major-roads-v1' ||
                !Array.isArray(data.paths) ||
                !Array.isArray(data.extent)
            ) {
                throw new Error('Unexpected OSM road asset format');
            }
            network = data;
            if (!stopped) prepare();
            if (reducedMotion && !stopped) animate(performance.now());
        })
        .catch((error) => {
            // The basemap loader remains independent of this decorative asset.
            console.warn('[boot] OSM road illustration not available:', error);
        });
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
