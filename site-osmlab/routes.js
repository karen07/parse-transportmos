/* Night Highway-inspired Canvas loading scene. Runs independently from the
 * transport data Worker; no third-party scripts or network calls. */
const bootUI = {
    overlay: document.getElementById('boot-overlay'),
    canvas: document.getElementById('boot-highway'),
    stage: document.getElementById('boot-stage'),
    percent: document.getElementById('boot-percent'),
    meter: document.getElementById('boot-meter'),
    fill: document.getElementById('boot-meter-fill'),
    details: document.getElementById('boot-details'),
    error: document.getElementById('boot-error'),
    errorMessage: document.getElementById('boot-error-message'),
};
const bootReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let bootAnimationId = null;
const roadContext = bootUI.canvas.getContext('2d', { alpha: false });
const ROAD_W = 800,
    ROAD_H = 400,
    HORIZON = 206,
    MID = 400;
const roadStars = Array.from({ length: 75 }, (_, i) => ({
    x: (i * 263.819 + 47) % ROAD_W,
    y: (i * 79.117 + 17) % 164,
    size: 0.5 + (i % 4) * 0.3,
    glow: (i % 13) / 13,
}));
const roadNoise = (x) => {
    const n = Math.sin(x * 78.233 + 12.983) * 43758.5453;
    return n - Math.floor(n);
};
function polygon(ctx, points, fill) {
    ctx.beginPath();
    ctx.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i++) ctx.lineTo(points[i][0], points[i][1]);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
}
function luminousDot(ctx, x, y, radius, color, halo) {
    if (halo) {
        const glow = ctx.createRadialGradient(x, y, 0, x, y, radius * 7);
        glow.addColorStop(0, halo);
        glow.addColorStop(1, '#0000');
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(x, y, radius * 7, 0, Math.PI * 2);
        ctx.fill();
    }
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
}
function mountainRidge(ctx, base, height, frequency, fill, seed) {
    const pts = [
        [0, ROAD_H],
        [0, base],
    ];
    for (let x = 0; x <= ROAD_W; x += 5) {
        const slow = Math.sin(x * frequency + seed) * 0.65;
        const broad = Math.sin(x * frequency * 0.43 + seed * 3.2) * 0.32;
        const jagged = (roadNoise(Math.floor(x / 13) + seed) - 0.5) * 0.12;
        pts.push([x, base - height * Math.max(0, slow + broad + jagged + 0.4)]);
    }
    pts.push([ROAD_W, ROAD_H]);
    polygon(ctx, pts, fill);
}
function drawHighwayScene(ms) {
    if (!roadContext) return;
    const ctx = roadContext;
    const t = ms * 0.001;
    const w = ROAD_W,
        h = ROAD_H;
    // Dusk sky, final warm band behind the mountain range.
    let sky = ctx.createLinearGradient(0, 0, 0, HORIZON + 42);
    sky.addColorStop(0, '#060b1f');
    sky.addColorStop(0.39, '#262447');
    sky.addColorStop(0.71, '#6c3b69');
    sky.addColorStop(0.89, '#b85a70');
    sky.addColorStop(1, '#f1a36a');
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, w, h);
    const sun = ctx.createRadialGradient(420, 215, 10, 420, 215, 260);
    sun.addColorStop(0, '#ffb36a42');
    sun.addColorStop(1, '#ffb36a00');
    ctx.fillStyle = sun;
    ctx.fillRect(70, 25, 680, 260);
    // Fine sky texture, blinking stars, moon crescent.
    for (let i = 0; i < roadStars.length; i++) {
        const star = roadStars[i];
        ctx.globalAlpha = 0.22 + (0.3 * (1 + Math.sin(t * 0.9 + i * 2.5))) / 2;
        ctx.fillStyle = '#e7e5ff';
        ctx.fillRect(star.x, star.y, star.size, star.size);
    }
    ctx.globalAlpha = 1;
    luminousDot(ctx, 608, 53, 8, '#ffe9d2', '#f3d7c72e');
    luminousDot(ctx, 612, 49, 7.4, '#0b1229', null);
    // Wispy coloured cloud shelves.
    ctx.globalAlpha = 0.2;
    for (let i = 0; i < 9; i++) {
        const y = 145 + i * 6 + Math.sin(i * 3.1) * 8;
        ctx.strokeStyle = i % 2 ? '#f7b086' : '#392746';
        ctx.lineWidth = 1.4 + (i % 4) * 0.5;
        ctx.beginPath();
        ctx.ellipse(160 + i * 51, y, 93, 2.2 + (i % 3), -0.045, 0, Math.PI * 2);
        ctx.stroke();
    }
    ctx.globalAlpha = 1;
    mountainRidge(ctx, 237, 43, 0.011, '#30233d', 2.3);
    mountainRidge(ctx, 244, 31, 0.018, '#1b1b34', 4.7);
    mountainRidge(ctx, 255, 19, 0.027, '#11172b', 6.7);
    // Foreground desert silhouettes.
    polygon(
        ctx,
        [
            [0, 260],
            [110, 257],
            [251, 275],
            [0, 400],
        ],
        '#121523',
    );
    polygon(
        ctx,
        [
            [800, 256],
            [702, 260],
            [548, 285],
            [800, 400],
        ],
        '#151522',
    );
    const farLeft = MID - 10,
        farRight = MID + 10;
    const nearLeft = MID - 360,
        nearRight = MID + 360;
    // Soft road shoulder glow and dark asphalt.
    polygon(
        ctx,
        [
            [farLeft - 3, HORIZON],
            [farRight + 3, HORIZON],
            [nearRight + 24, h],
            [nearLeft - 24, h],
        ],
        '#b85b6532',
    );
    const asphalt = ctx.createLinearGradient(0, HORIZON, 0, h);
    asphalt.addColorStop(0, '#19182a');
    asphalt.addColorStop(0.65, '#10131e');
    asphalt.addColorStop(1, '#080c16');
    polygon(
        ctx,
        [
            [farLeft, HORIZON],
            [farRight, HORIZON],
            [nearRight, h],
            [nearLeft, h],
        ],
        asphalt,
    );
    // Perspective edge: y is quadratic in depth, width grows with distance.
    const project = (z, side) => {
        const q = Math.max(0, Math.min(1, z));
        const p = q * q;
        const y = HORIZON + (h - HORIZON) * p;
        const edge = 10 + 350 * p;
        return { x: MID + side * edge, y, p };
    };
    for (const side of [-1, 1]) {
        ctx.strokeStyle = side === -1 ? '#db786a' : '#e7ad7e';
        ctx.lineWidth = 1.6;
        ctx.shadowColor = '#ffa46c';
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.moveTo(project(0, side).x, HORIZON);
        ctx.lineTo(project(1, side).x, h);
        ctx.stroke();
        ctx.shadowBlur = 0;
    }
    // Dashed central lane markers moving towards the viewer.
    const shift = (t * 0.25) % 1;
    for (let i = 0; i < 15; i++) {
        const z = (i / 15 + shift) % 1;
        const z2 = Math.min(1, z + 0.018 + z * 0.036);
        const a = project(z, 0),
            b = project(z2, 0);
        if (b.y - a.y < 0.2) continue;
        const wa = 0.6 + 2.4 * a.p,
            wb = 0.6 + 2.4 * b.p;
        polygon(
            ctx,
            [
                [MID - wa, a.y],
                [MID + wa, a.y],
                [MID + wb, b.y],
                [MID - wb, b.y],
            ],
            z > 0.65 ? '#f2d6b3' : '#d2a5a2',
        );
    }
    // Roadside reflective posts, telephone poles, and warm roadside lights.
    for (let i = 0; i < 18; i++) {
        const z = (i / 18 + t * 0.125) % 1;
        if (z < 0.08) continue;
        const o = project(z, 1);
        const half = o.x - MID;
        const size = 1.4 + 17 * o.p;
        for (const side of [-1, 1]) {
            const x = MID + side * (half + size * 1.2);
            const y = o.y;
            ctx.strokeStyle = '#707084';
            ctx.lineWidth = 0.6 + 3.4 * o.p;
            ctx.beginPath();
            ctx.moveTo(x, y);
            ctx.lineTo(x, y - size * 1.55);
            ctx.stroke();
            luminousDot(
                ctx,
                x,
                y - size * 1.5,
                0.7 + 2 * o.p,
                side === -1 ? '#ff6957' : '#ffd7a6',
                z > 0.45 ? '#ee92663a' : null,
            );
        }
        if (i % 3 === 0 && z > 0.15) {
            const x = MID - (half + size * 3.5);
            const poleHeight = 13 + 73 * o.p;
            ctx.strokeStyle = '#242437';
            ctx.lineWidth = 1 + 3 * o.p;
            ctx.beginPath();
            ctx.moveTo(x, o.y);
            ctx.lineTo(x, o.y - poleHeight);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(x - 9 * o.p, o.y - poleHeight * 0.8);
            ctx.lineTo(x + 9 * o.p, o.y - poleHeight * 0.8);
            ctx.stroke();
        }
    }
    // Ahead: persistent tail lamps, with a subtle body silhouette.
    const sway = Math.sin(t * 0.7) * 3;
    const carY = 267 + Math.sin(t * 0.5) * 1.4;
    polygon(
        ctx,
        [
            [MID - 23 + sway, carY + 2],
            [MID - 17 + sway, carY - 10],
            [MID + 17 + sway, carY - 10],
            [MID + 23 + sway, carY + 2],
            [MID + 20 + sway, carY + 11],
            [MID - 20 + sway, carY + 11],
        ],
        '#1a1a28',
    );
    polygon(
        ctx,
        [
            [MID - 14 + sway, carY - 9],
            [MID - 10 + sway, carY - 16],
            [MID + 10 + sway, carY - 16],
            [MID + 14 + sway, carY - 9],
        ],
        '#383348',
    );
    luminousDot(ctx, MID - 18 + sway, carY + 1, 3, '#ff584f', '#fa354849');
    luminousDot(ctx, MID + 18 + sway, carY + 1, 3, '#ff584f', '#fa354849');
    // Periodic approaching oncoming headlights, left-hand side of divider.
    const approach = (t * 0.16) % 1;
    const p = approach * approach;
    const ax = MID - 8 - 126 * p,
        ay = HORIZON + 195 * p;
    const spread = 2 + 16 * p;
    if (approach < 0.94) {
        luminousDot(ctx, ax - spread / 2, ay, 1 + 4 * p, '#fff0c9', '#ffd49065');
        luminousDot(ctx, ax + spread / 2, ay, 1 + 4 * p, '#fff0c9', '#ffd49065');
    }
    // Optical grain as stippled halftone cells, original ascii.rest-style feel.
    ctx.globalAlpha = 0.17;
    ctx.fillStyle = '#b8a4ba';
    for (let yi = 0; yi < h; yi += 5) {
        for (let xi = yi % 10 ? 3 : 0; xi < w; xi += 10) {
            if (roadNoise(xi * 0.02 + yi * 0.09) > 0.53) ctx.fillRect(xi, yi, 1, 1);
        }
    }
    ctx.globalAlpha = 1;
    const vignette = ctx.createRadialGradient(MID, 205, 115, MID, 205, 490);
    vignette.addColorStop(0, '#0000');
    vignette.addColorStop(1, '#030614b8');
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, w, h);
}
let roadLastFrame = 0;
function updateBootFrame(timestamp) {
    if (bootUI.overlay.hidden) return;
    if (!document.hidden && (timestamp - roadLastFrame >= 66 || !roadLastFrame)) {
        roadLastFrame = timestamp;
        drawHighwayScene(bootReducedMotion ? 0 : timestamp);
    }
    if (!bootReducedMotion) bootAnimationId = requestAnimationFrame(updateBootFrame);
}
if (roadContext) {
    if (bootReducedMotion) drawHighwayScene(0);
    else bootAnimationId = requestAnimationFrame(updateBootFrame);
}
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
    if (bootAnimationId !== null) cancelAnimationFrame(bootAnimationId);
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
        const worker = new Worker(new URL('routes-data-worker.js', document.baseURI));
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
        worker.postMessage({ url: new URL('routes.json.gz', document.baseURI).href });
    });
}

function loadExternalScript(url, isLoaded) {
    return new Promise((resolve, reject) => {
        if (isLoaded()) {
            resolve();
            return;
        }

        const script = document.createElement('script');
        script.src = url;
        script.onload = () => {
            if (isLoaded()) {
                resolve();
            } else {
                reject(new Error('Map library did not initialize: ' + url));
            }
        };
        script.onerror = () => {
            reject(new Error('Failed to load map library: ' + url));
        };
        document.head.appendChild(script);
    });
}

async function loadMapLibraries() {
    await loadExternalScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js', () => !!window.L);
    await loadExternalScript(
        'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl-csp.js',
        () => !!window.maplibregl,
    );
    // v4 CSP bundle with a same-origin classic Worker: no blob: exception.
    window.maplibregl.setWorkerUrl(new URL('routes-maplibre-worker.js', document.baseURI).href);
    await loadExternalScript(
        'https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.22/' + 'leaflet-maplibre-gl.js',
        () => !!window.L.maplibreGL,
    );
}

(async () => {
    const bootStarted = performance.now();
    console.info('[map] BOOT START');
    // Library scripts and large compressed network payload load concurrently.
    const [data] = await Promise.all([loadTransportData(), loadMapLibraries()]);
    bootPhase('RENDERING MAP', 'Preparing controls and map layers...');
    console.info('[map] BOOT libraries', Math.round(performance.now() - bootStarted), 'ms');
    console.info('[map] BOOT data decoded', Math.round(performance.now() - bootStarted), 'ms');

    const WALK_SPEED_M_PER_MIN = 80.0;
    const BUS_SPEED_M_PER_MIN = 320.0;
    const TRAM_SPEED_M_PER_MIN = 300.0;
    const TROLLEYBUS_SPEED_M_PER_MIN = 280.0;
    const METRO_SPEED_M_PER_MIN = 630.0;
    const MCC_SPEED_M_PER_MIN = 780.0;
    const MCD_SPEED_M_PER_MIN = 900.0;
    const TRAIN_SPEED_M_PER_MIN = 850.0;
    const STOP_DELAY_MIN = 0.35;
    const BOARDING_WAIT_MIN = 4.0;
    const MODE_SPEEDS = {
        bus: BUS_SPEED_M_PER_MIN,
        tram: TRAM_SPEED_M_PER_MIN,
        trolleybus: TROLLEYBUS_SPEED_M_PER_MIN,
        metro: METRO_SPEED_M_PER_MIN,
        mcc: MCC_SPEED_M_PER_MIN,
        mcd: MCD_SPEED_M_PER_MIN,
        train: TRAIN_SPEED_M_PER_MIN,
    };
    const MODE_BOARDING_WAITS = {
        bus: 4.0,
        tram: 4.0,
        trolleybus: 4.0,
        metro: 2.5,
        mcc: 4.0,
        mcd: 5.0,
        train: 8.0,
    };
    const ROUGH_STOP_MIN = 1.4;
    const WALK_PREFERENCE_EXTRA = 0.8;
    const MAX_EVIDENCE_PER_PATH = 4;
    const MAX_TRANSFER_EXTENSION_OPTIONS = 2;
    const MAX_TRIP_ALTERNATIVES = 8;
    const RAPTOR_ROUNDS = 3;
    const MAX_RAPTOR_ONBOARD_LABELS = 6;
    const MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE = 4;
    const MAX_RAPTOR_TRANSFER_LABELS_PER_STOP = 24;
    const REASONABLE_TIME_FACTOR = 1.5;
    const REASONABLE_TIME_EXTRA_MIN = 10.0;
    const GRID_SIZE = 400.0;
    const EARTH_RADIUS_M = 6371000.0;

    const ORIGIN_LON = 37.607174;
    const ORIGIN_LAT = 55.74465;
    const ORIGIN_LAT_RAD = (ORIGIN_LAT * Math.PI) / 180.0;

    const stops = data.stops;
    const routes = data.routes;
    const modeInputs = Array.from(document.querySelectorAll('.mode-input'));
    function selectedTransportModes() {
        const modes = new Set(
            modeInputs.filter((input) => input.checked).map((input) => input.value),
        );
        if (modes.delete('rail')) {
            modes.add('mcd');
            modes.add('train');
        }
        return modes;
    }
    let enabledModes = selectedTransportModes();

    function isRouteEnabled(routeKey) {
        const route = routes[routeKey];
        return !!route && enabledModes.has(route.mode || route.route);
    }

    function boardingWaitForRoute(routeKey) {
        const route = routes[routeKey];
        const mode = route ? route.mode || route.route : 'bus';
        return MODE_BOARDING_WAITS[mode] ?? BOARDING_WAIT_MIN;
    }

    const TEXT = {
        stop: 'Stop',
        stopGenitive: 'stop',
        transfers: 'Transfers',
        tripTitle: 'Trip',
        backToReachability: 'Back to reachability',
        noTransfers: 'No transfers',
        oneTransfer: 'One transfer',
        twoTransfers: 'Two transfers',
        accessDirect: 'Reachable: no transfers',
        accessTransfer: 'Reachable: one transfer',
        accessTwoTransfers: 'Reachable: two transfers',
        doubleClick: 'Double-click: show trip',
        destination: 'Destination',
        boarding: 'Boarding',
        transferExit: 'Transfer: exit',
        transferBoarding: 'Transfer: boarding',
        groupDirect: 'No transfers',
        groupTransfer: 'After one transfer',
        groupTwoTransfers: 'After two transfers',
        restoreFailedStart: 'Could not restore a path to <b>',
        restoreFailedEnd: '</b> for the current walking limit and transfer mode.',
        walkPrefix: 'Walk ~<b>',
        walkToStop: ' min</b> to stop <b>',
        transportNotNeeded: '</b>. Transit is not needed.',
        rideStopsSuffix: ' stops).',
        transferWalkPrefix: 'Walk ~<b>',
        minutesColon: ' min</b>: ',
        optionPrefix: 'Option ',
        walkOnly: 'walk',
        walkShort: 'walk',
        minutesShort: 'min',
        stopsShort: 'stops',
    };

    const walkTimeInput = document.getElementById('walk-time');
    const transferCountInput = document.getElementById('transfer-count');
    const travelTimeLimitInput = document.getElementById('travel-time-limit');
    const circleOpacityInput = document.getElementById('circle-opacity');
    const walkTimeValue = document.getElementById('walk-time-value');
    const transferModeValue = document.getElementById('transfer-mode-value');
    const travelTimeLimitValue = document.getElementById('travel-time-limit-value');
    const transferLabel = document.getElementById('transfer-label');
    const circleOpacityValue = document.getElementById('circle-opacity-value');
    const legendOriginText = document.getElementById('legend-origin-text');
    const analysisSummary = document.getElementById('analysis-summary');
    const tripPanel = document.getElementById('trip-panel');
    const tripTitle = document.getElementById('trip-title');
    const tripAlternatives = document.getElementById('trip-alternatives');
    const tripSteps = document.getElementById('trip-steps');
    const tripBackTop = document.getElementById('trip-back-top');
    const tripBack = document.getElementById('trip-back');
    const resetOrigin = document.getElementById('reset-origin');
    const timingStatus = document.getElementById('timing-status');

    const TRAVEL_TIME_INFINITY_SLIDER_VALUE = 185;
    let walkMinutes = Number(walkTimeInput.value);
    let walkRadius = walkMinutes * WALK_SPEED_M_PER_MIN;
    let maxTransfers = Number(transferCountInput.value);
    let maxTravelMinutes = 60;
    let circleOpacity = Number(circleOpacityInput.value) / 100.0;
    let selectedPoint = null;
    let selectedRouteFocus = null;
    let renderFrame = null;
    let renderGeneration = 0;
    // Profiling reports wall time to FULLY IDLE, not just the end of RAPTOR.
    // Measurement overhead is deliberately small: aggregate once per frame/batch.
    let runProfile = null;
    let runSerial = 0;
    const PROFILE_HEARTBEAT_MS = 5000;

    function profileElapsed(p) {
        return Math.round(performance.now() - p.started);
    }

    function profileSnapshot(p) {
        const wave = p.wave;
        const walk = wave ? wave.stats() : null;
        const stopPending = Math.max(0, stopPaintQueue.length - stopPaintCursor);
        return {
            elapsedMs: profileElapsed(p),
            phase: p.searchDone && wave && !wave.idle() ? 'Walking coverage wave' : p.currentPhase,
            transportDone: p.searchDone,
            wave: walk,
            walkingLines: [0, 1, 2, 3].map((tier) => coverageLinesForTier(tier).length),
            walkingPaintPending: coverageCursor.map(
                (position, tier) => coverageLinesForTier(tier).length - position,
            ),
            dirtyCoverageTiles: coverageMasksDirty ? 1 : 0,
            coverageComposites: coverageCompositeCount,
            routeEdges: routeEdgeRecords.map((items) => items.length),
            routePaintPending: routeEdgeRecords.map(
                (items, tier) => items.length - routeCanvasCursors[tier],
            ),
            stopCount: displayedStopIndexes.size,
            stopPaintPending: stopPending,
            stopRedrawPending: stopCanvasRedrawItems.length - stopCanvasRedrawIndex,
            cpuMs: { ...p.cpu },
            frames: { ...p.frames },
            evidenceEvents: p.evidenceEvents,
            longTasks: { ...p.longTasks },
        };
    }

    function profilePending(p) {
        if (!p.searchDone || !p.wave || !p.wave.idle()) return true;
        if (hasUnpaintedCoverage() || coveragePaintFrame !== null) return true;
        if (routePaintFrame !== null || routeCanvasZooming) return true;
        if (routeEdgeRecords.some((items, tier) => items.length !== routeCanvasCursors[tier]))
            return true;
        if (stopPaintFrame !== null || stopCanvasRedrawFrame !== null) return true;
        if (stopPaintCursor < stopPaintQueue.length) return true;
        if (stopCanvasRedrawIndex < stopCanvasRedrawItems.length) return true;
        return false;
    }

    function profileUpdateStatus(p) {
        if (!p || p !== runProfile) return;
        const seconds = (profileElapsed(p) / 1000).toFixed(1);
        if (p.searchDone) {
            timingStatus.textContent =
                'Results ' +
                (p.resultsReadyMs / 1000).toFixed(1) +
                ' s | Drawing... ' +
                seconds +
                ' s';
        } else {
            timingStatus.textContent = p.currentPhase + '... ' + seconds + ' s';
        }
    }

    function profileFinish(p) {
        if (p !== runProfile || p.finished || profilePending(p)) return;
        if (p.finalFrame) return;
        // Wait for the browser to present the final completed Canvas frame.
        p.finalFrame = requestAnimationFrame(() => {
            p.finalFrame = null;
            if (p !== runProfile || p.finished || profilePending(p)) return;
            p.finished = true;
            clearInterval(p.timer);
            if (p.longTaskObserver) p.longTaskObserver.disconnect();
            const report = {
                version: '0.7.3',
                run: p.serial,
                settings: p.settings,
                totalWallMs: profileElapsed(p),
                resultsReadyMs: p.resultsReadyMs,
                graph: {
                    walkNodes: walkNodes.length,
                    walkEdges: walkEdges.length,
                    stops: stops.length,
                    routeKeys: Object.keys(routes).length,
                },
                phases: p.phases,
                cpuMs: p.cpu,
                frames: p.frames,
                firstVisibleMs: p.firstVisible,
                final: profileSnapshot(p),
                longTasks: p.longTasks,
            };
            window.transportLastProfile = report;
            timingStatus.textContent =
                'Results ' +
                (report.resultsReadyMs / 1000).toFixed(1) +
                ' s | ' +
                'Fully drawn ' +
                (report.totalWallMs / 1000).toFixed(1) +
                ' s';
            console.info('[map] FULLY IDLE', report.totalWallMs + ' ms');
            console.table(p.phases);
            console.info('[map] FINAL REPORT (copy window.transportLastProfile)', report);
        });
    }

    function profileMarkPaint(name, elapsed, count = 0) {
        const p = runProfile;
        if (!p || p.finished) return;
        p.cpu[name] = (p.cpu[name] || 0) + elapsed;
        p.frames[name] = (p.frames[name] || 0) + 1;
        p.frames[name + 'Items'] = (p.frames[name + 'Items'] || 0) + count;
        if (!p.firstVisible[name] && count > 0) {
            p.firstVisible[name] = profileElapsed(p);
            console.info('[map] FIRST', name, p.firstVisible[name] + ' ms');
        }
        if (p.searchDone) profileFinish(p);
    }

    function profilePhaseStart(name) {
        const p = runProfile;
        if (!p || p.finished) return;
        p.currentPhase = name;
        p.active[name] = performance.now();
        console.info('[map] START', name, '+' + profileElapsed(p) + ' ms');
        profileUpdateStatus(p);
    }

    function profilePhaseEnd(name, details = null) {
        const p = runProfile;
        if (!p || p.finished || p.active[name] === undefined) return;
        const started = p.active[name];
        delete p.active[name];
        const elapsed = Math.round(performance.now() - started);
        p.phases.push({ phase: name, wallMs: elapsed, details: details || '' });
        console.info('[map] DONE', name, elapsed + ' ms', details || '');
        profileUpdateStatus(p);
    }

    function profileAbort(reason) {
        const p = runProfile;
        if (!p || p.finished) return;
        clearInterval(p.timer);
        if (p.longTaskObserver) p.longTaskObserver.disconnect();
        if (p.finalFrame !== null) cancelAnimationFrame(p.finalFrame);
        p.finished = true;
        console.info('[map] ABORT', reason, '+' + profileElapsed(p) + ' ms', profileSnapshot(p));
        timingStatus.textContent = 'Stopped (' + reason + ')';
        runProfile = null;
    }

    function profileBegin(token) {
        const p = {
            token,
            serial: ++runSerial,
            started: performance.now(),
            finished: false,
            searchDone: false,
            resultsReadyMs: null,
            wave: null,
            finalFrame: null,
            currentPhase: 'Preparing',
            active: {},
            phases: [],
            cpu: {},
            frames: {},
            evidenceEvents: 0,
            firstVisible: {},
            settings: {
                walkMinutes,
                maxTransfers,
                maxTravelMinutes,
                enabledModes: Array.from(enabledModes),
                start: [selectedPoint.lat, selectedPoint.lon],
            },
            timer: null,
            longTasks: { count: 0, totalMs: 0, maxMs: 0 },
            longTaskObserver: null,
        };
        runProfile = p;
        window.transportCurrentProfile = () => {
            if (!runProfile || runProfile.finished) return null;
            return profileSnapshot(runProfile);
        };
        if (typeof PerformanceObserver === 'function') {
            try {
                p.longTaskObserver = new PerformanceObserver((list) => {
                    for (const entry of list.getEntries()) {
                        if (entry.startTime < p.started || p.finished) continue;
                        p.longTasks.count++;
                        p.longTasks.totalMs += entry.duration;
                        p.longTasks.maxMs = Math.max(p.longTasks.maxMs, entry.duration);
                    }
                });
                p.longTaskObserver.observe({ entryTypes: ['longtask'] });
            } catch (_) {
                p.longTaskObserver = null;
            }
        }
        window.transportLastProfile = null;
        console.info('[map] RUN', p.serial, 'START', p.settings);
        timingStatus.textContent = 'Preparing...';
        p.timer = setInterval(() => {
            if (p !== runProfile || p.finished) return;
            const snap = profileSnapshot(p);
            console.info('[map] STILL WORKING', snap);
            profileUpdateStatus(p);
            profileFinish(p);
        }, PROFILE_HEARTBEAT_MS);
        return p;
    }

    let analysisCacheKey = null;
    let analysisCache = null;
    let targetPoint = null;
    let targetPickMode = false;
    let tripAlternativeIndex = 0;
    const MAP_DOUBLE_CLICK_DELAY_MS = 260;
    let pendingMapClickTimer = null;
    let suppressMapClickUntil = 0;
    let displayedStopIndexes = new Set();
    // Stop dots use one Canvas, not one Leaflet object per stop.
    const directionShapeCache = new WeakMap();
    const routeLegTraversalCache = new Map();
    const boardingOptionsCache = new Map();
    const routeEdgeMetricsCache = new Map();
    const reachabilitySearchCache = new Map();
    const itineraryCache = new Map();
    let travelRenderTimer = null;

    const map = L.map('map', {
        preferCanvas: true,
        minZoom: 1,
    }).setView([ORIGIN_LAT, ORIGIN_LON], 10);

    map.doubleClickZoom.disable();

    function createMapPane(name, zIndex, pointerEvents) {
        const pane = map.createPane(name);
        pane.style.zIndex = String(zIndex);
        pane.style.pointerEvents = pointerEvents;
    }

    createMapPane('coveragePane', 410, 'none');
    createMapPane('routePane', 420, 'none');
    createMapPane('tripWalkPane', 430, 'none');
    createMapPane('tripRoutePane', 440, 'none');
    createMapPane('stopPane', 650, 'auto');
    createMapPane('tripMarkerPane', 660, 'auto');
    createMapPane('originPane', 700, 'none');

    const MODE_NAMES = {
        heading: 'Transport modes',
        bus: 'Bus',
        tram: 'Tram',
        trolleybus: 'Trolleybus',
        metro: 'Metro',
        mcc: 'MCC',
        rail: 'MCD + Trains',
    };
    const MODE_HINT = 'Surface transport on by default. Travel times are estimates.';

    const modeHeading = document.getElementById('transport-modes-heading');
    modeHeading.textContent = MODE_NAMES.heading;
    document
        .querySelector('#transport-modes .mode-grid')
        .setAttribute('aria-label', MODE_NAMES.heading);

    document.querySelectorAll('[data-mode-name]').forEach((element) => {
        element.textContent = MODE_NAMES[element.dataset.modeName];
    });
    document.getElementById('transport-modes-hint').textContent = MODE_HINT;

    const vectorMapLayer = L.maplibreGL({
        style: 'https://tiles.openfreemap.org/styles/bright',
        attribution:
            '<a href="https://openfreemap.org/">OpenFreeMap</a> | ' +
            '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> | ' +
            '<a href="https://www.openstreetmap.org/copyright">' +
            'OpenStreetMap contributors</a>',
    }).addTo(map);
    // Start listening while the rest of the UI is constructed.
    const baseMapReady = waitForBaseMapReady(vectorMapLayer);
    // Attach the rejection handler immediately even if other initialization is slow.
    baseMapReady.catch(() => {});

    // Paint opaque tiers offscreen, then composite one translucent output.
    // A pixel belongs to the smallest transfer tier, never a blend of tiers.
    const coverageCanvas = document.createElement('canvas');
    coverageCanvas.style.position = 'absolute';
    coverageCanvas.style.pointerEvents = 'none';
    coverageCanvas.style.display = 'block';
    map.getPane('coveragePane').appendChild(coverageCanvas);
    const coverageContext = coverageCanvas.getContext('2d', { alpha: true });
    const coverageCanvases = Array.from({ length: 4 }, () => document.createElement('canvas'));
    const coverageContexts = coverageCanvases.map((canvas) =>
        canvas.getContext('2d', { alpha: true }),
    );

    const twoTransferRouteLayer = L.layerGroup().addTo(map);
    const transferRouteLayer = L.layerGroup().addTo(map);
    const directRouteLayer = L.layerGroup().addTo(map);
    const stopLayer = L.layerGroup().addTo(map);
    // One low-overhead route canvas per transfer tier. Edges arrive directly
    // from the RAPTOR scan, rather than waiting for the final route summary.
    const routeCanvases = Array.from({ length: 3 }, (_, tier) => {
        const canvas = document.createElement('canvas');
        canvas.style.position = 'absolute';
        canvas.style.pointerEvents = 'none';
        canvas.style.zIndex = String(3 - tier);
        canvas.style.opacity = String([0.85, 0.48, 0.34][tier]);
        map.getPane('routePane').appendChild(canvas);
        return canvas;
    });
    const routeContexts = routeCanvases.map((canvas) => canvas.getContext('2d', { alpha: true }));
    const stopCanvas = document.createElement('canvas');
    stopCanvas.style.position = 'absolute';
    stopCanvas.style.pointerEvents = 'none';
    map.getPane('stopPane').appendChild(stopCanvas);
    const stopContext = stopCanvas.getContext('2d', { alpha: true });
    let stopCanvasOrigin = L.point(0, 0);
    let stopCanvasScale = 1;
    let stopCanvasRedrawFrame = null;
    let stopCanvasRepaintTimer = null;
    let stopCanvasZooming = false;
    let stopCanvasRedrawItems = [];
    let stopCanvasRedrawIndex = 0;
    let stopCanvasBounds = null;
    const visibleStopIndexes = new Set();
    const stopHitGrid = new Map();
    const STOP_HIT_CELL = 32;
    const originLayer = L.layerGroup().addTo(map);
    const tripWalkLayer = L.layerGroup().addTo(map);
    const tripRouteLayer = L.layerGroup().addTo(map);
    const tripMarkerLayer = L.layerGroup().addTo(map);

    function escapeHtml(value) {
        if (value === null || value === undefined) {
            return '';
        }

        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;');
    }

    function project(lon, lat) {
        const x =
            EARTH_RADIUS_M * (((lon - ORIGIN_LON) * Math.PI) / 180.0) * Math.cos(ORIGIN_LAT_RAD);

        const y = EARTH_RADIUS_M * (((lat - ORIGIN_LAT) * Math.PI) / 180.0);

        return [x, y];
    }

    function routeColor(key) {
        let hash = 0;

        for (let i = 0; i < key.length; ++i) {
            hash = (hash << 5) - hash + key.charCodeAt(i);
            hash |= 0;
        }

        return 'hsl(' + (Math.abs(hash) % 360) + ',70%,42%)';
    }

    function updateTargetControls() {
        map.getContainer().classList.toggle('target-pick-mode', targetPickMode);
    }

    function setTargetPickMode(enabled) {
        targetPickMode = Boolean(enabled && selectedPoint);
        updateTargetControls();
    }

    function updateControls() {
        const newWalkMinutes = Number(walkTimeInput.value);
        const newWalkRadius = newWalkMinutes * WALK_SPEED_M_PER_MIN;
        const newMaxTransfers = Number(transferCountInput.value);
        const rawTravelTimeLimit = Number(travelTimeLimitInput.value);
        const newMaxTravelMinutes =
            rawTravelTimeLimit >= TRAVEL_TIME_INFINITY_SLIDER_VALUE ? Infinity : rawTravelTimeLimit;

        if (newWalkRadius !== walkRadius) {
            reachabilitySearchCache.clear();
            itineraryCache.clear();
        }

        if (newMaxTransfers !== maxTransfers || newMaxTravelMinutes !== maxTravelMinutes) {
            itineraryCache.clear();
        }

        walkMinutes = newWalkMinutes;
        walkRadius = newWalkRadius;
        maxTransfers = newMaxTransfers;
        maxTravelMinutes = newMaxTravelMinutes;
        circleOpacity = Number(circleOpacityInput.value) / 100.0;

        walkTimeValue.textContent =
            walkMinutes.toFixed(0) + ' min (~' + Math.round(walkRadius) + ' m)';

        transferLabel.textContent = TEXT.transfers;
        tripTitle.textContent = TEXT.tripTitle;
        tripBackTop.textContent = TEXT.backToReachability;
        tripBack.textContent = TEXT.backToReachability;

        const transferModeLabels = [TEXT.noTransfers, TEXT.oneTransfer, TEXT.twoTransfers];
        transferModeValue.textContent = transferModeLabels[maxTransfers];

        travelTimeLimitValue.textContent = Number.isFinite(maxTravelMinutes)
            ? Math.round(maxTravelMinutes) + ' min'
            : 'No limit';

        circleOpacityValue.textContent = Math.round(circleOpacity * 100) + '%';

        legendOriginText.textContent =
            walkMinutes.toFixed(0) + ' min (~' + Math.round(walkRadius) + ' m) from selected point';

        updateTargetControls();
    }

    // Walk-only graph: actual OSM topology, never an Euclidean catchment disk.
    // Direction: 1=both, 2=a->b, 3=b->a, 5=stop access connector.
    const walking = data.walking;
    if (
        !walking ||
        !walking.nodeXY ||
        !walking.edgeAB ||
        !walking.edgeLengths ||
        !walking.edgeDirs
    ) {
        throw new Error('Missing packed pedestrian graph in transport data');
    }
    const walkNodeXY = walking.nodeXY;
    const walkEdgeAB = walking.edgeAB;
    const walkEdgeLengths = walking.edgeLengths;
    const walkEdgeDirs = walking.edgeDirs;
    const walkNodes = { length: walking.nodeCount };
    const walkEdges = { length: walking.edgeCount };
    function walkNodeAt(i) {
        return [walkNodeXY[2 * i] / 10, walkNodeXY[2 * i + 1] / 10];
    }
    function walkEdgeAt(i) {
        return [walkEdgeAB[2 * i], walkEdgeAB[2 * i + 1], walkEdgeLengths[i], walkEdgeDirs[i]];
    }
    const walkTransfers = data.walk_transfers;
    if (!walkTransfers || walkTransfers.offsets.length !== stops.length + 1) {
        throw new Error('Missing precomputed walking transfer graph');
    }
    const walkStopAtNode = new Map();
    const walkNodeAtStopXY = new Map();
    for (let i = 0; i < stops.length; ++i) {
        const node = stops[i].walk_node;
        if (Number.isInteger(node) && node >= 0) {
            walkNodeAtStopXY.set(stops[i].x.toFixed(1) + ':' + stops[i].y.toFixed(1), node);
            const entries = walkStopAtNode.get(node) || [];
            entries.push(i);
            walkStopAtNode.set(node, entries);
        }
    }

    // CSR adjacency avoids millions of per-node JavaScript arrays.
    const csrStarted = performance.now();
    const walkDegree = new Uint32Array(walkNodes.length);
    for (let i = 0; i < walkEdges.length; ++i) {
        const direction = walkEdgeDirs[i];
        if (direction !== 3 && direction !== 13) walkDegree[walkEdgeAB[2 * i]]++;
        if (direction !== 2 && direction !== 12) walkDegree[walkEdgeAB[2 * i + 1]]++;
    }
    const walkOffsets = new Uint32Array(walkNodes.length + 1);
    for (let i = 0; i < walkNodes.length; ++i) {
        walkOffsets[i + 1] = walkOffsets[i] + walkDegree[i];
    }
    const walkTo = new Uint32Array(walkOffsets[walkNodes.length]);
    const walkWeight = new Float32Array(walkTo.length);
    const walkNext = walkOffsets.slice(0, walkNodes.length);
    for (let i = 0; i < walkEdges.length; ++i) {
        const a = walkEdgeAB[2 * i],
            b = walkEdgeAB[2 * i + 1];
        const length = walkEdgeLengths[i],
            direction = walkEdgeDirs[i];
        if (direction !== 3 && direction !== 13) {
            const slot = walkNext[a]++;
            walkTo[slot] = b;
            walkWeight[slot] = length;
        }
        if (direction !== 2 && direction !== 12) {
            const slot = walkNext[b]++;
            walkTo[slot] = a;
            walkWeight[slot] = length;
        }
    }

    console.info(
        '[map] BOOT pedestrian CSR',
        Math.round(performance.now() - csrStarted),
        'ms; nodes:',
        walkNodes.length,
        'arcs:',
        walkTo.length,
    );

    // All walking searches use the existing CSR arrays in this thread.
    // Bounded generator steps let the browser render between expensive searches.
    const walkingStats = { queries: 0, cpuMs: 0, yields: 0, yieldMs: 0 };
    const WALK_SLICE_MS = 6;

    async function drainWalkingSteps(iterator, token, countStats = false) {
        let started = performance.now();
        if (countStats) walkingStats.queries++;
        while (token === renderGeneration) {
            const next = iterator.next();
            if (next.done) {
                if (countStats) walkingStats.cpuMs += performance.now() - started;
                return next.value;
            }
            if (performance.now() - started < WALK_SLICE_MS) continue;
            if (countStats) {
                walkingStats.cpuMs += performance.now() - started;
                walkingStats.yields++;
            }
            const waiting = performance.now();
            await eventLoopYield();
            if (countStats) walkingStats.yieldMs += performance.now() - waiting;
            started = performance.now();
        }
        return null;
    }

    async function warmWalkingPoint(point, token) {
        const xy = project(point.lon, point.lat);
        const key = xy[0].toFixed(1) + ':' + xy[1].toFixed(1) + ':' + walkRadius;
        if (walkPointCache.has(key)) return true;
        if (!(await warmWalkSegmentGrid(token))) return false;
        const anchor = snapWalkXY(xy[0], xy[1]);
        if (!anchor) {
            walkPointCache.set(key, {
                anchor: null,
                distance: new Map(),
                stopsReached: new Map(),
            });
            return true;
        }
        const result = await drainWalkingSteps(
            walkDijkstraSteps(walkSeeds(anchor), walkRadius),
            token,
            true,
        );
        if (!result || token !== renderGeneration) return false;
        walkPointCache.set(key, { anchor, ...result });
        if (walkPointCache.size > 12) {
            walkPointCache.delete(walkPointCache.keys().next().value);
        }
        return true;
    }

    const WALK_GRID_METERS = 100;
    const WALK_MAX_SNAP_METERS = 35;
    let walkSegmentGrid = null;
    let walkSegmentGridIterator = null;
    let walkPointCache = new Map();

    function makeMinHeap() {
        const array = [];
        return {
            push(distance, node) {
                const item = [distance, node];
                let i = array.length;
                array.push(item);
                while (i) {
                    const parent = (i - 1) >> 1;
                    if (array[parent][0] <= item[0]) break;
                    array[i] = array[parent];
                    i = parent;
                }
                array[i] = item;
            },
            pop() {
                if (!array.length) return null;
                const first = array[0];
                const last = array.pop();
                if (array.length) {
                    let i = 0;
                    while (2 * i + 1 < array.length) {
                        let child = 2 * i + 1;
                        if (child + 1 < array.length && array[child + 1][0] < array[child][0])
                            child++;
                        if (array[child][0] >= last[0]) break;
                        array[i] = array[child];
                        i = child;
                    }
                    array[i] = last;
                }
                return first;
            },
            get size() {
                return array.length;
            },
        };
    }

    function* buildWalkSegmentGridSteps() {
        const grid = new Map();
        for (let i = 0; i < walkEdges.length; ++i) {
            const direction = walkEdgeDirs[i];
            if (direction === 5 || direction >= 10) continue; // No virtual links.
            const a = walkEdgeAB[2 * i],
                b = walkEdgeAB[2 * i + 1];
            const ax = walkNodeXY[2 * a],
                ay = walkNodeXY[2 * a + 1];
            const bx = walkNodeXY[2 * b],
                by = walkNodeXY[2 * b + 1];
            const cellSize = WALK_GRID_METERS * 10;
            const x0 = Math.floor(Math.min(ax, bx) / cellSize);
            const x1 = Math.floor(Math.max(ax, bx) / cellSize);
            const y0 = Math.floor(Math.min(ay, by) / cellSize);
            const y1 = Math.floor(Math.max(ay, by) / cellSize);
            if ((x1 - x0 + 1) * (y1 - y0 + 1) > 10000) continue;
            for (let x = x0; x <= x1; ++x) {
                for (let y = y0; y <= y1; ++y) {
                    const key = x + ':' + y;
                    let entries = grid.get(key);
                    if (!entries) {
                        entries = [];
                        grid.set(key, entries);
                    }
                    entries.push(i);
                }
            }
            if ((i & 1023) === 0) yield;
        }
        walkSegmentGrid = grid;
        walkSegmentGridIterator = null;
        return true;
    }

    function buildWalkSegmentGrid() {
        if (walkSegmentGrid) return;
        if (!walkSegmentGridIterator) {
            walkSegmentGridIterator = buildWalkSegmentGridSteps();
        }
        // Synchronous access is still supported for explicit trip inspection.
        while (!walkSegmentGridIterator.next().done) {}
    }

    async function warmWalkSegmentGrid(token) {
        if (walkSegmentGrid) return true;
        if (!walkSegmentGridIterator) {
            walkSegmentGridIterator = buildWalkSegmentGridSteps();
        }
        // Preserve work on cancellation; the next selection resumes the same index.
        return Boolean(await drainWalkingSteps(walkSegmentGridIterator, token));
    }
    function snapWalkXY(x, y) {
        const stopNode = walkNodeAtStopXY.get(x.toFixed(1) + ':' + y.toFixed(1));
        if (stopNode !== undefined) return { node: stopNode, x, y };
        buildWalkSegmentGrid();
        const gx = Math.floor(x / WALK_GRID_METERS);
        const gy = Math.floor(y / WALK_GRID_METERS);
        const checked = new Set();
        let best = null;
        for (let dx = -1; dx <= 1; ++dx) {
            for (let dy = -1; dy <= 1; ++dy) {
                const entries = walkSegmentGrid.get(gx + dx + ':' + (gy + dy));
                if (!entries) continue;
                for (const edgeIndex of entries) {
                    if (checked.has(edgeIndex)) continue;
                    checked.add(edgeIndex);
                    const edge = walkEdgeAt(edgeIndex);
                    const a = walkNodeAt(edge[0]);
                    const b = walkNodeAt(edge[1]);
                    const ux = b[0] - a[0];
                    const uy = b[1] - a[1];
                    const length2 = ux * ux + uy * uy;
                    if (length2 < 0.0001) continue;
                    const t = Math.max(
                        0,
                        Math.min(1, ((x - a[0]) * ux + (y - a[1]) * uy) / length2),
                    );
                    const offset = Math.hypot(x - a[0] - t * ux, y - a[1] - t * uy);
                    if (offset > WALK_MAX_SNAP_METERS) continue;
                    if (!best || offset < best.offset) {
                        best = { edgeIndex, t, offset, x, y };
                    }
                }
            }
        }
        return best;
    }

    function walkSeeds(anchor) {
        if (anchor.node !== undefined) return [[anchor.node, 0]];
        const edge = walkEdgeAt(anchor.edgeIndex);
        const length = edge[2];
        const seeds = [];
        if (edge[3] !== 2 || anchor.t < 0.000001) {
            seeds.push([edge[0], anchor.offset + length * anchor.t]);
        }
        if (edge[3] !== 3 || anchor.t > 0.999999) {
            seeds.push([edge[1], anchor.offset + length * (1 - anchor.t)]);
        }
        return seeds;
    }

    function* walkDijkstraSteps(seeds, limit, savePrevious = false) {
        const distance = new Map();
        const stopsReached = new Map();
        const previous = savePrevious ? new Map() : null;
        const heap = makeMinHeap();
        for (const [node, cost] of seeds) {
            if (cost > limit || cost >= (distance.get(node) ?? Infinity)) continue;
            distance.set(node, cost);
            heap.push(cost, node);
            if (previous) previous.set(node, -1);
        }
        let settled = 0;
        while (heap.size) {
            const entry = heap.pop();
            const d = entry[0],
                node = entry[1];
            if (d > limit) break;
            if (d > distance.get(node) + 0.0001) continue;
            const atStop = walkStopAtNode.get(node);
            if (atStop) {
                for (const stopIndex of atStop) {
                    stopsReached.set(stopIndex, d);
                }
            }
            for (let i = walkOffsets[node]; i < walkOffsets[node + 1]; ++i) {
                const next = walkTo[i];
                const nextDistance = d + walkWeight[i];
                if (nextDistance > limit + 0.00001) continue;
                if (nextDistance >= (distance.get(next) ?? Infinity) - 0.00001) {
                    continue;
                }
                distance.set(next, nextDistance);
                if (previous) previous.set(next, node);
                heap.push(nextDistance, next);
            }
            if ((++settled & 127) === 0) yield;
        }
        return { distance, stopsReached, previous };
    }

    function walkDijkstra(seeds, limit, savePrevious = false) {
        const iterator = walkDijkstraSteps(seeds, limit, savePrevious);
        while (true) {
            const next = iterator.next();
            if (next.done) return next.value;
        }
    }

    function walkContextForXY(x, y) {
        const key = x.toFixed(1) + ':' + y.toFixed(1) + ':' + walkRadius;
        const cached = walkPointCache.get(key);
        if (cached) return cached;
        const anchor = snapWalkXY(x, y);
        const context = anchor
            ? { anchor, ...walkDijkstra(walkSeeds(anchor), walkRadius) }
            : { anchor: null, distance: new Map(), stopsReached: new Map() };
        walkPointCache.set(key, context);
        if (walkPointCache.size > 12) {
            walkPointCache.delete(walkPointCache.keys().next().value);
        }
        return context;
    }

    function walkContextForPoint(point) {
        const xy = project(point.lon, point.lat);
        return walkContextForXY(xy[0], xy[1]);
    }

    // DevTools diagnostic; helpers above are scoped inside the map async IIFE.
    // Usage: transportDebugStation("Station name")
    window.transportDebugStation = function (query) {
        const wanted = String(query || '')
            .trim()
            .toLocaleLowerCase();
        if (!wanted) {
            console.warn('Pass part of a station name to transportDebugStation().');
            return [];
        }
        const xy = selectedPoint ? project(selectedPoint.lon, selectedPoint.lat) : null;
        const reached = selectedPoint ? walkContextForPoint(selectedPoint).stopsReached : null;
        const result = [];
        for (let i = 0; i < stops.length; ++i) {
            const stop = stops[i];
            if (
                !String(stop.name || '')
                    .toLocaleLowerCase()
                    .includes(wanted)
            ) {
                continue;
            }
            const modes = Array.from(
                new Set(stop.routes.map((key) => routes[key]?.mode).filter(Boolean)),
            );
            result.push({
                index: i,
                name: stop.name,
                id: stop.id,
                modes: modes.join(', '),
                straight_m: xy ? Math.round(Math.hypot(stop.x - xy[0], stop.y - xy[1])) : null,
                walking_m: reached ? Math.round(reached.get(i) ?? Infinity) : null,
                walk_node: stop.walk_node,
                enabled_route: stop.routes.some(isRouteEnabled),
                route_count: stop.routes.length,
            });
        }
        result.sort((a, b) => (a.straight_m ?? 0) - (b.straight_m ?? 0));
        console.table(result);
        return result;
    };

    function findNearbyStopsXY(x, y) {
        return Array.from(walkContextForXY(x, y).stopsReached.keys()).filter((index) =>
            stops[index].routes.some(isRouteEnabled),
        );
    }

    function findNearbyStops(lat, lon) {
        const point = project(lon, lat);
        return findNearbyStopsXY(point[0], point[1]);
    }

    function walkDistanceToAnchor(context, anchor) {
        if (!anchor) return Infinity;
        if (anchor.node !== undefined) {
            return context.distance.get(anchor.node) ?? Infinity;
        }
        const edge = walkEdgeAt(anchor.edgeIndex);
        const length = edge[2];
        let d = Infinity;
        if (edge[3] !== 3 || anchor.t < 0.000001) {
            d = Math.min(
                d,
                (context.distance.get(edge[0]) ?? Infinity) + anchor.t * length + anchor.offset,
            );
        }
        if (edge[3] !== 2 || anchor.t > 0.999999) {
            d = Math.min(
                d,
                (context.distance.get(edge[1]) ?? Infinity) +
                    (1 - anchor.t) * length +
                    anchor.offset,
            );
        }
        const source = context.anchor;
        if (source && source.edgeIndex === anchor.edgeIndex) {
            const delta = anchor.t - source.t;
            if ((delta >= 0 && edge[3] !== 3) || (delta <= 0 && edge[3] !== 2)) {
                d = Math.min(d, Math.abs(delta) * length + source.offset + anchor.offset);
            }
        }
        return d;
    }

    function walkCoverageSegments(sources) {
        return walkCoverageFromNodes(
            sources
                .filter((item) => item.stop.walk_node >= 0)
                .map((item) => ({ node: item.stop.walk_node, radius: item.radius })),
        );
    }

    function walkCoverageFromNodes(sources) {
        // Multi-source Dijkstra: each arrival supplies its own remaining walk budget.
        const remaining = new Map();
        const heap = makeMinHeap();
        for (const source of sources) {
            const node = source.node;
            if (node < 0 || source.radius <= 0) continue;
            if (source.radius <= (remaining.get(node) ?? -1)) continue;
            remaining.set(node, source.radius);
            heap.push(-source.radius, node);
        }
        while (heap.size) {
            const [neg, node] = heap.pop();
            const budget = -neg;
            if (budget + 0.001 < remaining.get(node)) continue;
            for (let i = walkOffsets[node]; i < walkOffsets[node + 1]; ++i) {
                const next = walkTo[i];
                const nextBudget = budget - walkWeight[i];
                if (nextBudget <= 0 || nextBudget <= (remaining.get(next) ?? -1) + 0.001) continue;
                remaining.set(next, nextBudget);
                heap.push(-nextBudget, next);
            }
        }
        const lines = [];
        for (let i = 0; i < walkEdges.length; ++i) {
            const edge = walkEdgeAt(i);
            if (edge[3] === 5 || edge[3] >= 10) continue; // Actual sidewalks only.
            const a = remaining.get(edge[0]) ?? -1;
            const b = remaining.get(edge[1]) ?? -1;
            const length = edge[2];
            if (length <= 0) continue;
            let left = edge[3] === 3 ? 0 : Math.max(0, a / length);
            let right = edge[3] === 2 ? 0 : Math.max(0, b / length);
            left = Math.min(1, left);
            right = Math.min(1, right);
            if (left <= 0 && right <= 0) continue;
            if (left + right >= 1) {
                lines.push([edge[0], edge[1], 0, 1]);
            } else {
                if (left > 0) lines.push([edge[0], edge[1], 0, left]);
                if (right > 0) lines.push([edge[0], edge[1], 1 - right, 1]);
            }
        }
        return lines;
    }

    function walkPolylineXY(a, b) {
        const start = snapWalkXY(a[0], a[1]);
        const end = snapWalkXY(b[0], b[1]);
        if (!start || !end) return null;
        if (start.edgeIndex !== undefined && start.edgeIndex === end.edgeIndex) {
            const edge = walkEdgeAt(start.edgeIndex);
            const delta = end.t - start.t;
            if ((delta >= 0 && edge[3] !== 3) || (delta <= 0 && edge[3] !== 2)) {
                const pa = walkNodeAt(edge[0]),
                    pb = walkNodeAt(edge[1]);
                const from = [pa[0] + (pb[0] - pa[0]) * start.t, pa[1] + (pb[1] - pa[1]) * start.t];
                const to = [pa[0] + (pb[0] - pa[0]) * end.t, pa[1] + (pb[1] - pa[1]) * end.t];
                return [a, from, to, b];
            }
        }
        const results = walkDijkstra(walkSeeds(start), walkRadius + 100, true);
        const edge = end.node === undefined ? walkEdgeAt(end.edgeIndex) : null;
        const candidates =
            end.node !== undefined
                ? [[end.node, 0]]
                : [
                      ...(edge[3] !== 3 || end.t < 0.000001
                          ? [[edge[0], end.t * edge[2] + end.offset]]
                          : []),
                      ...(edge[3] !== 2 || end.t > 0.999999
                          ? [[edge[1], (1 - end.t) * edge[2] + end.offset]]
                          : []),
                  ];
        let target = -1;
        let best = Infinity;
        for (const [node, extra] of candidates) {
            const d = results.distance.get(node);
            if (d !== undefined && d + extra < best) {
                best = d + extra;
                target = node;
            }
        }
        if (target < 0) return null;
        const sequence = [b];
        const seen = new Set();
        while (target !== -1 && !seen.has(target)) {
            seen.add(target);
            sequence.push(walkNodeAt(target));
            target = results.previous.get(target) ?? -1;
        }
        sequence.push(a);
        sequence.reverse();
        return sequence;
    }

    function walkXYToLatLng(point) {
        return [
            ORIGIN_LAT + (point[1] * 180) / (Math.PI * EARTH_RADIUS_M),
            ORIGIN_LON + (point[0] * 180) / (Math.PI * EARTH_RADIUS_M * Math.cos(ORIGIN_LAT_RAD)),
        ];
    }

    function clearResult() {
        clearProgressiveRoutes();
        clearProgressiveStops();
        pendingCoverageDisks = [[], [], []];
        activeCoverageDisks = [[], [], []];
        for (let i = 0; i < 3; ++i) activeCoverageLines[i] = [];
        originWalkingLines = [];
        coverageState = null;
        clearCoverageOverlay();
        twoTransferRouteLayer.clearLayers();
        transferRouteLayer.clearLayers();
        directRouteLayer.clearLayers();
        stopLayer.clearLayers();
        originLayer.clearLayers();
        tripWalkLayer.clearLayers();
        tripRouteLayer.clearLayers();
        tripMarkerLayer.clearLayers();
        displayedStopIndexes.clear();
        cancelStopCanvasRedraw();
        clearStopPixels();
    }

    function routeLabel(route) {
        if (!route) {
            return '?';
        }
        const names = {
            bus: 'Bus',
            tram: 'Tram',
            trolleybus: 'Trolleybus',
            metro: 'Metro',
            mcc: 'MCC',
            mcd: 'MCD',
            train: 'Train',
        };
        const type = route.mode || route.route || '';
        const ref = route.ref || '?';
        return (names[type] || type) + ' ' + ref;
    }

    function drawOrigin(lat, lon, drawWalking = true) {
        const originWalkRadius = Number.isFinite(maxTravelMinutes)
            ? Math.min(walkRadius, Math.max(0, maxTravelMinutes) * WALK_SPEED_M_PER_MIN)
            : walkRadius;

        if (drawWalking && originWalkRadius > 0) {
            const context = walkContextForPoint({ lat, lon });
            const seeds = [];
            if (context.anchor) {
                for (const [node, cost] of walkSeeds(context.anchor)) {
                    if (cost <= originWalkRadius) {
                        seeds.push({ node, radius: originWalkRadius - cost });
                    }
                }
            }
            originWalkingLines = walkCoverageFromNodes(seeds);
        }

        L.circleMarker([lat, lon], {
            radius: 7,
            weight: 2,
            color: '#111',
            fillColor: '#fff',
            fillOpacity: 1,
            interactive: false,
            pane: 'originPane',
        }).addTo(originLayer);
    }

    function drawRoute(routeKey, transferCount, reachableSegments) {
        const route = routes[routeKey];

        if (!route || !reachableSegments) {
            return;
        }

        const color = routeColor(routeKey);
        const routeLayers = [directRouteLayer, transferRouteLayer, twoTransferRouteLayer];
        const targetLayer = routeLayers[transferCount];
        const weights = [4, 2.8, 2.2];
        const opacities = [0.85, 0.48, 0.34];
        const accessLabels = ['no transfers', 'one transfer', 'two transfers'];

        for (const segment of reachableSegments.values()) {
            const geometry = routeSegmentGeometry(segment);

            if (!geometry) {
                continue;
            }

            const direction = segment.direction;
            const feature = {
                type: 'Feature',
                properties: {},
                geometry: geometry,
            };

            const layer = L.geoJSON(feature, {
                pane: 'routePane',
                interactive: false,
                style: {
                    color: color,
                    weight: weights[transferCount],
                    opacity: opacities[transferCount],
                },
            });

            const from = stops[direction.stops[segment.startPos]];
            const to = stops[direction.stops[segment.endPos]];

            layer.bindPopup(
                '<b>' +
                    escapeHtml(routeLabel(route)) +
                    '</b><br>' +
                    escapeHtml(from ? from.name : '?') +
                    ' &rarr; ' +
                    escapeHtml(to ? to.name : '?') +
                    '<br>' +
                    accessLabels[transferCount],
            );

            layer.addTo(targetLayer);
        }
    }

    // Route drawing is incremental and independent of the walking wave.
    // Each confirmed traversed edge is retained once per transfer tier, so
    // a zoom/pan redraw does not require repeating the transport search.
    const routeEdgeRecords = [[], [], []];
    const routeEdgeKeys = [new Set(), new Set(), new Set()];
    const routeCanvasCursors = [0, 0, 0];
    let routePaintFrame = null;
    let routeViewFrame = null;
    let routeCanvasOrigin = L.point(0, 0);
    let routeCanvasBounds = null;
    let routeCanvasScale = 1;
    let routeCanvasZooming = false;
    let routePaintTier = 0;
    let routeFirstPaintMs = null;

    function positionRouteCanvases() {
        const size = map.getSize();
        const scale = Math.min(window.devicePixelRatio || 1, 1.5);
        const width = Math.max(1, Math.round(size.x * scale));
        const height = Math.max(1, Math.round(size.y * scale));
        routeCanvasOrigin = map.containerPointToLayerPoint([0, 0]);
        routeCanvasBounds = map.getBounds().pad(0.05);
        routeCanvasScale = scale;
        for (let tier = 0; tier < 3; ++tier) {
            const canvas = routeCanvases[tier];
            canvas.style.width = size.x + 'px';
            canvas.style.height = size.y + 'px';
            L.DomUtil.setPosition(canvas, routeCanvasOrigin);
            if (canvas.width !== width) canvas.width = width;
            if (canvas.height !== height) canvas.height = height;
            routeContexts[tier].setTransform(scale, 0, 0, scale, 0, 0);
            routeContexts[tier].lineWidth = [4, 2.8, 2.2][tier];
            routeContexts[tier].lineCap = 'round';
            routeContexts[tier].lineJoin = 'round';
        }
    }

    function clearRouteCanvasPixels() {
        for (let tier = 0; tier < 3; ++tier) {
            const canvas = routeCanvases[tier];
            const ctx = routeContexts[tier];
            ctx.save();
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.restore();
        }
    }

    function cancelRoutePaint() {
        if (routePaintFrame !== null) cancelAnimationFrame(routePaintFrame);
        if (routeViewFrame !== null) cancelAnimationFrame(routeViewFrame);
        routePaintFrame = null;
        routeViewFrame = null;
    }

    function clearProgressiveRoutes() {
        cancelRoutePaint();
        for (let tier = 0; tier < 3; ++tier) {
            routeEdgeRecords[tier].length = 0;
            routeEdgeKeys[tier].clear();
            routeCanvasCursors[tier] = 0;
        }
        routeFirstPaintMs = null;
        clearRouteCanvasPixels();
    }

    function queueProgressiveRouteEdge(routeKey, direction, pos, tier, token) {
        if (token !== renderGeneration || selectedRouteFocus) return;
        if (tier < 0 || tier > 2 || pos <= 0 || !direction) return;
        if (!direction.stops || pos >= direction.stops.length) return;
        const key = routeKey + ':' + relationKey(direction) + ':' + pos;
        if (routeEdgeKeys[tier].has(key)) return;
        routeEdgeKeys[tier].add(key);
        routeEdgeRecords[tier].push({ routeKey, direction, pos });
        scheduleRoutePaint();
    }

    function queueProgressiveCandidateLeg(candidate, tier, token) {
        const legs = candidateLegs(candidate);
        const leg = legs[legs.length - 1];
        if (!leg) return;
        for (const part of legParts(leg)) {
            for (let pos = part.startPos + 1; pos <= part.endPos; ++pos) {
                queueProgressiveRouteEdge(
                    part.routeKey || leg.routeKey,
                    part.direction,
                    pos,
                    tier,
                    token,
                );
            }
        }
    }

    function paintOneRouteEdge(item, tier) {
        const part = {
            direction: item.direction,
            startPos: item.pos - 1,
            endPos: item.pos,
        };
        const geometry =
            routeSinglePartGeometry(part) ||
            stopChainGeometry(item.direction, part.startPos, part.endPos);
        if (!geometry) return;
        const ctx = routeContexts[tier];
        ctx.strokeStyle = routeColor(item.routeKey);
        ctx.beginPath();
        let visible = false;
        for (const line of geometryLines(geometry)) {
            if (line.length < 2) continue;
            // Bounding boxes retain lines crossing the viewport even if both
            // endpoints are offscreen; all coordinates remain OSM-derived.
            const box = routeCanvasBounds;
            const west = box.getWest(),
                east = box.getEast();
            const south = box.getSouth(),
                north = box.getNorth();
            let minLon = Infinity,
                maxLon = -Infinity;
            let minLat = Infinity,
                maxLat = -Infinity;
            for (const coord of line) {
                minLon = Math.min(minLon, coord[0]);
                maxLon = Math.max(maxLon, coord[0]);
                minLat = Math.min(minLat, coord[1]);
                maxLat = Math.max(maxLat, coord[1]);
            }
            if (maxLon < west || minLon > east || maxLat < south || minLat > north) continue;
            let first = true;
            for (const coord of line) {
                const pt = map.latLngToLayerPoint([coord[1], coord[0]]);
                const x = pt.x - routeCanvasOrigin.x;
                const y = pt.y - routeCanvasOrigin.y;
                if (first) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
                first = false;
            }
            visible = true;
        }
        if (visible) ctx.stroke();
    }

    function paintProgressiveRouteFrame() {
        const frameStart = performance.now();
        routePaintFrame = null;
        if (routeCanvasZooming) return;
        const deadline = performance.now() + 3;
        let painted = 0;
        let attempted = 0;
        // Rotate the tiers so outer routes cannot starve direct routes.
        while (attempted < 64 && performance.now() < deadline) {
            const tier = routePaintTier;
            routePaintTier = (routePaintTier + 1) % 3;
            if (routeCanvasCursors[tier] >= routeEdgeRecords[tier].length) {
                if (++attempted >= 3) break;
                continue;
            }
            attempted = 0;
            const item = routeEdgeRecords[tier][routeCanvasCursors[tier]++];
            paintOneRouteEdge(item, tier);
            painted++;
        }
        if (painted && routeFirstPaintMs === null) {
            routeFirstPaintMs = performance.now();
        }
        scheduleRoutePaint();
        profileMarkPaint('routePaint', performance.now() - frameStart, painted);
    }

    function scheduleRoutePaint() {
        if (routeCanvasZooming || routePaintFrame !== null) return;
        if (!routeEdgeRecords.some((items, tier) => routeCanvasCursors[tier] < items.length))
            return;
        routePaintFrame = requestAnimationFrame(paintProgressiveRouteFrame);
    }

    function redrawProgressiveRoutes() {
        if (routeCanvasZooming) return;
        cancelRoutePaint();
        positionRouteCanvases();
        clearRouteCanvasPixels();
        routeCanvasCursors.fill(0);
        scheduleRoutePaint();
    }

    function scheduleRouteViewportRedraw() {
        if (routeCanvasZooming || routeViewFrame !== null) return;
        routeViewFrame = requestAnimationFrame(() => {
            routeViewFrame = null;
            redrawProgressiveRoutes();
        });
    }

    function suspendRoutesForZoom() {
        routeCanvasZooming = true;
        cancelRoutePaint();
        for (const canvas of routeCanvases) canvas.style.visibility = 'hidden';
        clearRouteCanvasPixels();
    }

    function resumeRoutesAfterZoom() {
        routeCanvasZooming = false;
        redrawProgressiveRoutes();
        for (const canvas of routeCanvases) canvas.style.visibility = 'visible';
    }

    function coverageRadiusForArrival(arrivalMinutes) {
        if (!Number.isFinite(maxTravelMinutes)) {
            return walkRadius;
        }

        if (!Number.isFinite(arrivalMinutes)) {
            return 0;
        }

        const remainingMinutes = Math.max(0, maxTravelMinutes - arrivalMinutes);

        return Math.min(walkRadius, remainingMinutes * WALK_SPEED_M_PER_MIN);
    }

    let pendingCoverageDisks = [[], [], []];
    let activeCoverageDisks = [[], [], []];

    function drawCoverage(stop, transferCount, arrivalMinutes) {
        const radius = coverageRadiusForArrival(arrivalMinutes);

        if (radius <= 0) {
            return;
        }

        pendingCoverageDisks[transferCount].push({
            stop: stop,
            radius: radius,
        });
    }

    // Walking reachability is rendered along navigable OSM edges, not circles.
    const activeCoverageLines = [[], [], []];
    let originWalkingLines = [];
    // Painting never owns the search: its cursor may lag behind Dijkstra safely.
    // Canvases preserve the 2/1/0/origin stacking even when sources arrive later.
    const coverageColors = ['#3388ff', '#ff9800', '#7c3aed', '#dc2626'];
    const coverageWidths = [10, 10, 10, 7];
    const coverageCursor = [0, 0, 0, 0];
    // The wave stores per-vertex best budgets. Canvas queues only visible edge IDs.
    let coverageState = null;
    const coverageViewEdges = [[], [], [], []];
    let coverageViewIterator = null;
    let coverageBounds = null;
    let coverageWorldSize = 1;
    let coverageWorldOffsetX = 0;
    let coverageWorldOffsetY = 0;
    const coveragePixelX = new Float32Array(walkNodes.length);
    const coveragePixelY = new Float32Array(walkNodes.length);
    const coveragePixelStamp = new Uint32Array(walkNodes.length);
    let coveragePixelGeneration = 0;
    let coverageLastComposite = 0;
    // 2 transfers -> 1 transfer -> direct -> walk from origin (highest).
    const coverageOrder = [2, 1, 0, 3];
    // Composite opaque tier masks at most a few times per second.
    // A final composite happens as soon as all masks have been painted.
    let coverageMasksDirty = false;
    let coverageCompositeCount = 0;
    // At small zoom levels many OSM edges project onto the same screen pixel.
    // Each tier draws a tiny screen cell only once, without changing routing.
    let coverageTinyStep = 0;
    let coverageTinyColumns = 0;
    let coverageTinyRows = 0;
    let coverageTinySeen = [null, null, null, null];
    let coverageSize = L.point(0, 0);
    let coverageScale = 1;
    let coveragePaintFrame = null;
    let coveragePaintRotation = 0;
    let coverageTopLeft = L.point(0, 0);
    let coverageZooming = false;
    let coverageZoomInProgress = false;
    let coverageViewFrame = null;
    let coverageWheelTimer = null;

    function coverageLinesForTier(tier) {
        if (coverageState) return coverageViewEdges[tier];
        return tier === 3 ? originWalkingLines : activeCoverageLines[tier];
    }

    function coverageEdgeInViewAB(a, b) {
        const bounds = coverageBounds;
        const ax = walkNodeXY[2 * a] / 10,
            ay = walkNodeXY[2 * a + 1] / 10;
        const bx = walkNodeXY[2 * b] / 10,
            by = walkNodeXY[2 * b + 1] / 10;
        return !(
            Math.max(ax, bx) < bounds[0] ||
            Math.min(ax, bx) > bounds[2] ||
            Math.max(ay, by) < bounds[1] ||
            Math.min(ay, by) > bounds[3]
        );
    }

    function coveragePixel(node) {
        if (coveragePixelStamp[node] !== coveragePixelGeneration) {
            const lon =
                ORIGIN_LON +
                ((walkNodeXY[2 * node] / 10) * 180) /
                    (Math.PI * EARTH_RADIUS_M * Math.cos(ORIGIN_LAT_RAD));
            const lat =
                ORIGIN_LAT + ((walkNodeXY[2 * node + 1] / 10) * 180) / (Math.PI * EARTH_RADIUS_M);
            const phi = (lat * Math.PI) / 180;
            coveragePixelX[node] = (coverageWorldSize * (lon + 180)) / 360 - coverageWorldOffsetX;
            coveragePixelY[node] =
                (coverageWorldSize * (1 - Math.log(Math.tan(phi) + 1 / Math.cos(phi)) / Math.PI)) /
                    2 -
                coverageWorldOffsetY;
            coveragePixelStamp[node] = coveragePixelGeneration;
        }
    }

    // Typed heap for the large multi-source pedestrian wave. No per-push objects.
    function makeCoverageHeap() {
        let capacity = 4096;
        let keys = new Float64Array(capacity);
        let nodes = new Uint32Array(capacity);
        const result = [0, 0];
        let length = 0;
        return {
            get size() {
                return length;
            },
            push(key, node) {
                if (length === capacity) {
                    capacity *= 2;
                    const nextKeys = new Float64Array(capacity);
                    const nextNodes = new Uint32Array(capacity);
                    nextKeys.set(keys);
                    nextNodes.set(nodes);
                    keys = nextKeys;
                    nodes = nextNodes;
                }
                let i = length++;
                while (i > 0) {
                    const parent = (i - 1) >>> 1;
                    if (keys[parent] <= key) break;
                    keys[i] = keys[parent];
                    nodes[i] = nodes[parent];
                    i = parent;
                }
                keys[i] = key;
                nodes[i] = node;
            },
            pop() {
                result[0] = keys[0];
                result[1] = nodes[0];
                const key = keys[--length];
                const node = nodes[length];
                if (length > 0) {
                    let i = 0;
                    while (i * 2 + 1 < length) {
                        let child = i * 2 + 1;
                        if (child + 1 < length && keys[child + 1] < keys[child]) {
                            child++;
                        }
                        if (key <= keys[child]) break;
                        keys[i] = keys[child];
                        nodes[i] = nodes[child];
                        i = child;
                    }
                    keys[i] = key;
                    nodes[i] = node;
                }
                return result;
            },
        };
    }

    // Reconstruct a viewport from settled wave state, never from all-city strokes.
    function* rebuildCoverageViewSteps() {
        if (!coverageState || !walkSegmentGrid) return;
        const seen = new Uint8Array(walkEdges.length);
        const x0 = Math.floor(coverageBounds[0] / WALK_GRID_METERS);
        const x1 = Math.floor(coverageBounds[2] / WALK_GRID_METERS);
        const y0 = Math.floor(coverageBounds[1] / WALK_GRID_METERS);
        const y1 = Math.floor(coverageBounds[3] / WALK_GRID_METERS);
        let checked = 0;
        for (let y = y0; y <= y1; ++y) {
            for (let x = x0; x <= x1; ++x) {
                const ids = walkSegmentGrid.get(x + ':' + y);
                if (!ids) continue;
                for (const id of ids) {
                    if (seen[id]) continue;
                    seen[id] = 1;
                    const direction = walkEdgeDirs[id];
                    if (direction === 5 || direction >= 10) continue;
                    const a = walkEdgeAB[2 * id],
                        b = walkEdgeAB[2 * id + 1];
                    if (!coverageEdgeInViewAB(a, b)) continue;
                    for (let tier = 0; tier < 4; ++tier) {
                        const reached = coverageState.settled[tier];
                        if (reached[a] || reached[b]) {
                            coverageViewEdges[tier].push(id);
                        }
                    }
                    if ((++checked & 2047) === 0) yield;
                }
            }
            if ((y & 7) === 0) yield;
        }
    }

    function positionCoverageOverlay() {
        const topLeft = map.containerPointToLayerPoint([0, 0]);
        const size = map.getSize();
        const scale = Math.min(window.devicePixelRatio || 1, 1.5);
        const width = Math.max(1, Math.round(size.x * scale));
        const height = Math.max(1, Math.round(size.y * scale));
        coverageTopLeft = topLeft;
        coverageSize = size;
        coverageScale = scale;
        const bounds = map.getBounds().pad(0.04);
        const sw = project(bounds.getWest(), bounds.getSouth());
        const ne = project(bounds.getEast(), bounds.getNorth());
        coverageBounds = [sw[0], sw[1], ne[0], ne[1]];
        coverageWorldSize = 256 * Math.pow(2, map.getZoom());
        const origin = map.getPixelOrigin();
        coverageWorldOffsetX = origin.x + topLeft.x;
        coverageWorldOffsetY = origin.y + topLeft.y;
        coveragePixelGeneration++;
        if (coveragePixelGeneration === 0xffffffff) {
            coveragePixelStamp.fill(0);
            coveragePixelGeneration = 1;
        }
        L.DomUtil.setPosition(coverageCanvas, topLeft);
        coverageCanvas.style.width = size.x + 'px';
        coverageCanvas.style.height = size.y + 'px';
        coverageCanvas.style.opacity = String(circleOpacity);
        if (coverageCanvas.width !== width || coverageCanvas.height !== height) {
            coverageCanvas.width = width;
            coverageCanvas.height = height;
        }
        coverageContext.setTransform(scale, 0, 0, scale, 0, 0);
        for (let tier = 0; tier < 4; ++tier) {
            const canvas = coverageCanvases[tier];
            const ctx = coverageContexts[tier];
            if (canvas.width !== width || canvas.height !== height) {
                canvas.width = width;
                canvas.height = height;
            }
            ctx.setTransform(scale, 0, 0, scale, 0, 0);
            ctx.lineWidth = coverageWidths[tier];
            ctx.strokeStyle = coverageColors[tier];
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
        }
    }

    function cancelCoveragePaint() {
        if (coveragePaintFrame !== null) {
            cancelAnimationFrame(coveragePaintFrame);
            coveragePaintFrame = null;
        }
    }

    function clearCoveragePixels() {
        coverageMasksDirty = false;
        coverageTinyStep = map.getZoom() <= 12 ? 2 : 0;
        if (coverageTinyStep) {
            coverageTinyColumns = Math.ceil(coverageSize.x / coverageTinyStep);
            coverageTinyRows = Math.ceil(coverageSize.y / coverageTinyStep);
            const count = coverageTinyColumns * coverageTinyRows;
            coverageTinySeen = Array.from({ length: 4 }, () => new Uint8Array(count));
        } else {
            coverageTinySeen = [null, null, null, null];
        }
        for (const [canvas, ctx] of [
            [coverageCanvas, coverageContext],
            ...coverageCanvases.map((canvas, tier) => [canvas, coverageContexts[tier]]),
        ]) {
            ctx.save();
            ctx.setTransform(1, 0, 0, 1, 0, 0);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.restore();
        }
    }

    function clearCoverageOverlay() {
        cancelCoveragePaint();
        coverageViewIterator = null;
        coverageCursor.fill(0);
        for (const entries of coverageViewEdges) entries.length = 0;
        clearCoveragePixels();
    }

    function hasUnpaintedCoverage() {
        return (
            Boolean(coverageViewIterator) ||
            coverageMasksDirty ||
            coverageCursor.some((position, tier) => position < coverageLinesForTier(tier).length)
        );
    }

    // Opaque masks guarantee that pixels never mix transfer-tier colors.
    // Compositing the full viewport once per ~150 ms is cheaper than marking
    // millions of dirty tiles for every overlapping street segment.
    function compositeCoverageMasks() {
        if (!coverageMasksDirty) return 0;
        const ctx = coverageContext;
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, coverageCanvas.width, coverageCanvas.height);
        for (const tier of coverageOrder) {
            ctx.drawImage(coverageCanvases[tier], 0, 0);
        }
        ctx.restore();
        coverageMasksDirty = false;
        coverageCompositeCount++;
        return 1;
    }

    // One stroke for a bounded batch: no one-path-per-edge DOM allocations.
    function drawCoverageBatch(tier, deadline) {
        if (!coverageState) return drawLegacyCoverageBatch(tier, deadline);
        const ids = coverageViewEdges[tier];
        const ctx = coverageContexts[tier];
        const budgets = coverageState.remaining[tier];
        const settled = coverageState.settled[tier];
        let position = coverageCursor[tier];
        let scanned = 0;
        let tinySkipped = 0;
        ctx.beginPath();
        while (position < ids.length && scanned < 65536) {
            if ((scanned & 255) === 0 && performance.now() >= deadline) break;
            const id = ids[position++];
            scanned++;
            const length = walkEdgeLengths[id],
                direction = walkEdgeDirs[id];
            if (length <= 0 || direction === 5 || direction >= 10) continue;
            const a = walkEdgeAB[2 * id],
                b = walkEdgeAB[2 * id + 1];
            if (!coverageEdgeInViewAB(a, b)) continue;
            const left =
                direction === 3 || !settled[a] ? 0 : Math.min(1, Math.max(0, budgets[a] / length));
            const right =
                direction === 2 || !settled[b] ? 0 : Math.min(1, Math.max(0, budgets[b] / length));
            if (left <= 0 && right <= 0) continue;
            coveragePixel(a);
            coveragePixel(b);
            const px = coveragePixelX[a],
                py = coveragePixelY[a];
            const dx = coveragePixelX[b] - px;
            const dy = coveragePixelY[b] - py;
            const full = left + right >= 1;
            for (let part = 0; part < (full ? 1 : 2); ++part) {
                const from = full ? 0 : part ? 1 - right : 0;
                const to = full ? 1 : part ? 1 : left;
                if (to <= from) continue;
                const x0 = px + dx * from,
                    y0 = py + dy * from;
                const x1 = px + dx * to,
                    y1 = py + dy * to;
                if (
                    coverageTinyStep &&
                    Math.abs(x1 - x0) <= coverageTinyStep &&
                    Math.abs(y1 - y0) <= coverageTinyStep
                ) {
                    const cx = Math.floor((x0 + x1) / 2 / coverageTinyStep);
                    const cy = Math.floor((y0 + y1) / 2 / coverageTinyStep);
                    if (cx >= 0 && cy >= 0 && cx < coverageTinyColumns && cy < coverageTinyRows) {
                        const key = cy * coverageTinyColumns + cx;
                        if (coverageTinySeen[tier][key]) {
                            tinySkipped++;
                            continue;
                        }
                        coverageTinySeen[tier][key] = 1;
                    }
                }
                ctx.moveTo(x0, y0);
                ctx.lineTo(x1, y1);
            }
        }
        if (scanned) {
            ctx.stroke();
            coverageMasksDirty = true;
        }
        coverageCursor[tier] = position;
        if (runProfile && !runProfile.finished) {
            runProfile.frames.coverageTinySkipped =
                (runProfile.frames.coverageTinySkipped || 0) + tinySkipped;
        }
        return scanned;
    }

    // Used only by explicit non-streaming coverage outside the reachability wave.
    function drawLegacyCoverageBatch(tier, deadline) {
        const lines = coverageLinesForTier(tier);
        const ctx = coverageContexts[tier];
        const bounds = map.getBounds();
        const lower = project(bounds.getWest(), bounds.getSouth());
        const upper = project(bounds.getEast(), bounds.getNorth());
        const pixels = new Map();
        const topLeft = coverageTopLeft;
        const pixel = (index) => {
            let value = pixels.get(index);
            if (!value) {
                value = map.latLngToLayerPoint(walkXYToLatLng(walkNodeAt(index)));
                pixels.set(index, value);
            }
            return value;
        };
        let position = coverageCursor[tier];
        let scanned = 0;
        ctx.beginPath();
        while (position < lines.length && scanned < 128) {
            if ((scanned & 31) === 0 && performance.now() >= deadline) break;
            const [a, b, t0, t1] = lines[position++];
            scanned++;
            const from = walkNodeAt(a);
            const to = walkNodeAt(b);
            if (
                Math.max(from[0], to[0]) < lower[0] ||
                Math.min(from[0], to[0]) > upper[0] ||
                Math.max(from[1], to[1]) < lower[1] ||
                Math.min(from[1], to[1]) > upper[1]
            )
                continue;
            const p = pixel(a);
            const q = pixel(b);
            const dx = q.x - p.x;
            const dy = q.y - p.y;
            const x0 = p.x + dx * t0 - topLeft.x;
            const y0 = p.y + dy * t0 - topLeft.y;
            const x1 = p.x + dx * t1 - topLeft.x;
            const y1 = p.y + dy * t1 - topLeft.y;
            ctx.moveTo(x0, y0);
            ctx.lineTo(x1, y1);
        }
        if (scanned) {
            ctx.stroke();
            coverageMasksDirty = true;
        }
        coverageCursor[tier] = position;
        return scanned;
    }

    function paintCoverageFrame() {
        const frameStart = performance.now();
        coveragePaintFrame = null;
        // One long batch per animation frame instead of 1024 edges per tier.
        // The deadline, not an arbitrary low item count, limits main-thread work.
        const deadline = frameStart + 11;
        if (coverageViewIterator) {
            while (performance.now() < deadline) {
                if (coverageViewIterator.next().done) {
                    coverageViewIterator = null;
                    profilePhaseEnd(
                        'Coverage viewport index',
                        'tier counts: ' + coverageViewEdges.map((items) => items.length).join(','),
                    );
                    break;
                }
            }
        }
        let lines = 0;
        if (!coverageViewIterator) {
            while (performance.now() < deadline) {
                let nextTier = -1;
                for (let i = 0; i < 4; ++i) {
                    const tier = coverageOrder[coveragePaintRotation];
                    coveragePaintRotation = (coveragePaintRotation + 1) % 4;
                    if (coverageCursor[tier] < coverageLinesForTier(tier).length) {
                        nextTier = tier;
                        break;
                    }
                }
                if (nextTier < 0) break;
                if (
                    runProfile &&
                    !runProfile.finished &&
                    runProfile.active['Coverage Canvas painting'] === undefined
                ) {
                    profilePhaseStart('Coverage Canvas painting');
                }
                lines += drawCoverageBatch(nextTier, deadline);
            }
        }
        const unfinished =
            Boolean(coverageViewIterator) ||
            coverageCursor.some((position, tier) => position < coverageLinesForTier(tier).length);
        const compositeStart = performance.now();
        // Intermediate preview is throttled; the last composite is immediate.
        const shouldComposite =
            coverageMasksDirty && (!unfinished || performance.now() - coverageLastComposite >= 150);
        const composites = shouldComposite ? compositeCoverageMasks() : 0;
        if (composites) coverageLastComposite = performance.now();
        if (runProfile && !runProfile.finished) {
            runProfile.cpu.coverageComposite =
                (runProfile.cpu.coverageComposite || 0) + performance.now() - compositeStart;
            runProfile.frames.coverageTiles = (runProfile.frames.coverageTiles || 0) + composites;
        }
        if (!hasUnpaintedCoverage()) {
            profilePhaseEnd('Coverage Canvas painting', 'fully composited');
        } else {
            scheduleCoveragePaint();
        }
        profileMarkPaint('coveragePaint', performance.now() - frameStart, lines);
    }

    function scheduleCoveragePaint() {
        if (coverageZooming || coveragePaintFrame !== null) return;
        if (!hasUnpaintedCoverage()) return;
        coveragePaintFrame = requestAnimationFrame(paintCoverageFrame);
    }

    function redrawCoverageOverlay() {
        if (coverageZooming) return;
        cancelCoveragePaint();
        positionCoverageOverlay();
        coverageCursor.fill(0);
        for (const entries of coverageViewEdges) entries.length = 0;
        coverageViewIterator = coverageState ? rebuildCoverageViewSteps() : null;
        if (coverageViewIterator && runProfile && !runProfile.finished) {
            profilePhaseStart('Coverage viewport index');
        }
        clearCoveragePixels();
        scheduleCoveragePaint();
    }

    function suspendCoverageForZoom() {
        if (coverageZooming) return;
        coverageZooming = true;
        if (coverageViewFrame !== null) {
            cancelAnimationFrame(coverageViewFrame);
            coverageViewFrame = null;
        }
        cancelCoveragePaint();
        coverageViewIterator = null;
        coverageCursor.fill(0);
        for (const entries of coverageViewEdges) entries.length = 0;
        coverageCanvas.style.visibility = 'hidden';
        clearCoveragePixels();
    }

    // Coalesce zoomend and moveend: redraw from the stored graph only once.
    function requestCoverageViewportRedraw() {
        if (coverageZooming || coverageViewFrame !== null) return;
        coverageViewFrame = requestAnimationFrame(() => {
            coverageViewFrame = null;
            if (coverageZooming) return;
            redrawCoverageOverlay();
            coverageCanvas.style.visibility = 'visible';
        });
    }

    function resumeCoverageAfterZoom() {
        coverageZooming = false;
        requestCoverageViewportRedraw();
    }

    // Leaflet debounces wheel zoom. Hide pixels at wheel time, not moveend.
    function onCoverageWheel(event) {
        if (!map.scrollWheelZoom.enabled() || !event.deltaY) return;
        if (coverageZoomInProgress) return;
        const zoom = map.getZoom();
        if (event.deltaY > 0 && zoom <= map.getMinZoom()) return;
        if (event.deltaY < 0 && zoom >= map.getMaxZoom()) return;
        suspendCoverageForZoom();
        if (coverageWheelTimer !== null) clearTimeout(coverageWheelTimer);
        coverageWheelTimer = setTimeout(() => {
            coverageWheelTimer = null;
            resumeCoverageAfterZoom();
        }, 250);
    }

    function onCoverageZoomStart() {
        coverageZoomInProgress = true;
        if (coverageWheelTimer !== null) {
            clearTimeout(coverageWheelTimer);
            coverageWheelTimer = null;
        }
        suspendCoverageForZoom();
    }

    function onCoverageZoomEnd() {
        coverageZoomInProgress = false;
        resumeCoverageAfterZoom();
    }

    positionCoverageOverlay();

    function flushCoverage() {
        activeCoverageDisks = pendingCoverageDisks.map((disks) => disks.slice());
        for (let tier = 0; tier < 3; ++tier) {
            activeCoverageLines[tier] = walkCoverageSegments(activeCoverageDisks[tier]);
        }
        redrawCoverageOverlay();
    }

    // The OSM edge-to-vertex reverse index is no longer needed here.
    // The wave settles vertices; the viewport index enumerates edges once later.
    function startProgressiveCoverage(token, streaming = false) {
        const count = walkNodes.length;
        const heap = makeCoverageHeap();
        const remaining = Array.from({ length: 4 }, () => {
            const array = new Float32Array(count);
            array.fill(-1);
            return array;
        });
        const settled = Array.from({ length: 4 }, () => new Uint8Array(count));
        const sources = Array.from({ length: 3 }, () => new Map());
        const waveStats = {
            sources: 0,
            popped: 0,
            settled: 0,
            cpuMs: 0,
            yields: 0,
            peakHeap: 0,
        };
        const state = { remaining, settled };
        coverageState = state;
        let started = false;
        let running = false;
        let scheduled = false;

        function seed(tier, node, budget) {
            if (!Number.isInteger(node) || node < 0 || budget <= 0) return;
            if (budget <= remaining[tier][node] + 0.0001) return;
            remaining[tier][node] = budget;
            heap.push(-budget, tier * count + node);
        }

        function begin() {
            if (started || token !== renderGeneration) return;
            started = true;
            profilePhaseStart('Walking coverage wave');
            const originRadius = Number.isFinite(maxTravelMinutes)
                ? Math.min(walkRadius, maxTravelMinutes * WALK_SPEED_M_PER_MIN)
                : walkRadius;
            if (originRadius > 0) {
                const xy = project(selectedPoint.lon, selectedPoint.lat);
                const anchor = snapWalkXY(xy[0], xy[1]);
                if (anchor) {
                    for (const [node, distance] of walkSeeds(anchor)) {
                        seed(3, node, originRadius - distance);
                    }
                }
            }
            for (let tier = 0; tier < 3; ++tier) {
                for (const [node, radius] of sources[tier]) {
                    seed(tier, node, radius);
                }
                sources[tier].clear();
            }
            start();
            if (!heap.size) profilePhaseEnd('Walking coverage wave', 'no sources');
        }

        async function expand() {
            running = true;
            while (heap.size && token === renderGeneration) {
                const startTime = performance.now();
                let processed = 0;
                while (heap.size && performance.now() - startTime < 8) {
                    const [negative, packed] = heap.pop();
                    const tier = Math.floor(packed / count);
                    const node = packed % count;
                    const budget = -negative;
                    waveStats.popped++;
                    processed++;
                    if (settled[tier][node] || budget + 0.001 < remaining[tier][node]) continue;
                    settled[tier][node] = 1;
                    waveStats.settled++;
                    // Search settles only vertices. After completion, enumerate
                    // each visible OSM edge ONCE and paint its final reachability.
                    for (let i = walkOffsets[node]; i < walkOffsets[node + 1]; ++i) {
                        seed(tier, walkTo[i], budget - walkWeight[i]);
                    }
                }
                waveStats.cpuMs += performance.now() - startTime;
                waveStats.peakHeap = Math.max(waveStats.peakHeap, heap.size);
                scheduleCoveragePaint();
                if (heap.size) {
                    waveStats.yields++;
                    await eventLoopYield();
                }
            }
            running = false;
            if (token === renderGeneration) {
                const profiled = runProfile && runProfile.token === token;
                if (profiled) {
                    profilePhaseEnd('Walking coverage wave', 'settled: ' + waveStats.settled);
                }
                // Also support the non-profiled/non-streaming rendering path.
                redrawCoverageOverlay();
                if (profiled) profileFinish(runProfile);
            }
        }

        function start() {
            if (!started || running || scheduled || token !== renderGeneration || !heap.size)
                return;
            scheduled = true;
            setTimeout(() => {
                scheduled = false;
                if (!running && token === renderGeneration && heap.size) {
                    void expand();
                }
            }, 0);
        }
        if (!streaming) {
            for (let tier = 0; tier < 3; ++tier) {
                for (const source of pendingCoverageDisks[tier]) {
                    const node = source.stop.walk_node;
                    if (node < 0 || source.radius <= 0) continue;
                    sources[tier].set(node, Math.max(sources[tier].get(node) || -1, source.radius));
                }
            }
            begin();
        }
        return {
            idle() {
                return started && !running && !scheduled && heap.size === 0;
            },
            stats() {
                return { ...waveStats, heapSize: heap.size, running, started };
            },
            begin,
            addSource(tier, stopIndex, arrivalMinutes) {
                if (token !== renderGeneration || tier < 0 || tier > 2) return;
                const stop = stops[stopIndex];
                if (!stop || stop.walk_node < 0) return;
                const radius = coverageRadiusForArrival(arrivalMinutes);
                if (radius <= 0) return;
                waveStats.sources++;
                const node = stop.walk_node;
                if (started) {
                    // A recalculation must start with all sources together.
                    // Normally this branch is unreachable for current RAPTOR runs.
                    console.warn('Late walking source; recompute reachability');
                    return;
                }
                if (radius > (sources[tier].get(node) || -1)) {
                    sources[tier].set(node, radius);
                }
            },
        };
    }

    function stopRouteDetails(stopIndex) {
        const stop = stops[stopIndex];
        const rows = [];
        const ordered = Array.from(stop.routes).sort((a, b) =>
            routeLabel(routes[a]).localeCompare(routeLabel(routes[b]), undefined, {
                numeric: true,
            }),
        );

        for (const routeKey of ordered) {
            const route = routes[routeKey];

            if (!route || !isRouteEnabled(routeKey)) {
                continue;
            }

            const directions = [];

            for (const direction of route.directions) {
                if (!direction.stops.includes(stopIndex)) {
                    continue;
                }

                directions.push(
                    escapeHtml(direction.from || '?') +
                        ' &rarr; ' +
                        escapeHtml(direction.to || '?'),
                );
            }

            rows.push(
                '<div><b>' +
                    escapeHtml(routeLabel(route)) +
                    '</b>' +
                    (directions.length ? ': ' + directions.join(' / ') : '') +
                    '</div>',
            );
        }

        return rows.join('');
    }

    // A single viewport-sized Canvas replaces thousands of Leaflet circleMarkers.
    // All hit testing and popups are handled by the existing map events.
    const stopOutlineColors = ['#1565c0', '#ef6c00', '#6d28d9'];
    const stopFillColors = ['#42a5f5', '#ffb74d', '#a78bfa'];

    function stopMarkerRadiusForZoom() {
        const zoom = map.getZoom();
        if (zoom <= 10) return 1.4;
        if (zoom <= 11) return 1.7;
        if (zoom <= 12) return 2.0;
        if (zoom <= 13) return 2.4;
        if (zoom <= 15) return 2.8;
        return 3.5;
    }

    function positionStopCanvas() {
        const size = map.getSize();
        const scale = Math.min(window.devicePixelRatio || 1, 1.5);
        stopCanvasOrigin = map.containerPointToLayerPoint([0, 0]);
        stopCanvasScale = scale;
        stopCanvasBounds = map.getBounds().pad(0.02);
        L.DomUtil.setPosition(stopCanvas, stopCanvasOrigin);
        stopCanvas.style.width = size.x + 'px';
        stopCanvas.style.height = size.y + 'px';
        const width = Math.max(1, Math.round(size.x * scale));
        const height = Math.max(1, Math.round(size.y * scale));
        if (stopCanvas.width !== width) stopCanvas.width = width;
        if (stopCanvas.height !== height) stopCanvas.height = height;
        stopContext.setTransform(scale, 0, 0, scale, 0, 0);
    }

    function clearStopPixels() {
        stopContext.clearRect(
            0,
            0,
            stopCanvas.width / stopCanvasScale,
            stopCanvas.height / stopCanvasScale,
        );
        visibleStopIndexes.clear();
        stopHitGrid.clear();
    }

    function paintStopDot(stopIndex, tier) {
        const stop = stops[stopIndex];
        if (!stop || !stopCanvasBounds.contains([stop.lat, stop.lon])) {
            return;
        }
        const point = map.latLngToLayerPoint([stop.lat, stop.lon]);
        const x = point.x - stopCanvasOrigin.x;
        const y = point.y - stopCanvasOrigin.y;
        stopContext.beginPath();
        stopContext.arc(x, y, stopMarkerRadiusForZoom(), 0, Math.PI * 2);
        stopContext.fillStyle = stopFillColors[tier];
        stopContext.strokeStyle = stopOutlineColors[tier];
        stopContext.lineWidth = 0.7;
        stopContext.fill();
        stopContext.stroke();
        if (!visibleStopIndexes.has(stopIndex)) {
            const key = Math.floor(x / STOP_HIT_CELL) + ':' + Math.floor(y / STOP_HIT_CELL);
            const list = stopHitGrid.get(key) || [];
            list.push(stopIndex);
            stopHitGrid.set(key, list);
            visibleStopIndexes.add(stopIndex);
        }
    }

    function cancelStopCanvasRedraw() {
        if (stopCanvasRedrawFrame !== null) {
            cancelAnimationFrame(stopCanvasRedrawFrame);
            stopCanvasRedrawFrame = null;
        }
        if (stopCanvasRepaintTimer !== null) {
            clearTimeout(stopCanvasRepaintTimer);
            stopCanvasRepaintTimer = null;
        }
        stopCanvasRedrawItems = [];
        stopCanvasRedrawIndex = 0;
    }

    function paintStopCanvasFrame() {
        const frameStart = performance.now();
        stopCanvasRedrawFrame = null;
        if (stopCanvasZooming) return;
        const start = performance.now();
        let count = 0;
        while (
            stopCanvasRedrawIndex < stopCanvasRedrawItems.length &&
            count < 100 &&
            performance.now() - start < 3
        ) {
            const [stopIndex, tier] = stopCanvasRedrawItems[stopCanvasRedrawIndex++];
            // A better route may have been discovered while the frame was pending.
            paintStopDot(stopIndex, renderedStopTiers.get(stopIndex) ?? tier);
            count++;
        }
        if (stopCanvasRedrawIndex < stopCanvasRedrawItems.length) {
            stopCanvasRedrawFrame = requestAnimationFrame(paintStopCanvasFrame);
        } else {
            stopCanvasRedrawItems = [];
            stopCanvasRedrawIndex = 0;
        }
        profileMarkPaint('stopRedraw', performance.now() - frameStart, count);
    }

    function redrawStopCanvas() {
        cancelStopCanvasRedraw();
        if (stopCanvasZooming) return;
        positionStopCanvas();
        clearStopPixels();
        const bounds = map.getBounds().pad(0.02);
        stopCanvasRedrawItems = Array.from(renderedStopTiers).filter(([index]) =>
            bounds.contains([stops[index].lat, stops[index].lon]),
        );
        stopCanvasRedrawIndex = 0;
        if (stopCanvasRedrawItems.length) {
            stopCanvasRedrawFrame = requestAnimationFrame(paintStopCanvasFrame);
        }
    }

    function scheduleStopCanvasRepaint() {
        if (stopCanvasRepaintTimer !== null || stopCanvasZooming) return;
        stopCanvasRepaintTimer = setTimeout(() => {
            stopCanvasRepaintTimer = null;
            redrawStopCanvas();
        }, 120);
    }

    function updateStopMarkerSizes() {
        redrawStopCanvas();
    }

    function stopPopupContent(stopIndex, transferCount) {
        const stop = stops[stopIndex];
        const access = [TEXT.accessDirect, TEXT.accessTransfer, TEXT.accessTwoTransfers][
            transferCount
        ];
        return (
            '<b>' +
            escapeHtml(stop.name || TEXT.stop) +
            '</b><br>' +
            access +
            '<br><br>' +
            stopRouteDetails(stopIndex) +
            '<br><span style="color:#666">' +
            TEXT.doubleClick +
            '</span>'
        );
    }

    function updateStopMarkerTier(stopIndex, transferCount) {
        if (!renderedStopTiers.has(stopIndex)) {
            drawStop(stopIndex, transferCount);
            return;
        }
        // Clearing a single dot may erase neighbors. Repaint in bounded frames.
        scheduleStopCanvasRepaint();
    }

    function drawStop(stopIndex, transferCount) {
        displayedStopIndexes.add(stopIndex);
        renderedStopTiers.set(stopIndex, transferCount);
        if (!stopCanvasZooming) paintStopDot(stopIndex, transferCount);
    }

    positionStopCanvas();

    // Marker creation must not wait for all route-search rounds to finish.
    // Queue a stop only once, and keep its best (fewest transfers) tier.
    const pendingStopTiers = new Map();
    const renderedStopTiers = new Map();
    const stopPaintQueue = [];
    let stopPaintCursor = 0;
    let stopPaintFrame = null;
    const progressiveStopCounts = [0, 0, 0];

    function clearProgressiveStops() {
        cancelStopCanvasRedraw();
        if (stopPaintFrame !== null) cancelAnimationFrame(stopPaintFrame);
        stopPaintFrame = null;
        stopPaintQueue.length = 0;
        stopPaintCursor = 0;
        pendingStopTiers.clear();
        renderedStopTiers.clear();
        progressiveStopCounts.fill(0);
    }

    function updateProgressiveStopCounts() {
        for (let tier = 0; tier < 3; ++tier) {
            const id = ['direct-stop-count', 'transfer-stop-count', 'two-transfer-stop-count'][
                tier
            ];
            document.getElementById(id).textContent = progressiveStopCounts[tier];
        }
    }

    function paintProgressiveStops(token) {
        const frameStart = performance.now();
        stopPaintFrame = null;
        if (token !== renderGeneration || !selectedPoint) return;
        const started = performance.now();
        let count = 0;
        while (
            stopPaintCursor < stopPaintQueue.length &&
            count < 48 &&
            performance.now() - started < 3
        ) {
            const stopIndex = stopPaintQueue[stopPaintCursor++];
            const tier = pendingStopTiers.get(stopIndex);
            if (tier === undefined || renderedStopTiers.get(stopIndex) === tier) {
                continue;
            }
            updateStopMarkerTier(stopIndex, tier);
            renderedStopTiers.set(stopIndex, tier);
            count++;
        }
        updateProgressiveStopCounts();
        if (stopPaintCursor < stopPaintQueue.length) {
            stopPaintFrame = requestAnimationFrame(() => paintProgressiveStops(token));
        } else {
            stopPaintQueue.length = 0;
            stopPaintCursor = 0;
        }
        profileMarkPaint('stopPaint', performance.now() - frameStart, count);
    }

    function queueProgressiveStop(stopIndex, tier, token) {
        if (token !== renderGeneration || selectedRouteFocus) return;
        if (tier < 0 || tier > 2 || !stops[stopIndex]) return;
        const previous = pendingStopTiers.get(stopIndex);
        if (previous !== undefined && previous <= tier) return;
        if (previous !== undefined) progressiveStopCounts[previous]--;
        progressiveStopCounts[tier]++;
        pendingStopTiers.set(stopIndex, tier);
        if (previous === undefined || renderedStopTiers.has(stopIndex)) {
            stopPaintQueue.push(stopIndex);
        }
        if (stopPaintFrame === null) {
            stopPaintFrame = requestAnimationFrame(() => paintProgressiveStops(token));
        }
    }

    function nearestDisplayedStop(latlng, maxPixels) {
        if (!visibleStopIndexes.size) return null;
        const clickPoint = map.latLngToContainerPoint(latlng);
        const gx = Math.floor(clickPoint.x / STOP_HIT_CELL);
        const gy = Math.floor(clickPoint.y / STOP_HIT_CELL);
        let bestIndex = null;
        let bestDistance2 = maxPixels * maxPixels;
        const span = Math.ceil(maxPixels / STOP_HIT_CELL);
        for (let dx = -span; dx <= span; ++dx) {
            for (let dy = -span; dy <= span; ++dy) {
                const cell = stopHitGrid.get(gx + dx + ':' + (gy + dy));
                if (!cell) continue;
                for (const stopIndex of cell) {
                    const stop = stops[stopIndex];
                    const point = map.latLngToContainerPoint([stop.lat, stop.lon]);
                    const distance2 = (point.x - clickPoint.x) ** 2 + (point.y - clickPoint.y) ** 2;
                    if (distance2 <= bestDistance2) {
                        bestDistance2 = distance2;
                        bestIndex = stopIndex;
                    }
                }
            }
        }
        return bestIndex;
    }

    function openStopPopup(stopIndex) {
        const stop = stops[stopIndex];
        const tier = renderedStopTiers.get(stopIndex);
        if (!stop || tier === undefined) return;
        L.popup({ maxWidth: 320 })
            .setLatLng([stop.lat, stop.lon])
            .setContent(stopPopupContent(stopIndex, tier))
            .openOn(map);
    }

    function stopDistanceMeters(aIndex, bIndex) {
        const a = stops[aIndex];
        const b = stops[bIndex];
        return Math.hypot(a.x - b.x, a.y - b.y);
    }

    function pointToStopDistanceMeters(point, stopIndex) {
        return walkContextForPoint(point).stopsReached.get(stopIndex) ?? Infinity;
    }

    function pointToPointDistanceMeters(a, b) {
        let context = walkContextForPoint(a);
        if (context.anchor && !context.distance.size) {
            const xy = project(a.lon, a.lat);
            const key = xy[0].toFixed(1) + ':' + xy[1].toFixed(1) + ':' + walkRadius;
            context = {
                anchor: context.anchor,
                ...walkDijkstra(walkSeeds(context.anchor), walkRadius),
            };
            walkPointCache.set(key, context);
        }
        const coords = project(b.lon, b.lat);
        const anchor = snapWalkXY(coords[0], coords[1]);
        return walkDistanceToAnchor(context, anchor);
    }

    function stopPositions(direction, stopIndex) {
        const result = [];

        for (let pos = 0; pos < direction.stops.length; ++pos) {
            if (direction.stops[pos] === stopIndex) {
                result.push(pos);
            }
        }

        return result;
    }

    function directionsBetween(routeKey, startStopIndex, endStopIndex) {
        const route = routes[routeKey];
        const result = [];

        if (!route || !isRouteEnabled(routeKey)) {
            return result;
        }

        for (const direction of route.directions) {
            const startPositions = stopPositions(direction, startStopIndex);

            for (const startPos of startPositions) {
                for (let endPos = startPos + 1; endPos < direction.stops.length; ++endPos) {
                    if (direction.stops[endPos] !== endStopIndex) {
                        continue;
                    }

                    result.push({
                        routeKey: routeKey,
                        direction: direction,
                        startStopIndex: startStopIndex,
                        endStopIndex: endStopIndex,
                        startPos: startPos,
                        endPos: endPos,
                        rideStops: endPos - startPos,
                    });
                }
            }
        }

        return result;
    }

    function routeSpeedMetersPerMin(routeKey) {
        const route = routes[routeKey];
        const mode = route ? route.mode || route.route : 'bus';
        return MODE_SPEEDS[mode] ?? BUS_SPEED_M_PER_MIN;
    }

    function legParts(leg) {
        if (leg.parts && leg.parts.length) {
            return leg.parts;
        }

        return [
            {
                direction: leg.direction,
                startPos: leg.startPos,
                endPos: leg.endPos,
            },
        ];
    }

    function legStartStopIndex(leg) {
        const parts = legParts(leg);

        for (const part of parts) {
            if (
                part &&
                part.direction &&
                part.direction.stops &&
                part.startPos !== undefined &&
                part.startPos >= 0 &&
                part.startPos < part.direction.stops.length
            ) {
                return part.direction.stops[part.startPos];
            }
        }

        return leg.startStopIndex;
    }

    function legEndStopIndex(leg) {
        const parts = legParts(leg);

        for (let i = parts.length - 1; i >= 0; --i) {
            const part = parts[i];

            if (
                part &&
                part.direction &&
                part.direction.stops &&
                part.endPos !== undefined &&
                part.endPos >= 0 &&
                part.endPos < part.direction.stops.length
            ) {
                return part.direction.stops[part.endPos];
            }
        }

        return leg.endStopIndex;
    }

    function stopChainDistanceMeters(leg) {
        let result = 0;
        let previousEnd = null;

        for (const part of legParts(leg)) {
            const first = part.direction.stops[part.startPos];
            if (previousEnd !== null) {
                result += stopDistanceMeters(previousEnd, first);
            }
            for (let pos = part.startPos; pos < part.endPos; ++pos) {
                const a = part.direction.stops[pos];
                const b = part.direction.stops[pos + 1];
                result += stopDistanceMeters(a, b);
            }
            previousEnd = part.direction.stops[part.endPos];
        }

        return result;
    }

    // OSM-only model: these are estimates, not GTFS departure or travel times.
    // Distances use consecutive mapped route stops and mode circuity factors.
    const MODE_DISTANCE_FACTORS = {
        bus: 1.25,
        tram: 1.18,
        trolleybus: 1.25,
        metro: 1.1,
        mcc: 1.08,
        mcd: 1.08,
        train: 1.08,
    };

    function legDistanceMeters(leg) {
        const route = routes[leg.routeKey];
        const mode = route ? route.mode || route.route : 'bus';
        const factor = MODE_DISTANCE_FACTORS[mode] ?? 1.2;
        let distance = 0;
        let previousEnd = null;

        for (const part of legParts(leg)) {
            const first = part.direction.stops[part.startPos];
            if (previousEnd !== null) {
                distance += stopDistanceMeters(previousEnd, first) * factor;
            }
            const edges = part.direction.edge_meters || [];
            for (let pos = part.startPos; pos < part.endPos; ++pos) {
                const mapped = edges[pos];
                if (Number.isFinite(mapped) && mapped > 0) {
                    distance += mapped;
                } else {
                    const a = part.direction.stops[pos];
                    const b = part.direction.stops[pos + 1];
                    distance += stopDistanceMeters(a, b) * factor;
                }
            }
            previousEnd = part.direction.stops[part.endPos];
        }
        return distance;
    }

    function legEstimatedMinutes(leg) {
        const distance = legDistanceMeters(leg);
        const speed = routeSpeedMetersPerMin(leg.routeKey);
        // Count same-line terminal dwell in final itinerary metrics too, not
        // only in the RAPTOR scan (otherwise summaries understate travel time).
        const terminalMinutes = legParts(leg).reduce(
            (total, part) => total + (part.terminalMinutes || 0),
            0,
        );
        return {
            distance: distance,
            minutes: distance / speed + leg.rideStops * STOP_DELAY_MIN + terminalMinutes,
        };
    }

    function prepareItineraryMetrics(itinerary) {
        if (Number.isFinite(itinerary.generalizedMinutes)) {
            return itinerary;
        }

        const walkTime = itinerary.walkMeters / WALK_SPEED_M_PER_MIN;
        itinerary.walkMinutes = walkTime;

        if (itinerary.type === 'walk') {
            itinerary.transitMeters = 0;
            itinerary.boardings = 0;
            itinerary.totalMinutes = walkTime;
            itinerary.generalizedMinutes = walkTime * (1.0 + WALK_PREFERENCE_EXTRA);
            return itinerary;
        }

        const leg1 = legEstimatedMinutes(itinerary.leg1);
        let transitMeters = leg1.distance;
        let transitMinutes = leg1.minutes;
        let boardings = 1;

        if (itinerary.type === 'transfer' || itinerary.type === 'two-transfer') {
            const leg2 = legEstimatedMinutes(itinerary.leg2);
            transitMeters += leg2.distance;
            transitMinutes += leg2.minutes;
            boardings = 2;
        }

        if (itinerary.type === 'two-transfer') {
            const leg3 = legEstimatedMinutes(itinerary.leg3);
            transitMeters += leg3.distance;
            transitMinutes += leg3.minutes;
            boardings = 3;
        }

        itinerary.transitMeters = transitMeters;
        itinerary.boardings = boardings;
        const waitingMinutes = candidateLegs(itinerary).reduce(
            (total, leg) => total + boardingWaitForRoute(leg.routeKey),
            0,
        );
        itinerary.totalMinutes = walkTime + transitMinutes + waitingMinutes;
        itinerary.generalizedMinutes = itinerary.totalMinutes + walkTime * WALK_PREFERENCE_EXTRA;

        return itinerary;
    }

    function withinTravelTimeLimit(itinerary) {
        if (!Number.isFinite(maxTravelMinutes)) {
            return true;
        }

        prepareItineraryMetrics(itinerary);
        return itinerary.totalMinutes <= maxTravelMinutes + 1e-7;
    }

    function itineraryTransferCount(itinerary) {
        if (itinerary.type === 'two-transfer') {
            return 2;
        }

        return itinerary.type === 'transfer' ? 1 : 0;
    }

    function compareItineraries(a, b) {
        prepareItineraryMetrics(a);
        prepareItineraryMetrics(b);

        const transferDiff = itineraryTransferCount(a) - itineraryTransferCount(b);

        if (transferDiff !== 0) {
            return transferDiff;
        }

        const scoreDiff = a.generalizedMinutes - b.generalizedMinutes;

        if (Math.abs(scoreDiff) > 0.05) {
            return scoreDiff;
        }

        if (a.walkMeters !== b.walkMeters) {
            return a.walkMeters - b.walkMeters;
        }

        const timeDiff = a.totalMinutes - b.totalMinutes;

        if (Math.abs(timeDiff) > 0.05) {
            return timeDiff;
        }

        if (a.rideStops !== b.rideStops) {
            return a.rideStops - b.rideStops;
        }

        if (a.transitMeters !== b.transitMeters) {
            return a.transitMeters - b.transitMeters;
        }

        return a.initialWalk - b.initialWalk;
    }

    function roughItineraryMinutes(itinerary) {
        let boardings = 0;

        if (itinerary.type === 'direct') {
            boardings = 1;
        } else if (itinerary.type === 'transfer') {
            boardings = 2;
        } else if (itinerary.type === 'two-transfer') {
            boardings = 3;
        }

        return (
            itinerary.walkMeters / WALK_SPEED_M_PER_MIN +
            itinerary.rideStops * ROUGH_STOP_MIN +
            (boardings
                ? candidateLegs(itinerary).reduce(
                      (total, leg) => total + boardingWaitForRoute(leg.routeKey),
                      0,
                  )
                : 0)
        );
    }

    function roughGeneralizedMinutes(itinerary) {
        const walkTime = itinerary.walkMeters / WALK_SPEED_M_PER_MIN;

        return roughItineraryMinutes(itinerary) + walkTime * WALK_PREFERENCE_EXTRA;
    }

    function evidenceDominates(a, b) {
        const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
        const noMoreStops = a.rideStops <= b.rideStops;
        const strictlyBetter = a.walkMeters < b.walkMeters - 1.0 || a.rideStops < b.rideStops;

        return noMoreWalk && noMoreStops && strictlyBetter;
    }

    function trimEvidenceBucket(bucket) {
        if (bucket.length <= MAX_EVIDENCE_PER_PATH) {
            return bucket;
        }

        const keep = new Set();
        let minWalkIndex = 0;
        let minStopsIndex = 0;

        for (let i = 1; i < bucket.length; ++i) {
            if (bucket[i].walkMeters < bucket[minWalkIndex].walkMeters) {
                minWalkIndex = i;
            }

            if (bucket[i].rideStops < bucket[minStopsIndex].rideStops) {
                minStopsIndex = i;
            }
        }

        keep.add(bucket[minWalkIndex]);
        keep.add(bucket[minStopsIndex]);

        const ordered = bucket
            .slice()
            .sort((a, b) => roughGeneralizedMinutes(a) - roughGeneralizedMinutes(b));

        for (const candidate of ordered) {
            keep.add(candidate);

            if (keep.size >= MAX_EVIDENCE_PER_PATH) {
                break;
            }
        }

        return Array.from(keep);
    }

    function updateEvidence(evidence, stopIndex, pathKey, candidate) {
        if (!withinTravelTimeLimit(candidate)) {
            return;
        }

        let byPath = evidence.get(stopIndex);

        if (!byPath) {
            byPath = new Map();
            evidence.set(stopIndex, byPath);
        }

        let bucket = byPath.get(pathKey) || [];

        if (bucket.some((item) => evidenceDominates(item, candidate))) {
            return;
        }

        bucket = bucket.filter((item) => !evidenceDominates(candidate, item));
        bucket.push(candidate);
        byPath.set(pathKey, trimEvidenceBucket(bucket));
    }

    function flattenEvidence(byPath) {
        const result = [];

        if (!byPath) {
            return result;
        }

        for (const bucket of byPath.values()) {
            result.push(...bucket);
        }

        return result;
    }

    function bestTransferExtensionPaths(byPath) {
        const bestByLastRoute = new Map();

        for (const path of flattenEvidence(byPath)) {
            const routeKey = path.leg2.routeKey;
            const current = bestByLastRoute.get(routeKey);

            if (!current || roughGeneralizedMinutes(path) < roughGeneralizedMinutes(current)) {
                bestByLastRoute.set(routeKey, path);
            }
        }

        return Array.from(bestByLastRoute.values())
            .sort((a, b) => roughGeneralizedMinutes(a) - roughGeneralizedMinutes(b))
            .slice(0, MAX_TRANSFER_EXTENSION_OPTIONS);
    }

    function addTransferBoardOption(boardEvidence, boardStopIndex, candidate) {
        if (!withinTravelTimeLimit(candidate)) {
            return;
        }

        let options = boardEvidence.get(boardStopIndex) || [];
        const routeKey = candidate.leg2.routeKey;
        const existingIndex = options.findIndex((item) => item.leg2.routeKey === routeKey);

        if (existingIndex >= 0) {
            if (
                roughGeneralizedMinutes(candidate) >=
                roughGeneralizedMinutes(options[existingIndex])
            ) {
                return;
            }

            options[existingIndex] = candidate;
        } else {
            options.push(candidate);
        }

        options.sort((a, b) => roughGeneralizedMinutes(a) - roughGeneralizedMinutes(b));

        if (options.length > MAX_TRANSFER_EXTENSION_OPTIONS) {
            options = options.slice(0, MAX_TRANSFER_EXTENSION_OPTIONS);
        }

        boardEvidence.set(boardStopIndex, options);
    }

    function itineraryDominates(a, b) {
        prepareItineraryMetrics(a);
        prepareItineraryMetrics(b);

        const noSlower = a.totalMinutes <= b.totalMinutes + 0.1;
        const noMoreWalk = a.walkMeters <= b.walkMeters + 20.0;
        const noMoreBoardings = a.boardings <= b.boardings;
        const strictlyBetter =
            a.totalMinutes < b.totalMinutes - 0.1 ||
            a.walkMeters < b.walkMeters - 20.0 ||
            a.boardings < b.boardings;

        return noSlower && noMoreWalk && noMoreBoardings && strictlyBetter;
    }

    function itineraryIdentityKey(itinerary) {
        if (itinerary.type === 'walk') {
            return 'walk';
        }

        const legs = candidateLegs(itinerary).map((leg) =>
            [
                leg.routeKey,
                String(legStartStopIndex(leg)),
                String(legEndStopIndex(leg)),
                routeLegPathKey(leg),
            ].join(':'),
        );
        const transfers = [];

        if (itinerary.leg2) {
            transfers.push(
                [itinerary.transferAlightStopIndex, itinerary.transferBoardStopIndex].join('-'),
            );
        }

        if (itinerary.leg3) {
            transfers.push(
                [
                    itinerary.secondTransferAlightStopIndex,
                    itinerary.secondTransferBoardStopIndex,
                ].join('-'),
            );
        }

        return legs.join('>') + '|' + transfers.join('>');
    }

    function filterReasonableItineraries(itineraries) {
        const unique = new Map();

        for (const itinerary of itineraries) {
            if (!withinTravelTimeLimit(itinerary) || itineraryHasTechnicalSamePlaceLeg(itinerary)) {
                continue;
            }

            const identity = itineraryIdentityKey(itinerary);
            const current = unique.get(identity);

            if (!current || compareItineraries(itinerary, current) < 0) {
                unique.set(identity, itinerary);
            }
        }

        const sorted = Array.from(unique.values());

        for (const itinerary of sorted) {
            prepareItineraryMetrics(itinerary);
        }

        sorted.sort(compareItineraries);
        const pareto = [];

        for (const candidate of sorted) {
            if (pareto.some((item) => itineraryDominates(item, candidate))) {
                continue;
            }

            pareto.push(candidate);
        }

        const byTransferCount = new Map();

        for (const candidate of pareto) {
            const count = itineraryTransferCount(candidate);
            const group = byTransferCount.get(count) || [];
            group.push(candidate);
            byTransferCount.set(count, group);
        }

        const reasonable = [];

        for (const group of byTransferCount.values()) {
            const bestTime = Math.min(...group.map((item) => item.totalMinutes));
            const maxTime = Math.max(
                bestTime * REASONABLE_TIME_FACTOR,
                bestTime + REASONABLE_TIME_EXTRA_MIN,
            );

            reasonable.push(...group.filter((item) => item.totalMinutes <= maxTime));
        }

        return reasonable.sort(compareItineraries).slice(0, MAX_TRIP_ALTERNATIVES);
    }

    function relationKey(direction) {
        // Distinguish clipped variants of the same OSM relation.
        if (direction.direction_id !== undefined && direction.direction_id !== null) {
            return String(direction.direction_id);
        }
        if (direction.relation_id !== null && direction.relation_id !== undefined) {
            return String(direction.relation_id);
        }

        return [direction.from || '', direction.to || '', direction.stops.join(',')].join(':');
    }

    function findDirectItineraries(targetStopIndex, analysis) {
        const byDirection = analysis.directEvidence.get(targetStopIndex);

        return flattenEvidence(byDirection).sort(compareItineraries);
    }

    function findTransferItineraries(targetStopIndex, analysis) {
        if (maxTransfers < 1) {
            return [];
        }

        const byPair = analysis.transferEvidence.get(targetStopIndex);

        return flattenEvidence(byPair).sort(compareItineraries);
    }

    function findTwoTransferItineraries(targetStopIndex, analysis) {
        if (maxTransfers < 2) {
            return [];
        }

        const byTriple = analysis.twoTransferEvidence.get(targetStopIndex);

        return flattenEvidence(byTriple).sort(compareItineraries);
    }

    function findItineraries(targetStopIndex) {
        const key = selectionKey() + ':' + String(targetStopIndex);
        const cached = itineraryCache.get(key);

        if (cached) {
            return cached;
        }

        const result = [];
        const targetWalk = pointToStopDistanceMeters(selectedPoint, targetStopIndex);

        if (targetWalk <= walkRadius) {
            result.push({
                type: 'walk',
                walkMeters: targetWalk,
                rideStops: 0,
                initialWalk: targetWalk,
            });
        }

        const analysis = analyzeSelection();

        result.push(...findDirectItineraries(targetStopIndex, analysis));

        if (maxTransfers >= 1) {
            result.push(...findTransferItineraries(targetStopIndex, analysis));
        }

        if (maxTransfers >= 2) {
            result.push(...findTwoTransferItineraries(targetStopIndex, analysis));
        }

        const filtered = filterReasonableItineraries(result);
        itineraryCache.set(key, filtered);
        return filtered;
    }

    function copyItineraryForTargetPoint(itinerary, targetStopIndex, finalWalk) {
        prepareItineraryMetrics(itinerary);
        const finalWalkMinutes = finalWalk / WALK_SPEED_M_PER_MIN;

        return {
            ...itinerary,
            targetStopIndex: targetStopIndex,
            finalWalk: finalWalk,
            walkMeters: itinerary.walkMeters + finalWalk,
            walkMinutes: itinerary.walkMinutes + finalWalkMinutes,
            totalMinutes: itinerary.totalMinutes + finalWalkMinutes,
            generalizedMinutes:
                itinerary.generalizedMinutes + finalWalkMinutes * (1.0 + WALK_PREFERENCE_EXTRA),
        };
    }

    function targetPointKey(point) {
        return [point.lat.toFixed(6), point.lon.toFixed(6)].join(':');
    }

    function findPointItineraries(point) {
        const key = selectionKey() + ':point:' + targetPointKey(point);
        const cached = itineraryCache.get(key);

        if (cached) {
            return cached;
        }

        const result = [];
        const directWalk = pointToPointDistanceMeters(selectedPoint, point);

        if (directWalk <= walkRadius) {
            result.push({
                type: 'walk',
                walkMeters: directWalk,
                rideStops: 0,
                initialWalk: directWalk,
                finalWalk: directWalk,
                targetStopIndex: null,
            });
        }

        const analysis = analyzeSelection();
        const targetStops = findNearbyStops(point.lat, point.lon);

        for (const stopIndex of targetStops) {
            const finalWalk = pointToStopDistanceMeters(point, stopIndex);

            const direct = findDirectItineraries(stopIndex, analysis);

            for (const itinerary of direct) {
                result.push(copyItineraryForTargetPoint(itinerary, stopIndex, finalWalk));
            }

            if (maxTransfers >= 1) {
                const transfer = findTransferItineraries(stopIndex, analysis);

                for (const itinerary of transfer) {
                    result.push(copyItineraryForTargetPoint(itinerary, stopIndex, finalWalk));
                }
            }

            if (maxTransfers >= 2) {
                const twoTransfer = findTwoTransferItineraries(stopIndex, analysis);

                for (const itinerary of twoTransfer) {
                    result.push(copyItineraryForTargetPoint(itinerary, stopIndex, finalWalk));
                }
            }
        }

        const filtered = filterReasonableItineraries(result);
        itineraryCache.set(key, filtered);
        return filtered;
    }

    function geometryLines(geometry) {
        if (!geometry) {
            return [];
        }

        if (geometry.type === 'LineString') {
            return geometry.coordinates ? [geometry.coordinates] : [];
        }

        if (geometry.type === 'MultiLineString') {
            return geometry.coordinates || [];
        }

        if (geometry.type === 'GeometryCollection') {
            const result = [];

            for (const item of geometry.geometries || []) {
                result.push(...geometryLines(item));
            }

            return result;
        }

        return [];
    }

    function segmentLengthMeters(a, b) {
        const ax = project(a[0], a[1]);
        const bx = project(b[0], b[1]);
        return Math.hypot(ax[0] - bx[0], ax[1] - bx[1]);
    }

    function orderedDirectionParts(direction) {
        const sourceLines = geometryLines(direction.geometry)
            .filter((line) => line && line.length >= 2)
            .map((line) => line.map((coord) => [coord[0], coord[1]]));
        const parts = [];
        let current = null;

        for (const line of sourceLines) {
            if (!current) {
                current = line.slice();
                continue;
            }

            const currentEnd = current[current.length - 1];
            const gap = segmentLengthMeters(currentEnd, line[0]);

            if (gap <= 3.0) {
                if (gap <= 0.5) {
                    current.push(...line.slice(1));
                } else {
                    current.push(...line);
                }
            } else {
                parts.push(current);
                current = line.slice();
            }
        }

        if (current) {
            parts.push(current);
        }

        return parts;
    }

    function projectStopToSegment(stop, segment) {
        const a = segment.aXY;
        const b = segment.bXY;
        const vx = b[0] - a[0];
        const vy = b[1] - a[1];
        const length2 = vx * vx + vy * vy;
        let t = 0;

        if (length2 > 0) {
            t = ((stop.x - a[0]) * vx + (stop.y - a[1]) * vy) / length2;
            t = Math.max(0, Math.min(1, t));
        }

        const x = a[0] + vx * t;
        const y = a[1] + vy * t;
        const distance = Math.hypot(stop.x - x, stop.y - y);
        const lon = segment.a[0] + (segment.b[0] - segment.a[0]) * t;
        const lat = segment.a[1] + (segment.b[1] - segment.a[1]) * t;

        return {
            distance: distance,
            progress: segment.order + t,
            partIndex: segment.partIndex,
            segmentIndex: segment.segmentIndex,
            t: t,
            coord: [lon, lat],
        };
    }

    function chooseStopAnchor(segments, stopIndex, minProgress) {
        const stop = stops[stopIndex];
        let best = null;

        for (const segment of segments) {
            if (segment.order + 1 < minProgress) {
                continue;
            }

            const candidate = projectStopToSegment(stop, segment);

            if (candidate.progress + 1e-7 < minProgress) {
                continue;
            }

            if (!best || candidate.distance < best.distance) {
                best = candidate;
                continue;
            }

            if (
                Math.abs(candidate.distance - best.distance) <= 5.0 &&
                candidate.progress < best.progress
            ) {
                best = candidate;
            }
        }

        return best;
    }

    function getDirectionShape(direction) {
        const cached = directionShapeCache.get(direction);

        if (cached) {
            return cached;
        }

        const parts = orderedDirectionParts(direction);
        const segments = [];
        let order = 0;

        parts.forEach((part, partIndex) => {
            for (let i = 0; i + 1 < part.length; ++i) {
                segments.push({
                    order: order,
                    partIndex: partIndex,
                    segmentIndex: i,
                    a: part[i],
                    b: part[i + 1],
                    aXY: project(part[i][0], part[i][1]),
                    bXY: project(part[i + 1][0], part[i + 1][1]),
                });
                ++order;
            }
        });

        const anchors = [];
        let minProgress = 0;

        for (const stopIndex of direction.stops) {
            const anchor = chooseStopAnchor(segments, stopIndex, minProgress);

            anchors.push(anchor);

            if (anchor) {
                minProgress = anchor.progress + 1e-6;
            }
        }

        const shape = {
            parts: parts,
            anchors: anchors,
        };

        directionShapeCache.set(direction, shape);
        return shape;
    }

    function pushUniqueCoord(target, coord) {
        if (target.length === 0) {
            target.push(coord);
            return;
        }

        const last = target[target.length - 1];

        if (last[0] === coord[0] && last[1] === coord[1]) {
            return;
        }

        target.push(coord);
    }

    function samePartSegment(part, start, end) {
        const result = [];

        pushUniqueCoord(result, start.coord);

        if (start.segmentIndex === end.segmentIndex) {
            pushUniqueCoord(result, end.coord);
            return result;
        }

        for (let i = start.segmentIndex + 1; i <= end.segmentIndex; ++i) {
            pushUniqueCoord(result, part[i]);
        }

        pushUniqueCoord(result, end.coord);
        return result;
    }

    function firstPartSegment(part, start) {
        const result = [];

        pushUniqueCoord(result, start.coord);

        for (let i = start.segmentIndex + 1; i < part.length; ++i) {
            pushUniqueCoord(result, part[i]);
        }

        return result;
    }

    function lastPartSegment(part, end) {
        const result = [];

        for (let i = 0; i <= end.segmentIndex; ++i) {
            pushUniqueCoord(result, part[i]);
        }

        pushUniqueCoord(result, end.coord);
        return result;
    }

    function routeSinglePartGeometry(part) {
        const direction = part.direction;
        const startPos = part.startPos;
        const endPos = part.endPos;

        if (startPos === undefined || endPos === undefined || startPos < 0 || endPos <= startPos) {
            return null;
        }

        const shape = getDirectionShape(direction);
        const start = shape.anchors[startPos];
        const end = shape.anchors[endPos];

        if (!start || !end || end.progress <= start.progress) {
            return null;
        }

        if (start.partIndex === end.partIndex) {
            const coordinates = samePartSegment(shape.parts[start.partIndex], start, end);

            if (coordinates.length < 2) {
                return null;
            }

            return {
                type: 'LineString',
                coordinates: coordinates,
            };
        }

        const lines = [];
        const first = firstPartSegment(shape.parts[start.partIndex], start);

        if (first.length >= 2) {
            lines.push(first);
        }

        for (let partIndex = start.partIndex + 1; partIndex < end.partIndex; ++partIndex) {
            const part = shape.parts[partIndex];

            if (part.length >= 2) {
                lines.push(part);
            }
        }

        const last = lastPartSegment(shape.parts[end.partIndex], end);

        if (last.length >= 2) {
            lines.push(last);
        }

        if (lines.length === 0) {
            return null;
        }

        if (lines.length === 1) {
            return {
                type: 'LineString',
                coordinates: lines[0],
            };
        }

        return {
            type: 'MultiLineString',
            coordinates: lines,
        };
    }

    function stopChainGeometry(direction, startPos, endPos) {
        if (!direction || !direction.stops || endPos <= startPos) {
            return null;
        }

        const coords = [];

        for (let pos = startPos; pos <= endPos; ++pos) {
            const stop = stops[direction.stops[pos]];

            if (!stop || !Number.isFinite(stop.lon) || !Number.isFinite(stop.lat)) {
                continue;
            }

            const coord = [stop.lon, stop.lat];
            const previous = coords[coords.length - 1];

            if (!previous || previous[0] !== coord[0] || previous[1] !== coord[1]) {
                coords.push(coord);
            }
        }

        if (coords.length < 2) {
            return null;
        }

        return { type: 'LineString', coordinates: coords };
    }

    function routeSegmentGeometry(leg) {
        const lines = [];

        for (const part of legParts(leg)) {
            if (part.endPos <= part.startPos) {
                continue;
            }

            // Rail route ways may be incomplete or unordered in OSM.
            // Never hide an otherwise reachable segment merely because map
            // matching failed: join its ordered stations as a fallback.
            const geometry =
                routeSinglePartGeometry(part) ||
                stopChainGeometry(part.direction, part.startPos, part.endPos);

            if (geometry) {
                lines.push(...geometryLines(geometry));
            }
        }

        if (lines.length === 0) {
            return null;
        }

        if (lines.length === 1) {
            return {
                type: 'LineString',
                coordinates: lines[0],
            };
        }

        return {
            type: 'MultiLineString',
            coordinates: lines,
        };
    }

    function fallbackTripLegLatLngs(leg) {
        const result = [];

        function pushStop(stopIndex) {
            const stop = stops[stopIndex];

            if (!stop) {
                return;
            }

            const latlng = [stop.lat, stop.lon];
            const last = result[result.length - 1];

            if (last && last[0] === latlng[0] && last[1] === latlng[1]) {
                return;
            }

            result.push(latlng);
        }

        for (const part of legParts(leg)) {
            if (!part || !part.direction || !part.direction.stops) {
                continue;
            }

            const startPos = Math.max(0, Number(part.startPos) || 0);
            const endPos = Math.min(part.direction.stops.length - 1, Number(part.endPos) || 0);

            if (endPos < startPos) {
                continue;
            }

            for (let pos = startPos; pos <= endPos; ++pos) {
                pushStop(part.direction.stops[pos]);
            }
        }

        return result;
    }

    function tripGeometryEndpointDistanceMeters(latlng, stopIndex) {
        const stop = stops[stopIndex];

        if (
            !stop ||
            !latlng ||
            latlng.length < 2 ||
            !Number.isFinite(latlng[0]) ||
            !Number.isFinite(latlng[1])
        ) {
            return Infinity;
        }

        const xy = project(latlng[1], latlng[0]);
        return Math.hypot(stop.x - xy[0], stop.y - xy[1]);
    }

    function tripEdgeGeometryLines(direction, startPos, endPos) {
        const startStopIndex = direction.stops[startPos];
        const endStopIndex = direction.stops[endPos];
        const fallback = [
            [stops[startStopIndex].lat, stops[startStopIndex].lon],
            [stops[endStopIndex].lat, stops[endStopIndex].lon],
        ];

        try {
            const geometry = routeSinglePartGeometry({
                direction: direction,
                startPos: startPos,
                endPos: endPos,
            });

            if (!geometry) {
                return [fallback];
            }

            const lines = geometryLines(geometry)
                .filter((line) => line && line.length >= 2)
                .map((line) =>
                    line
                        .map((coord) => [coord[1], coord[0]])
                        .filter(
                            (latlng) => Number.isFinite(latlng[0]) && Number.isFinite(latlng[1]),
                        ),
                )
                .filter((line) => line.length >= 2);

            if (!lines.length) {
                return [fallback];
            }

            const first = lines[0][0];
            const lastLine = lines[lines.length - 1];
            const last = lastLine[lastLine.length - 1];
            const startError = tripGeometryEndpointDistanceMeters(first, startStopIndex);
            const endError = tripGeometryEndpointDistanceMeters(last, endStopIndex);

            /*
             * A route geometry can pass close to the same stop several times.
             * If map matching selected another branch of a loop, reject only
             * this stop-to-stop edge instead of corrupting the whole trip leg.
             */
            if (startError > 250.0 || endError > 250.0) {
                console.warn(
                    'Trip edge geometry rejected',
                    startError,
                    endError,
                    startStopIndex,
                    endStopIndex,
                );
                return [fallback];
            }

            return lines;
        } catch (error) {
            console.warn('Trip edge geometry fallback', error, startStopIndex, endStopIndex);
            return [fallback];
        }
    }

    function tripLegLatLngLines(leg) {
        const result = [];
        const route = routes[leg.routeKey];

        for (const part of legParts(leg)) {
            if (!part || !part.direction || !part.direction.stops) {
                continue;
            }

            const directionBelongsToRoute =
                route && route.directions && route.directions.includes(part.direction);

            if ((part.routeKey && part.routeKey !== leg.routeKey) || !directionBelongsToRoute) {
                console.error(
                    'Trip leg/geometry route mismatch',
                    leg.routeKey,
                    part.routeKey,
                    relationKey(part.direction),
                );
                continue;
            }

            const startPos = Math.max(0, Number(part.startPos) || 0);
            const endPos = Math.min(part.direction.stops.length - 1, Number(part.endPos) || 0);

            for (let pos = startPos; pos < endPos; ++pos) {
                result.push(...tripEdgeGeometryLines(part.direction, pos, pos + 1));
            }
        }

        if (result.length) {
            return result;
        }

        const fallback = fallbackTripLegLatLngs(leg);
        return fallback.length >= 2 ? [fallback] : [];
    }

    function drawTripRouteLeg(leg, color) {
        const route = routes[leg.routeKey];

        if (!route) {
            return false;
        }

        const lines = tripLegLatLngLines(leg);

        if (!lines.length) {
            console.warn('No drawable trip geometry', leg);
            return false;
        }

        const startStopIndex = legStartStopIndex(leg);
        const endStopIndex = legEndStopIndex(leg);
        const startStop = stops[startStopIndex];
        const endStop = stops[endStopIndex];
        const popupHtml =
            startStop && endStop
                ? '<b>' +
                  escapeHtml(routeLabel(route)) +
                  '</b><br>' +
                  escapeHtml(startStop.name || TEXT.stop) +
                  ' &rarr; ' +
                  escapeHtml(endStop.name || TEXT.stop)
                : null;

        let drawn = false;

        for (const latlngs of lines) {
            const cleanLatLngs = latlngs.filter(
                (latlng) =>
                    latlng &&
                    latlng.length >= 2 &&
                    Number.isFinite(latlng[0]) &&
                    Number.isFinite(latlng[1]),
            );

            if (cleanLatLngs.length < 2) {
                continue;
            }

            try {
                const layer = L.polyline(cleanLatLngs, {
                    pane: 'tripRoutePane',
                    interactive: false,
                    color: color,
                    weight: 6,
                    opacity: 0.95,
                    lineCap: 'round',
                    lineJoin: 'round',
                });

                if (popupHtml) {
                    layer.bindPopup(popupHtml);
                }

                layer.addTo(tripRouteLayer);
                drawn = true;
            } catch (error) {
                console.warn('Trip polyline pane fallback', error);

                try {
                    const fallbackLayer = L.polyline(cleanLatLngs, {
                        interactive: false,
                        color: color,
                        weight: 6,
                        opacity: 0.95,
                    });

                    if (popupHtml) {
                        fallbackLayer.bindPopup(popupHtml);
                    }

                    fallbackLayer.addTo(tripRouteLayer);
                    drawn = true;
                } catch (fallbackError) {
                    console.error('Unable to draw trip polyline', fallbackError, cleanLatLngs);
                }
            }
        }

        return drawn;
    }

    function drawTripWalk(lat1, lon1, lat2, lon2) {
        const start = project(lon1, lat1);
        const end = project(lon2, lat2);
        const xy = walkPolylineXY(start, end);
        if (!xy) return;
        L.polyline(xy.map(walkXYToLatLng), {
            color: '#555',
            weight: 3,
            opacity: 0.85,
            dashArray: '5 7',
            interactive: false,
            pane: 'tripWalkPane',
        }).addTo(tripWalkLayer);
    }

    function drawTripMarker(stopIndex, label, color) {
        const stop = stops[stopIndex];
        const marker = L.circleMarker([stop.lat, stop.lon], {
            radius: 7,
            weight: 2,
            color: color,
            fillColor: '#fff',
            fillOpacity: 1,
            bubblingMouseEvents: false,
            pane: 'tripMarkerPane',
        });

        marker.bindPopup(
            '<b>' +
                escapeHtml(label) +
                '</b><br>' +
                escapeHtml(stop.name || TEXT.stop) +
                '<br><br>' +
                stopRouteDetails(stopIndex),
        );

        marker.addTo(tripMarkerLayer);
    }

    function drawTripPoint(point, label, color) {
        const marker = L.circleMarker([point.lat, point.lon], {
            radius: 8,
            weight: 3,
            color: color,
            fillColor: '#fff',
            fillOpacity: 1,
            bubblingMouseEvents: false,
            interactive: false,
            pane: 'tripMarkerPane',
        });

        marker.bindPopup('<b>' + escapeHtml(label) + '</b>');
        marker.addTo(tripMarkerLayer);

        // Walking reachability is drawn on OSM paths, not as a disk.
    }

    function minutesForMeters(meters) {
        return Math.max(0, meters / WALK_SPEED_M_PER_MIN);
    }

    function addTripStep(text, className) {
        const step = document.createElement('div');
        step.className = 'trip-step ' + className;
        step.innerHTML = text;
        tripSteps.appendChild(step);
    }

    function legSummary(leg) {
        const route = routes[leg.routeKey];
        const startStopIndex = legStartStopIndex(leg);
        const endStopIndex = legEndStopIndex(leg);
        const startStop = stops[startStopIndex];
        const endStop = stops[endStopIndex];
        let result = routeLabel(route);

        if (startStop || endStop) {
            result +=
                ' (' +
                (startStop && startStop.name ? startStop.name : '?') +
                ' -> ' +
                (endStop && endStop.name ? endStop.name : '?') +
                ')';
        }

        return result;
    }

    function itinerarySummary(itinerary, index) {
        const prefix = TEXT.optionPrefix + String(index + 1) + ': ';

        prepareItineraryMetrics(itinerary);

        if (itinerary.type === 'walk') {
            return (
                prefix +
                TEXT.walkOnly +
                ' ~' +
                itinerary.totalMinutes.toFixed(1) +
                ' ' +
                TEXT.minutesShort
            );
        }

        let routeText = legSummary(itinerary.leg1);

        if (itinerary.type === 'transfer' || itinerary.type === 'two-transfer') {
            routeText += ' -> ' + legSummary(itinerary.leg2);
        }

        if (itinerary.type === 'two-transfer') {
            routeText += ' -> ' + legSummary(itinerary.leg3);
        }

        return (
            prefix +
            routeText +
            ', ~' +
            itinerary.totalMinutes.toFixed(1) +
            ' ' +
            TEXT.minutesShort +
            ', ' +
            TEXT.walkShort +
            ' ' +
            minutesForMeters(itinerary.walkMeters).toFixed(1) +
            ' ' +
            TEXT.minutesShort +
            ', ' +
            String(itinerary.rideStops) +
            ' ' +
            TEXT.stopsShort
        );
    }

    function drawTripAlternatives(point, itineraries, selectedIndex) {
        tripAlternatives.innerHTML = '';

        if (itineraries.length <= 1) {
            return;
        }

        itineraries.forEach((itinerary, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'trip-option';
            button.textContent = itinerarySummary(itinerary, index);

            if (index === selectedIndex) {
                button.classList.add('trip-option-active');
            }

            button.addEventListener('click', () => {
                tripAlternativeIndex = index;
                renderTrip(point);
            });

            tripAlternatives.appendChild(button);
        });
    }

    function addFinalWalkStep(stop, meters) {
        if (meters <= 1.0) {
            return;
        }

        addTripStep(
            TEXT.walkPrefix +
                minutesForMeters(meters).toFixed(1) +
                TEXT.minutesColon +
                escapeHtml(stop.name || TEXT.stop) +
                ' &rarr; ' +
                escapeHtml(TEXT.destination) +
                '.',
            'trip-step-walk',
        );
    }

    function renderTrip(point) {
        clearResult();
        updateControls();

        if (!selectedPoint || !point) {
            return;
        }

        const itineraries = findPointItineraries(point);

        analysisSummary.hidden = true;
        tripPanel.hidden = false;
        tripSteps.innerHTML = '';

        if (tripAlternativeIndex >= itineraries.length) {
            tripAlternativeIndex = 0;
        }

        drawTripAlternatives(point, itineraries, tripAlternativeIndex);

        L.circleMarker([selectedPoint.lat, selectedPoint.lon], {
            radius: 7,
            weight: 2,
            color: '#111',
            fillColor: '#fff',
            fillOpacity: 1,
            bubblingMouseEvents: false,
            interactive: false,
            pane: 'originPane',
        }).addTo(originLayer);

        drawTripPoint(point, TEXT.destination, '#16a34a');

        if (itineraries.length === 0) {
            addTripStep(
                'No route found to the selected point for the current ' +
                    'pedestrian walking limit and transfer mode.',
                'trip-step-walk',
            );
            return;
        }

        const itinerary = itineraries[tripAlternativeIndex];

        if (itinerary.type === 'walk') {
            drawTripWalk(selectedPoint.lat, selectedPoint.lon, point.lat, point.lon);
            addTripStep(
                TEXT.walkPrefix +
                    minutesForMeters(itinerary.walkMeters).toFixed(1) +
                    TEXT.minutesColon +
                    escapeHtml(TEXT.destination) +
                    '.',
                'trip-step-walk',
            );
            return;
        }

        const targetStopIndex = itinerary.targetStopIndex;
        const targetStop = stops[targetStopIndex];
        const leg1 = itinerary.leg1;
        const leg1StartStopIndex = legStartStopIndex(leg1);
        const leg1EndStopIndex = legEndStopIndex(leg1);
        const board = stops[leg1StartStopIndex];
        const alight1 = stops[leg1EndStopIndex];
        const route1 = routes[leg1.routeKey];

        if (!targetStop || !board || !alight1 || !route1) {
            console.error('Invalid first trip leg', itinerary);
            addTripStep('Unable to draw the selected route.', 'trip-step-walk');
            return;
        }

        drawTripWalk(selectedPoint.lat, selectedPoint.lon, board.lat, board.lon);
        drawTripMarker(leg1StartStopIndex, TEXT.boarding, '#1565c0');
        drawTripRouteLeg(leg1, '#1565c0');

        addTripStep(
            TEXT.walkPrefix +
                minutesForMeters(itinerary.initialWalk).toFixed(1) +
                TEXT.walkToStop +
                escapeHtml(board.name || TEXT.stop) +
                '</b>.',
            'trip-step-walk',
        );

        addTripStep(
            '<b>' +
                escapeHtml(routeLabel(route1)) +
                '</b>: ' +
                escapeHtml(board.name || TEXT.stop) +
                ' &rarr; ' +
                escapeHtml(alight1.name || TEXT.stop) +
                ' (' +
                leg1.rideStops +
                TEXT.rideStopsSuffix,
            'trip-step-leg1',
        );

        if (itinerary.type === 'direct') {
            drawTripMarker(targetStopIndex, TEXT.destination, '#1565c0');
            drawTripWalk(targetStop.lat, targetStop.lon, point.lat, point.lon);
            addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
            return;
        }

        const transferBoard = stops[itinerary.transferBoardStopIndex];
        const leg2 = itinerary.leg2;
        const leg2EndStopIndex = legEndStopIndex(leg2);
        const route2 = routes[leg2.routeKey];

        if (!transferBoard || !route2 || stops[leg2EndStopIndex] === undefined) {
            console.error('Invalid second trip leg', itinerary);
            addTripStep('Unable to draw the selected route.', 'trip-step-walk');
            return;
        }

        drawTripMarker(itinerary.transferAlightStopIndex, TEXT.transferExit, '#1565c0');
        drawTripMarker(itinerary.transferBoardStopIndex, TEXT.transferBoarding, '#ef6c00');

        if (itinerary.transferAlightStopIndex !== itinerary.transferBoardStopIndex) {
            drawTripWalk(alight1.lat, alight1.lon, transferBoard.lat, transferBoard.lon);
        }

        drawTripRouteLeg(leg2, '#ef6c00');

        addTripStep(
            TEXT.transferWalkPrefix +
                minutesForMeters(itinerary.transferWalk).toFixed(1) +
                TEXT.minutesColon +
                escapeHtml(alight1.name || TEXT.stop) +
                ' &rarr; ' +
                escapeHtml(transferBoard.name || TEXT.stop) +
                '.',
            'trip-step-walk',
        );

        const alight2 = stops[leg2EndStopIndex];

        addTripStep(
            '<b>' +
                escapeHtml(routeLabel(route2)) +
                '</b>: ' +
                escapeHtml(transferBoard.name || TEXT.stop) +
                ' &rarr; ' +
                escapeHtml(alight2.name || TEXT.stop) +
                ' (' +
                leg2.rideStops +
                TEXT.rideStopsSuffix,
            'trip-step-leg2',
        );

        if (itinerary.type === 'transfer') {
            drawTripMarker(targetStopIndex, TEXT.destination, '#ef6c00');
            drawTripWalk(targetStop.lat, targetStop.lon, point.lat, point.lon);
            addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
            return;
        }

        const secondTransferBoard = stops[itinerary.secondTransferBoardStopIndex];
        const leg3 = itinerary.leg3;
        const leg3EndStopIndex = legEndStopIndex(leg3);
        const route3 = routes[leg3.routeKey];

        if (!route3 || stops[leg3EndStopIndex] === undefined) {
            console.error('Invalid third trip leg', itinerary);
            addTripStep('Unable to draw the selected route.', 'trip-step-walk');
            return;
        }

        drawTripMarker(itinerary.secondTransferAlightStopIndex, TEXT.transferExit, '#ef6c00');
        drawTripMarker(itinerary.secondTransferBoardStopIndex, TEXT.transferBoarding, '#7c3aed');

        if (itinerary.secondTransferAlightStopIndex !== itinerary.secondTransferBoardStopIndex) {
            drawTripWalk(
                alight2.lat,
                alight2.lon,
                secondTransferBoard.lat,
                secondTransferBoard.lon,
            );
        }

        addTripStep(
            TEXT.transferWalkPrefix +
                minutesForMeters(itinerary.secondTransferWalk).toFixed(1) +
                TEXT.minutesColon +
                escapeHtml(alight2.name || TEXT.stop) +
                ' &rarr; ' +
                escapeHtml(secondTransferBoard.name || TEXT.stop) +
                '.',
            'trip-step-walk',
        );

        drawTripRouteLeg(leg3, '#7c3aed');
        drawTripMarker(targetStopIndex, TEXT.destination, '#7c3aed');

        addTripStep(
            '<b>' +
                escapeHtml(routeLabel(route3)) +
                '</b>: ' +
                escapeHtml(secondTransferBoard.name || TEXT.stop) +
                ' &rarr; ' +
                escapeHtml(targetStop.name || TEXT.stop) +
                ' (' +
                leg3.rideStops +
                TEXT.rideStopsSuffix,
            'trip-step-leg3',
        );

        drawTripWalk(targetStop.lat, targetStop.lon, point.lat, point.lon);
        addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
    }

    function showTripToPoint(point) {
        if (!selectedPoint) {
            return;
        }

        targetPoint = {
            lat: point.lat,
            lon: point.lon,
        };
        setTargetPickMode(false);
        tripAlternativeIndex = 0;
        renderTrip(targetPoint);
    }

    function showTripToStop(stopIndex) {
        if (!selectedPoint) {
            return;
        }

        const stop = stops[stopIndex];
        showTripToPoint({
            lat: stop.lat,
            lon: stop.lon,
        });
    }

    function selectionKey() {
        if (!selectedPoint) {
            return '';
        }

        return [
            selectedPoint.lat.toFixed(6),
            selectedPoint.lon.toFixed(6),
            walkRadius.toFixed(1),
            Number.isFinite(maxTravelMinutes) ? maxTravelMinutes : 'unlimited',
            Array.from(enabledModes).sort().join(','),
            String(maxTransfers),
            Number.isFinite(maxTravelMinutes) ? maxTravelMinutes.toFixed(0) : 'inf',
        ].join(':');
    }

    function addReachableRouteSegment(routeSegments, segment) {
        if (!segment || segment.endPos <= segment.startPos) {
            return;
        }

        const directionId = relationKey(segment.direction);
        let startPos = segment.startPos;
        let endPos = segment.endPos;
        let startStopIndex = segment.startStopIndex;
        let endStopIndex = segment.endStopIndex;
        const removeKeys = [];

        for (const [key, current] of routeSegments.entries()) {
            if (relationKey(current.direction) !== directionId) {
                continue;
            }

            if (endPos + 1 < current.startPos || current.endPos + 1 < startPos) {
                continue;
            }

            if (current.startPos < startPos) {
                startPos = current.startPos;
                startStopIndex = current.startStopIndex;
            }

            if (current.endPos > endPos) {
                endPos = current.endPos;
                endStopIndex = current.endStopIndex;
            }

            removeKeys.push(key);
        }

        for (const key of removeKeys) {
            routeSegments.delete(key);
        }

        const key = directionId + ':' + startPos + ':' + endPos;
        routeSegments.set(key, {
            routeKey: segment.routeKey,
            direction: segment.direction,
            startStopIndex: startStopIndex,
            endStopIndex: endStopIndex,
            startPos: startPos,
            endPos: endPos,
            rideStops: endPos - startPos,
        });
    }

    // OSM describes route topology, not vehicle blocks. The reachability model
    // allows *estimated* same-line terminal turnaround only for an unambiguous
    // reciprocal pair of directions sharing a corridor and both endpoints.
    // Explicit roundtrip=yes with coincident endpoints is also a loop. None of
    // these static inferences establishes a scheduled in-seat GTFS block transfer.
    const routeContinuationCache = new WeakMap();
    const ALLOW_INFERRED_THROUGH_SERVICE = true;
    const LOOP_TERMINAL_RADIUS_M = 180.0;
    const LOOP_CORRIDOR_RADIUS_M = 130.0;
    const LOOP_TURNAROUND_MIN = 3.0;

    function directionHasSharedCorridor(a, b) {
        const found = [];
        for (const aStop of a.stops.slice(1, -1)) {
            if (
                !b.stops
                    .slice(1, -1)
                    .some((bStop) => stopDistanceMeters(aStop, bStop) <= LOOP_CORRIDOR_RADIUS_M)
            ) {
                continue;
            }
            if (found.every((old) => stopDistanceMeters(old, aStop) > LOOP_CORRIDOR_RADIUS_M)) {
                found.push(aStop);
                if (found.length >= 3) return true;
            }
        }
        return false;
    }

    function routeDirectionContinuations(route) {
        if (!route) return new Map();
        const cached = routeContinuationCache.get(route);
        if (cached) return cached;
        const directions = route.directions || [];
        const result = new Map();
        const surface = ['bus', 'tram', 'trolleybus'].includes(route.mode || route.route);
        if (!surface || !ALLOW_INFERRED_THROUGH_SERVICE) {
            routeContinuationCache.set(route, result);
            return result;
        }
        for (const arriving of directions) {
            const a = arriving.stops || [];
            if (a.length < 2) continue;
            const last = a[a.length - 1];
            const selfLoop = String(arriving.roundtrip || '').toLowerCase() === 'yes';
            const sameStopLoop = a[0] === last;
            if (
                (selfLoop || sameStopLoop) &&
                stopDistanceMeters(last, a[0]) <= LOOP_TERMINAL_RADIUS_M
            ) {
                result.set(relationKey(arriving), [{ direction: arriving, terminalMinutes: 0 }]);
                continue;
            }
            // The two half-trips need reciprocal endpoints, an overlapping
            // corridor and a unique partner. Merely equal ref or route_master
            // is intentionally insufficient.
            const candidates = directions.filter((departing) => {
                const b = departing.stops || [];
                return (
                    departing !== arriving &&
                    b.length >= 4 &&
                    a.length >= 4 &&
                    arriving.relation_id !== departing.relation_id &&
                    stopDistanceMeters(last, b[0]) <= LOOP_TERMINAL_RADIUS_M &&
                    stopDistanceMeters(a[0], b[b.length - 1]) <= LOOP_TERMINAL_RADIUS_M &&
                    directionHasSharedCorridor(arriving, departing)
                );
            });
            if (candidates.length === 1) {
                const other = candidates[0];
                const reverseCandidates = directions.filter((test) => {
                    const b = test.stops || [];
                    return (
                        test !== other &&
                        b.length >= 4 &&
                        test.relation_id !== other.relation_id &&
                        stopDistanceMeters(other.stops[other.stops.length - 1], b[0]) <=
                            LOOP_TERMINAL_RADIUS_M &&
                        stopDistanceMeters(other.stops[0], b[b.length - 1]) <=
                            LOOP_TERMINAL_RADIUS_M &&
                        directionHasSharedCorridor(other, test)
                    );
                });
                if (reverseCandidates.length === 1 && reverseCandidates[0] === arriving) {
                    result.set(relationKey(arriving), [
                        {
                            direction: other,
                            terminalMinutes: LOOP_TURNAROUND_MIN,
                        },
                    ]);
                }
            }
        }
        routeContinuationCache.set(route, result);
        return result;
    }

    function cloneRouteLegParts(parts) {
        return parts.map((part) => ({ ...part }));
    }

    function routeLegPathKey(leg) {
        return legParts(leg)
            .map(
                (part) =>
                    (part.routeKey || leg.routeKey) +
                    ':' +
                    relationKey(part.direction) +
                    ':' +
                    String(part.startPos) +
                    '-' +
                    String(part.endPos),
            )
            .join('>');
    }

    function routeLegsFromBoard(routeKey, startDirection, startPos) {
        const cacheKey = routeKey + '|' + relationKey(startDirection) + '|' + String(startPos);
        const cached = routeLegTraversalCache.get(cacheKey);

        if (cached) {
            return cached;
        }

        const route = routes[routeKey];

        if (
            !route ||
            !startDirection.stops ||
            startPos < 0 ||
            startPos >= startDirection.stops.length
        ) {
            return [];
        }

        const continuations = routeDirectionContinuations(route);
        const startStopIndex = startDirection.stops[startPos];
        const initialParts = [
            {
                routeKey: routeKey,
                direction: startDirection,
                startPos: startPos,
                endPos: startPos,
            },
        ];
        const queue = [
            {
                direction: startDirection,
                pos: startPos,
                parts: initialParts,
                rideStops: 0,
                rideMinutes: 0,
            },
        ];
        const bestStateMinutes = new Map();
        const result = [];
        const EPSILON = 1e-7;

        bestStateMinutes.set(relationKey(startDirection) + ':' + String(startPos), 0);

        while (queue.length) {
            queue.sort((a, b) => a.rideMinutes - b.rideMinutes);
            const state = queue.shift();
            const stateKey = relationKey(state.direction) + ':' + String(state.pos);
            const known = bestStateMinutes.get(stateKey);

            if (known !== undefined && state.rideMinutes > known + EPSILON) {
                continue;
            }

            if (state.pos + 1 < state.direction.stops.length) {
                const nextPos = state.pos + 1;
                const edge = {
                    routeKey: routeKey,
                    direction: state.direction,
                    startStopIndex: state.direction.stops[state.pos],
                    endStopIndex: state.direction.stops[nextPos],
                    startPos: state.pos,
                    endPos: nextPos,
                    rideStops: 1,
                };
                const edgeMinutes = legEstimatedMinutes(edge).minutes;
                const nextMinutes = state.rideMinutes + edgeMinutes;
                const nextParts = cloneRouteLegParts(state.parts);
                const lastPart = nextParts[nextParts.length - 1];
                lastPart.endPos = nextPos;
                const nextRideStops = state.rideStops + 1;
                const nextStopIndex = state.direction.stops[nextPos];
                const leg = {
                    routeKey: routeKey,
                    direction: startDirection,
                    startStopIndex: startStopIndex,
                    endStopIndex: nextStopIndex,
                    startPos: startPos,
                    endPos: nextPos,
                    rideStops: nextRideStops,
                    parts: nextParts,
                };

                result.push(leg);

                const nextStateKey = relationKey(state.direction) + ':' + String(nextPos);
                const previous = bestStateMinutes.get(nextStateKey);

                if (previous === undefined || nextMinutes + EPSILON < previous) {
                    bestStateMinutes.set(nextStateKey, nextMinutes);
                    queue.push({
                        direction: state.direction,
                        pos: nextPos,
                        parts: nextParts,
                        rideStops: nextRideStops,
                        rideMinutes: nextMinutes,
                    });
                }

                continue;
            }

            const nextDirections = continuations.get(relationKey(state.direction)) || [];

            for (const next of nextDirections) {
                const nextDirection = next.direction;
                if (!nextDirection.stops || nextDirection.stops.length === 0) {
                    continue;
                }

                const nextStateKey = relationKey(nextDirection) + ':0';
                const previous = bestStateMinutes.get(nextStateKey);

                const connectingDistance =
                    stopDistanceMeters(state.direction.stops[state.pos], nextDirection.stops[0]) *
                    MODE_DISTANCE_FACTORS[route.mode || route.route];
                const connectingMinutes = connectingDistance / routeSpeedMetersPerMin(routeKey);
                const nextMinutes = state.rideMinutes + connectingMinutes + next.terminalMinutes;
                if (previous !== undefined && previous <= nextMinutes + EPSILON) {
                    continue;
                }
                const nextParts = cloneRouteLegParts(state.parts);
                nextParts.push({
                    routeKey: routeKey,
                    direction: nextDirection,
                    startPos: 0,
                    endPos: 0,
                    terminalMinutes: next.terminalMinutes,
                });
                bestStateMinutes.set(nextStateKey, nextMinutes);
                queue.push({
                    direction: nextDirection,
                    pos: 0,
                    parts: nextParts,
                    rideStops: state.rideStops,
                    rideMinutes: nextMinutes,
                });
            }
        }

        routeLegTraversalCache.set(cacheKey, result);
        return result;
    }

    function* collectReachableRouteDataSteps(evidence, legName) {
        const segmentsByRoute = new Map();
        const stopsByRoute = new Map();

        let routeStopCounter = 0;
        for (const [stopIndex, paths] of evidence.entries()) {
            if (++routeStopCounter % 32 === 0) yield null;
            for (const path of flattenEvidence(paths)) {
                const leg = path[legName];

                if (!leg) {
                    continue;
                }

                let routeStops = stopsByRoute.get(leg.routeKey);

                if (!routeStops) {
                    routeStops = new Set();
                    stopsByRoute.set(leg.routeKey, routeStops);
                }

                routeStops.add(stopIndex);

                let routeSegments = segmentsByRoute.get(leg.routeKey);

                if (!routeSegments) {
                    routeSegments = new Map();
                    segmentsByRoute.set(leg.routeKey, routeSegments);
                }

                for (const part of legParts(leg)) {
                    if (part.endPos <= part.startPos) {
                        continue;
                    }

                    addReachableRouteSegment(routeSegments, {
                        routeKey: leg.routeKey,
                        direction: part.direction,
                        startStopIndex: part.direction.stops[part.startPos],
                        endStopIndex: part.direction.stops[part.endPos],
                        startPos: part.startPos,
                        endPos: part.endPos,
                        rideStops: part.endPos - part.startPos,
                    });
                }
            }
        }

        return {
            segmentsByRoute: segmentsByRoute,
            stopsByRoute: stopsByRoute,
        };
    }

    function* collectBestArrivalMinutesSteps(evidence) {
        const result = new Map();

        if (!Number.isFinite(maxTravelMinutes)) {
            return result;
        }

        let arrivalStopCounter = 0;
        for (const [stopIndex, paths] of evidence.entries()) {
            if (++arrivalStopCounter % 32 === 0) yield null;
            let best = Infinity;

            for (const path of flattenEvidence(paths)) {
                prepareItineraryMetrics(path);
                best = Math.min(best, path.totalMinutes);
            }

            if (Number.isFinite(best)) {
                result.set(stopIndex, best);
            }
        }

        return result;
    }

    function directionStopRole(direction, pos) {
        if (!direction || !direction.stop_roles) {
            return '';
        }

        return direction.stop_roles[pos] || '';
    }

    function stopAllowsBoarding(direction, pos) {
        const role = directionStopRole(direction, pos);
        return !role.endsWith('_exit_only');
    }

    function stopAllowsAlighting(direction, pos) {
        const role = directionStopRole(direction, pos);
        return !role.endsWith('_entry_only');
    }

    function normalizedStopName(stopIndex) {
        const stop = stops[stopIndex];
        return stop && stop.name ? stop.name.trim().toLocaleLowerCase() : '';
    }

    function isTechnicalSamePlaceLeg(leg) {
        if (!leg || leg.rideStops > 2) {
            return false;
        }

        const startStopIndex = legStartStopIndex(leg);
        const endStopIndex = legEndStopIndex(leg);

        if (startStopIndex === endStopIndex) {
            return true;
        }

        const startName = normalizedStopName(startStopIndex);
        const endName = normalizedStopName(endStopIndex);

        if (!startName || startName !== endName) {
            return false;
        }

        return stopDistanceMeters(startStopIndex, endStopIndex) <= 300.0;
    }

    function itineraryHasTechnicalSamePlaceLeg(itinerary) {
        return candidateLegs(itinerary).some(isTechnicalSamePlaceLeg);
    }

    function boardingOptionsAtStop(stopIndex) {
        const cached = boardingOptionsCache.get(stopIndex);

        if (cached) {
            return cached;
        }

        const result = [];
        const stop = stops[stopIndex];

        for (const routeKey of stop.routes) {
            const route = routes[routeKey];

            if (!route || !isRouteEnabled(routeKey)) {
                continue;
            }

            for (const direction of route.directions) {
                for (const boardPos of stopPositions(direction, stopIndex)) {
                    if (!stopAllowsBoarding(direction, boardPos)) {
                        continue;
                    }

                    result.push({
                        routeKey: routeKey,
                        direction: direction,
                        boardPos: boardPos,
                    });
                }
            }
        }

        boardingOptionsCache.set(stopIndex, result);
        return result;
    }

    function routeEdgeMetrics(routeKey, direction, pos) {
        const key = routeKey + '|' + relationKey(direction) + '|' + String(pos);
        const cached = routeEdgeMetricsCache.get(key);

        if (cached) {
            return cached;
        }

        if (pos < 0 || pos + 1 >= direction.stops.length) {
            return null;
        }

        const edge = {
            routeKey: routeKey,
            direction: direction,
            startStopIndex: direction.stops[pos],
            endStopIndex: direction.stops[pos + 1],
            startPos: pos,
            endPos: pos + 1,
            rideStops: 1,
        };
        const metrics = legEstimatedMinutes(edge);
        routeEdgeMetricsCache.set(key, metrics);
        return metrics;
    }

    /*
     * RAPTOR-style reachability engine.
     * Round 0 = first ride, round 1 = one transfer, round 2 = two transfers.
     * Travel-time changes only filter cached labels; increasing the transfer
     * limit computes only the missing round.
     */
    function reachabilitySearchKey() {
        if (!selectedPoint) {
            return '';
        }

        return [
            selectedPoint.lat.toFixed(6),
            selectedPoint.lon.toFixed(6),
            walkRadius.toFixed(1),
            Number.isFinite(maxTravelMinutes) ? maxTravelMinutes.toFixed(1) : 'inf',
            Array.from(enabledModes).sort().join(','),
        ].join(':');
    }

    function cloneLegParts(parts) {
        return parts.map((part) => ({ ...part }));
    }

    function initialRideLeg(routeKey, direction, boardPos) {
        const stopIndex = direction.stops[boardPos];

        return {
            routeKey: routeKey,
            direction: direction,
            startStopIndex: stopIndex,
            endStopIndex: stopIndex,
            startPos: boardPos,
            endPos: boardPos,
            rideStops: 0,
            parts: [
                {
                    routeKey: routeKey,
                    direction: direction,
                    startPos: boardPos,
                    endPos: boardPos,
                },
            ],
        };
    }

    function extendRideLeg(leg, direction, nextPos) {
        const parts = cloneLegParts(leg.parts);
        const last = parts[parts.length - 1];
        last.endPos = nextPos;

        return {
            ...leg,
            endStopIndex: direction.stops[nextPos],
            endPos: nextPos,
            rideStops: leg.rideStops + 1,
            parts: parts,
        };
    }

    function continueRideLeg(leg, nextDirection, terminalMinutes = 0) {
        const parts = cloneLegParts(leg.parts);
        parts.push({
            routeKey: leg.routeKey,
            direction: nextDirection,
            startPos: 0,
            endPos: 0,
            terminalMinutes: terminalMinutes,
        });

        return {
            ...leg,
            endStopIndex: nextDirection.stops[0],
            endPos: 0,
            parts: parts,
        };
    }

    function itineraryFromRaptorState(state) {
        const legs = state.legs.concat([state.currentLeg]);
        const transferCount = state.transfersUsed;
        const walkMinutes = state.walkMeters / WALK_SPEED_M_PER_MIN;
        const candidate = {
            type:
                transferCount === 0 ? 'direct' : transferCount === 1 ? 'transfer' : 'two-transfer',
            walkMeters: state.walkMeters,
            walkMinutes: walkMinutes,
            rideStops: state.rideStops,
            initialWalk: state.initialWalk,
            transitMeters: state.transitMeters,
            boardings: transferCount + 1,
            totalMinutes: state.elapsedMinutes,
            generalizedMinutes: state.elapsedMinutes + walkMinutes * WALK_PREFERENCE_EXTRA,
            leg1: legs[0],
        };

        if (transferCount >= 1) {
            const transfer = state.transfers[0];
            candidate.leg2 = legs[1];
            candidate.transferWalk = transfer.walkMeters;
            candidate.transferAlightStopIndex = transfer.alightStopIndex;
            candidate.transferBoardStopIndex = transfer.boardStopIndex;
        }

        if (transferCount >= 2) {
            const transfer = state.transfers[1];
            candidate.leg3 = legs[2];
            candidate.secondTransferWalk = transfer.walkMeters;
            candidate.secondTransferAlightStopIndex = transfer.alightStopIndex;
            candidate.secondTransferBoardStopIndex = transfer.boardStopIndex;
        }

        return candidate;
    }

    function candidateLegs(candidate) {
        const result = [];

        if (candidate.leg1) {
            result.push(candidate.leg1);
        }

        if (candidate.leg2) {
            result.push(candidate.leg2);
        }

        if (candidate.leg3) {
            result.push(candidate.leg3);
        }

        return result;
    }

    function candidateTransfers(candidate) {
        const result = [];

        if (candidate.leg2) {
            result.push({
                alightStopIndex: candidate.transferAlightStopIndex,
                boardStopIndex: candidate.transferBoardStopIndex,
                walkMeters: candidate.transferWalk || 0,
            });
        }

        if (candidate.leg3) {
            result.push({
                alightStopIndex: candidate.secondTransferAlightStopIndex,
                boardStopIndex: candidate.secondTransferBoardStopIndex,
                walkMeters: candidate.secondTransferWalk || 0,
            });
        }

        return result;
    }

    function searchEvidencePathKey(candidate) {
        return candidateLegs(candidate)
            .map(
                (leg) =>
                    leg.routeKey +
                    ':' +
                    String(legStartStopIndex(leg)) +
                    '-' +
                    String(legEndStopIndex(leg)) +
                    ':' +
                    routeLegPathKey(leg),
            )
            .join('>');
    }

    function updateSearchEvidence(evidence, stopIndex, candidate) {
        const pathKey = searchEvidencePathKey(candidate);
        let byPath = evidence.get(stopIndex);

        if (!byPath) {
            byPath = new Map();
            evidence.set(stopIndex, byPath);
        }

        let bucket = byPath.get(pathKey) || [];

        if (bucket.some((item) => itineraryDominates(item, candidate))) {
            return;
        }

        bucket = bucket.filter((item) => !itineraryDominates(candidate, item));
        bucket.push(candidate);
        bucket.sort(compareItineraries);

        if (bucket.length > MAX_EVIDENCE_PER_PATH) {
            bucket = bucket.slice(0, MAX_EVIDENCE_PER_PATH);
        }

        byPath.set(pathKey, bucket);
    }

    function raptorSourcePathKey(source) {
        if (!source.legs || source.legs.length === 0) {
            return 'walk';
        }

        return source.legs
            .map(
                (leg) =>
                    leg.routeKey +
                    ':' +
                    String(legStartStopIndex(leg)) +
                    '-' +
                    String(legEndStopIndex(leg)),
            )
            .join('>');
    }

    function raptorSourceDominates(a, b) {
        const noSlower = a.elapsedMinutes <= b.elapsedMinutes + 1e-7;
        const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
        const strictlyBetter =
            a.elapsedMinutes < b.elapsedMinutes - 1e-7 || a.walkMeters < b.walkMeters - 1.0;

        return noSlower && noMoreWalk && strictlyBetter;
    }

    function raptorSourceScore(source) {
        return (
            source.elapsedMinutes +
            (source.walkMeters / WALK_SPEED_M_PER_MIN) * WALK_PREFERENCE_EXTRA
        );
    }

    function addRaptorTransferSource(sourcesByStop, stopIndex, source) {
        let byLastRoute = sourcesByStop.get(stopIndex);

        if (!byLastRoute) {
            byLastRoute = new Map();
            sourcesByStop.set(stopIndex, byLastRoute);
        }

        const lastRouteKey = source.lastRouteKey || '';
        let bucket = byLastRoute.get(lastRouteKey) || [];

        if (bucket.some((item) => raptorSourceDominates(item, source))) {
            return false;
        }

        bucket = bucket.filter((item) => !raptorSourceDominates(source, item));

        const pathKey = raptorSourcePathKey(source);
        const samePathIndex = bucket.findIndex((item) => raptorSourcePathKey(item) === pathKey);

        if (samePathIndex >= 0) {
            const current = bucket[samePathIndex];

            if (raptorSourceScore(current) <= raptorSourceScore(source)) {
                return false;
            }

            bucket.splice(samePathIndex, 1);
        }

        bucket.push(source);
        bucket.sort((a, b) => raptorSourceScore(a) - raptorSourceScore(b));

        if (bucket.length > MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE) {
            bucket = bucket.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE);
        }

        byLastRoute.set(lastRouteKey, bucket);

        const all = [];

        for (const [routeKey, routeBucket] of byLastRoute.entries()) {
            for (const item of routeBucket) {
                all.push({ routeKey: routeKey, source: item });
            }
        }

        if (all.length > MAX_RAPTOR_TRANSFER_LABELS_PER_STOP) {
            all.sort((a, b) => raptorSourceScore(a.source) - raptorSourceScore(b.source));
            const keep = new Set(
                all.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_STOP).map((item) => item.source),
            );

            for (const [routeKey, routeBucket] of byLastRoute.entries()) {
                const filtered = routeBucket.filter((item) => keep.has(item));

                if (filtered.length) {
                    byLastRoute.set(routeKey, filtered);
                } else {
                    byLastRoute.delete(routeKey);
                }
            }
        }

        return true;
    }

    function flattenRaptorSources(byLastRoute) {
        const result = [];

        if (!byLastRoute) {
            return result;
        }

        for (const bucket of byLastRoute.values()) {
            result.push(...bucket);
        }

        return result;
    }

    function raptorOnboardNodeKey(state) {
        return [state.routeKey, relationKey(state.direction), String(state.pos)].join('|');
    }

    function raptorOnboardPathKey(state) {
        return [
            ...state.legs.map((leg) => leg.routeKey),
            state.routeKey,
            String(legStartStopIndex(state.currentLeg)),
        ].join('>');
    }

    function raptorOnboardDominates(a, b) {
        const noSlower = a.elapsedMinutes <= b.elapsedMinutes + 1e-7;
        const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
        const strictlyBetter =
            a.elapsedMinutes < b.elapsedMinutes - 1e-7 || a.walkMeters < b.walkMeters - 1.0;

        return noSlower && noMoreWalk && strictlyBetter;
    }

    function raptorOnboardScore(state) {
        return (
            state.elapsedMinutes + (state.walkMeters / WALK_SPEED_M_PER_MIN) * WALK_PREFERENCE_EXTRA
        );
    }

    function addRaptorOnboardState(nodeLabels, queue, state) {
        const nodeKey = raptorOnboardNodeKey(state);
        let bucket = nodeLabels.get(nodeKey) || [];

        if (bucket.some((item) => raptorOnboardDominates(item, state))) {
            return false;
        }

        bucket = bucket.filter((item) => !raptorOnboardDominates(state, item));

        const pathKey = raptorOnboardPathKey(state);
        const samePathIndex = bucket.findIndex((item) => raptorOnboardPathKey(item) === pathKey);

        if (samePathIndex >= 0) {
            const current = bucket[samePathIndex];

            if (raptorOnboardScore(current) <= raptorOnboardScore(state)) {
                return false;
            }

            bucket.splice(samePathIndex, 1);
        }

        bucket.push(state);
        bucket.sort((a, b) => raptorOnboardScore(a) - raptorOnboardScore(b));

        if (bucket.length > MAX_RAPTOR_ONBOARD_LABELS) {
            bucket = bucket.slice(0, MAX_RAPTOR_ONBOARD_LABELS);
        }

        if (!bucket.includes(state)) {
            nodeLabels.set(nodeKey, bucket);
            return false;
        }

        nodeLabels.set(nodeKey, bucket);
        queue.push(state);
        return true;
    }

    function* seedRaptorRoundSteps(boardSources, roundIndex) {
        const nodeLabels = new Map();
        const queue = [];

        let seededStops = 0;
        for (const [boardStopIndex, byLastRoute] of boardSources.entries()) {
            if (++seededStops % 8 === 0) yield null;
            const sources = flattenRaptorSources(byLastRoute);

            for (const source of sources) {
                for (const option of boardingOptionsAtStop(boardStopIndex)) {
                    if (source.lastRouteKey && source.lastRouteKey === option.routeKey) {
                        continue;
                    }

                    const boardingTime =
                        source.elapsedMinutes + boardingWaitForRoute(option.routeKey);
                    if (boardingTime > maxTravelMinutes + 0.00001) continue;
                    addRaptorOnboardState(nodeLabels, queue, {
                        routeKey: option.routeKey,
                        direction: option.direction,
                        pos: option.boardPos,
                        transfersUsed: roundIndex,
                        elapsedMinutes: boardingTime,
                        walkMeters: source.walkMeters,
                        initialWalk: source.initialWalk,
                        rideStops: source.rideStops,
                        transitMeters: source.transitMeters,
                        legs: source.legs,
                        currentLeg: initialRideLeg(
                            option.routeKey,
                            option.direction,
                            option.boardPos,
                        ),
                        transfers: source.transfers,
                    });
                }
            }
        }

        return { nodeLabels: nodeLabels, queue: queue };
    }

    function* scanRaptorRoundSteps(boardSources, roundIndex, onEvidence = null, onRideEdge = null) {
        const seeded = yield* seedRaptorRoundSteps(boardSources, roundIndex);
        const nodeLabels = seeded.nodeLabels;
        const queue = seeded.queue;
        const evidence = new Map();
        let head = 0;

        while (head < queue.length) {
            const state = queue[head++];
            if (head % 128 === 0) yield null;
            const nodeKey = raptorOnboardNodeKey(state);
            const currentBucket = nodeLabels.get(nodeKey);

            if (!currentBucket || !currentBucket.includes(state)) {
                continue;
            }
            if (state.elapsedMinutes > maxTravelMinutes + 0.00001) continue;

            const currentStopIndex = state.direction.stops[state.pos];

            if (
                onRideEdge &&
                state.pos > 0 &&
                state.currentLeg.rideStops > 0 &&
                state.elapsedMinutes <= maxTravelMinutes + 1e-7
            ) {
                onRideEdge(state.routeKey, state.direction, state.pos, roundIndex);
            }

            if (state.currentLeg.rideStops > 0 && stopAllowsAlighting(state.direction, state.pos)) {
                const candidate = itineraryFromRaptorState(state);
                updateSearchEvidence(evidence, currentStopIndex, candidate);
                if (onEvidence) onEvidence(currentStopIndex, candidate, roundIndex);
            }

            if (state.pos + 1 < state.direction.stops.length) {
                const metrics = routeEdgeMetrics(state.routeKey, state.direction, state.pos);

                if (!metrics) {
                    continue;
                }

                const nextPos = state.pos + 1;
                if (state.elapsedMinutes + metrics.minutes > maxTravelMinutes + 0.00001) continue;
                addRaptorOnboardState(nodeLabels, queue, {
                    ...state,
                    pos: nextPos,
                    elapsedMinutes: state.elapsedMinutes + metrics.minutes,
                    rideStops: state.rideStops + 1,
                    transitMeters: state.transitMeters + metrics.distance,
                    currentLeg: extendRideLeg(state.currentLeg, state.direction, nextPos),
                });
                continue;
            }

            const route = routes[state.routeKey];
            const continuations = routeDirectionContinuations(route);

            for (const next of continuations.get(relationKey(state.direction)) || []) {
                const nextDirection = next.direction;
                if (!nextDirection.stops || nextDirection.stops.length === 0) {
                    continue;
                }

                const gapMeters =
                    stopDistanceMeters(currentStopIndex, nextDirection.stops[0]) *
                    MODE_DISTANCE_FACTORS[route.mode || route.route];
                const gapMinutes =
                    gapMeters / routeSpeedMetersPerMin(state.routeKey) + next.terminalMinutes;
                if (state.elapsedMinutes + gapMinutes > maxTravelMinutes + 0.00001) continue;
                addRaptorOnboardState(nodeLabels, queue, {
                    ...state,
                    direction: nextDirection,
                    pos: 0,
                    elapsedMinutes: state.elapsedMinutes + gapMinutes,
                    transitMeters: state.transitMeters + gapMeters,
                    currentLeg: continueRideLeg(
                        state.currentLeg,
                        nextDirection,
                        next.terminalMinutes,
                    ),
                });
            }
        }

        return evidence;
    }

    function scanRaptorRound(boardSources, roundIndex) {
        const iterator = scanRaptorRoundSteps(boardSources, roundIndex);
        while (true) {
            const next = iterator.next();
            if (next.done) return next.value;
        }
    }

    // scheduler.yield() is available in Chromium, but not in Safari.
    // Safari's setTimeout(0) fallback can be clamped to ~4 ms per yield, so use
    // MessageChannel tasks to let the browser process input without timer clamping.
    let yieldMessageChannel = null;
    const yieldResolvers = [];
    function eventLoopYield() {
        if (globalThis.scheduler && typeof globalThis.scheduler.yield === 'function') {
            return globalThis.scheduler.yield();
        }
        if (typeof MessageChannel !== 'undefined') {
            if (!yieldMessageChannel) {
                yieldMessageChannel = new MessageChannel();
                yieldMessageChannel.port1.onmessage = () => {
                    const resolve = yieldResolvers.shift();
                    if (resolve) resolve();
                };
            }
            return new Promise((resolve) => {
                yieldResolvers.push(resolve);
                yieldMessageChannel.port2.postMessage(0);
            });
        }
        return new Promise((resolve) => setTimeout(resolve, 0));
    }

    async function scanRaptorRoundAsync(boardSources, roundIndex, token, onEvidence, onRideEdge) {
        const iterator = scanRaptorRoundSteps(boardSources, roundIndex, onEvidence, onRideEdge);
        let start = performance.now();
        while (true) {
            if (token !== renderGeneration) return null;
            const cpuStart = performance.now();
            const next = iterator.next();
            if (runProfile) {
                runProfile.cpu.raptor = (runProfile.cpu.raptor || 0) + performance.now() - cpuStart;
            }
            if (next.done) return next.value;
            if (performance.now() - start >= 7) {
                await eventLoopYield();
                start = performance.now();
            }
        }
    }

    function bestRaptorTransferCandidates(byPath) {
        const byLastRoute = new Map();

        for (const candidate of flattenEvidence(byPath)) {
            const legs = candidateLegs(candidate);
            const lastLeg = legs[legs.length - 1];

            if (!lastLeg) {
                continue;
            }

            const routeKey = lastLeg.routeKey;
            let bucket = byLastRoute.get(routeKey) || [];

            if (bucket.some((item) => itineraryDominates(item, candidate))) {
                continue;
            }

            bucket = bucket.filter((item) => !itineraryDominates(candidate, item));
            bucket.push(candidate);
            bucket.sort(compareItineraries);

            if (bucket.length > MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE) {
                bucket = bucket.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE);
            }

            byLastRoute.set(routeKey, bucket);
        }

        const result = [];

        for (const bucket of byLastRoute.values()) {
            result.push(...bucket);
        }

        result.sort(compareItineraries);
        return result.slice(0, MAX_RAPTOR_TRANSFER_LABELS_PER_STOP);
    }

    const transferStats = { arcsScanned: 0, cpuMs: 0, steps: 0 };

    function* buildRaptorTransferSourcesSteps(evidence) {
        const result = new Map();
        // Check enabled routes once per round, not for every transfer edge.
        const canBoard = new Uint8Array(stops.length);
        for (let index = 0; index < stops.length; ++index) {
            canBoard[index] = stops[index].routes.some(isRouteEnabled) ? 1 : 0;
        }

        let scanned = 0;
        for (const [alightStopIndex, byPath] of evidence.entries()) {
            if (++scanned % 16 === 0) yield null;
            const candidates = bestRaptorTransferCandidates(byPath);

            for (const candidate of candidates) {
                prepareItineraryMetrics(candidate);
                if (candidate.totalMinutes > maxTravelMinutes + 0.00001) continue;
                const legs = candidateLegs(candidate);
                const lastLeg = legs[legs.length - 1];
                if (!lastLeg) continue;
                const transfers = candidateTransfers(candidate);
                const offsets = walkTransfers.offsets;
                for (
                    let edge = offsets[alightStopIndex];
                    edge < offsets[alightStopIndex + 1];
                    ++edge
                ) {
                    transferStats.arcsScanned++;
                    const boardStopIndex = walkTransfers.to[edge];
                    const transferWalk = walkTransfers.meters[edge];
                    if (transferWalk > walkRadius + 0.00001) continue;
                    if (!canBoard[boardStopIndex]) continue;
                    if ((edge & 255) === 0) yield null;
                    const arrival = candidate.totalMinutes + transferWalk / WALK_SPEED_M_PER_MIN;
                    if (arrival > maxTravelMinutes + 0.00001) continue;
                    const source = {
                        elapsedMinutes: arrival,
                        walkMeters: candidate.walkMeters + transferWalk,
                        initialWalk: candidate.initialWalk,
                        rideStops: candidate.rideStops,
                        transitMeters: candidate.transitMeters,
                        legs: legs,
                        transfers: transfers.concat([
                            {
                                alightStopIndex: alightStopIndex,
                                boardStopIndex: boardStopIndex,
                                walkMeters: transferWalk,
                            },
                        ]),
                        lastRouteKey: lastLeg.routeKey,
                    };
                    addRaptorTransferSource(result, boardStopIndex, source);
                }
            }
        }

        return result;
    }

    function buildRaptorTransferSources(evidence) {
        const iterator = buildRaptorTransferSourcesSteps(evidence);
        while (true) {
            const next = iterator.next();
            if (next.done) return next.value;
        }
    }

    async function buildRaptorTransferSourcesAsync(evidence, token) {
        const iterator = buildRaptorTransferSourcesSteps(evidence);
        let started = performance.now();
        while (true) {
            if (token !== renderGeneration) return null;
            const stepStart = performance.now();
            const next = iterator.next();
            transferStats.cpuMs += performance.now() - stepStart;
            transferStats.steps++;
            if (next.done) return next.value;
            if (performance.now() - started >= 7) {
                await eventLoopYield();
                started = performance.now();
            }
        }
    }

    function createInitialRaptorSources(nearbyStops) {
        const result = new Map();

        for (const boardStopIndex of nearbyStops) {
            const initialWalk = pointToStopDistanceMeters(selectedPoint, boardStopIndex);

            const arrival = initialWalk / WALK_SPEED_M_PER_MIN;
            if (arrival > maxTravelMinutes + 0.00001) continue;
            addRaptorTransferSource(result, boardStopIndex, {
                elapsedMinutes: arrival,
                walkMeters: initialWalk,
                initialWalk: initialWalk,
                rideStops: 0,
                transitMeters: 0,
                legs: [],
                transfers: [],
                lastRouteKey: null,
            });
        }

        return result;
    }

    function createReachabilitySearch() {
        const nearbyStops = findNearbyStops(selectedPoint.lat, selectedPoint.lon);

        return {
            nearbyStops: nearbyStops,
            evidence: [new Map(), new Map(), new Map()],
            boardSources: createInitialRaptorSources(nearbyStops),
            builtRounds: 0,
        };
    }

    function ensureReachabilitySearch() {
        const cacheKey = reachabilitySearchKey();
        let context = reachabilitySearchCache.get(cacheKey);

        if (!context) {
            context = createReachabilitySearch();
            reachabilitySearchCache.set(cacheKey, context);

            if (reachabilitySearchCache.size > 4) {
                const firstKey = reachabilitySearchCache.keys().next().value;
                reachabilitySearchCache.delete(firstKey);
            }
        }

        const requiredRounds = Math.min(RAPTOR_ROUNDS, maxTransfers + 1);

        while (context.builtRounds < requiredRounds) {
            const round = context.builtRounds;

            if (!context.boardSources || context.boardSources.size === 0) {
                context.builtRounds = RAPTOR_ROUNDS;
                break;
            }

            context.evidence[round] = scanRaptorRound(context.boardSources, round);
            ++context.builtRounds;

            if (context.builtRounds < RAPTOR_ROUNDS) {
                context.boardSources = buildRaptorTransferSources(context.evidence[round]);
            }
        }

        return context;
    }

    async function ensureReachabilitySearchAsync(token, onEvidence, onRideEdge) {
        const cacheKey = reachabilitySearchKey();
        let context = reachabilitySearchCache.get(cacheKey);
        if (!context) {
            profilePhaseStart('Walking from start point');
            if (!(await warmWalkingPoint(selectedPoint, token))) return false;
            profilePhaseEnd('Walking from start point');
            context = createReachabilitySearch();
            reachabilitySearchCache.set(cacheKey, context);
            console.info('[map] Reachability cache MISS');
        } else {
            console.info('[map] Reachability cache HIT; rounds:', context.builtRounds);
        }
        const requiredRounds = Math.min(RAPTOR_ROUNDS, maxTransfers + 1);
        if (onEvidence) {
            let replayStarted = performance.now();
            for (let round = 0; round < Math.min(context.builtRounds, requiredRounds); ++round) {
                for (const [stopIndex, byPath] of context.evidence[round]) {
                    for (const candidate of flattenEvidence(byPath)) {
                        if (token !== renderGeneration) return false;
                        onEvidence(stopIndex, candidate, round, true);
                    }
                    if (performance.now() - replayStarted >= 4) {
                        await eventLoopYield();
                        replayStarted = performance.now();
                    }
                }
            }
        }
        while (context.builtRounds < requiredRounds) {
            if (token !== renderGeneration) return false;
            const round = context.builtRounds;
            if (!context.boardSources || !context.boardSources.size) {
                context.builtRounds = RAPTOR_ROUNDS;
                break;
            }
            const roundName = 'RAPTOR round ' + round;
            profilePhaseStart(roundName);
            const evidence = await scanRaptorRoundAsync(
                context.boardSources,
                round,
                token,
                onEvidence,
                onRideEdge,
            );
            profilePhaseEnd(roundName, 'evidence stops: ' + (evidence ? evidence.size : 0));
            if (token !== renderGeneration || evidence === null) return false;
            context.evidence[round] = evidence;
            ++context.builtRounds;
            if (context.builtRounds < RAPTOR_ROUNDS) {
                const transferName = 'Walking transfers after round ' + round;
                profilePhaseStart(transferName);
                context.boardSources = await buildRaptorTransferSourcesAsync(evidence, token);
                profilePhaseEnd(
                    transferName,
                    'boarding stops: ' + (context.boardSources ? context.boardSources.size : 0),
                );
                if (!context.boardSources) return false;
            }
        }
        return token === renderGeneration;
    }

    function filterEvidenceByTravelLimit(evidence) {
        if (!Number.isFinite(maxTravelMinutes)) {
            return evidence;
        }

        const result = new Map();

        for (const [stopIndex, byPath] of evidence.entries()) {
            const filteredPaths = new Map();

            for (const [pathKey, bucket] of byPath.entries()) {
                const filtered = bucket.filter(
                    (candidate) => candidate.totalMinutes <= maxTravelMinutes + 1e-7,
                );

                if (filtered.length > 0) {
                    filteredPaths.set(pathKey, filtered);
                }
            }

            if (filteredPaths.size > 0) {
                result.set(stopIndex, filteredPaths);
            }
        }

        return result;
    }

    function* analyzeSelectionSteps(skipGeometry = false) {
        const key = selectionKey();

        if (
            key === analysisCacheKey &&
            analysisCache &&
            (skipGeometry || analysisCache.includesGeometry)
        ) {
            return analysisCache;
        }

        const search = ensureReachabilitySearch();
        const nearbyStops = search.nearbyStops;
        const directEvidence = filterEvidenceByTravelLimit(search.evidence[0]);
        const transferEvidence =
            maxTransfers >= 1 ? filterEvidenceByTravelLimit(search.evidence[1]) : new Map();
        const twoTransferEvidence =
            maxTransfers >= 2 ? filterEvidenceByTravelLimit(search.evidence[2]) : new Map();
        const directRoutes = new Set();
        const directDirectionIds = new Set();

        let analyzed = 0;
        for (const paths of directEvidence.values()) {
            if (++analyzed % 32 === 0) yield null;
            for (const path of flattenEvidence(paths)) {
                directRoutes.add(path.leg1.routeKey);
                directDirectionIds.add(relationKey(path.leg1.direction));
            }
        }

        const directStops = new Set(directEvidence.keys());
        const directTransitStops = new Set(directStops);
        const transferBoardingStops = new Set();
        const transferRoutes = new Set();
        const transferDirectionIds = new Set();
        const transferTransitStops = new Set(transferEvidence.keys());
        const transferOnlyStops = new Set();

        for (const [stopIndex, paths] of transferEvidence.entries()) {
            if (++analyzed % 32 === 0) yield null;
            if (!directStops.has(stopIndex)) {
                transferOnlyStops.add(stopIndex);
            }

            for (const path of flattenEvidence(paths)) {
                transferBoardingStops.add(path.transferBoardStopIndex);
                transferRoutes.add(path.leg2.routeKey);
                transferDirectionIds.add(relationKey(path.leg2.direction));
            }
        }

        const twoTransferBoardingStops = new Set();
        const twoTransferRoutes = new Set();
        const twoTransferDirectionIds = new Set();
        const twoTransferTransitStops = new Set(twoTransferEvidence.keys());
        const twoTransferOnlyStops = new Set();

        for (const [stopIndex, paths] of twoTransferEvidence.entries()) {
            if (++analyzed % 32 === 0) yield null;
            if (!directStops.has(stopIndex) && !transferEvidence.has(stopIndex)) {
                twoTransferOnlyStops.add(stopIndex);
            }

            for (const path of flattenEvidence(paths)) {
                twoTransferBoardingStops.add(path.secondTransferBoardStopIndex);
                twoTransferRoutes.add(path.leg3.routeKey);
                twoTransferDirectionIds.add(relationKey(path.leg3.direction));
            }
        }

        // The progressive route and walking canvases already contain the
        // geometry. Do not rebuild expensive route segments during finalization.
        const emptyData = () => ({
            segmentsByRoute: new Map(),
            stopsByRoute: new Map(),
        });
        const directRouteData = skipGeometry
            ? emptyData()
            : yield* collectReachableRouteDataSteps(directEvidence, 'leg1');
        const transferRouteData = skipGeometry
            ? emptyData()
            : yield* collectReachableRouteDataSteps(transferEvidence, 'leg2');
        const twoTransferRouteData = skipGeometry
            ? emptyData()
            : yield* collectReachableRouteDataSteps(twoTransferEvidence, 'leg3');
        const directArrivalMinutes = skipGeometry
            ? new Map()
            : yield* collectBestArrivalMinutesSteps(directEvidence);
        const transferArrivalMinutes = skipGeometry
            ? new Map()
            : yield* collectBestArrivalMinutesSteps(transferEvidence);
        const twoTransferArrivalMinutes = skipGeometry
            ? new Map()
            : yield* collectBestArrivalMinutesSteps(twoTransferEvidence);

        analysisCacheKey = key;
        analysisCache = {
            includesGeometry: !skipGeometry,
            nearbyStops: nearbyStops,
            directRoutes: directRoutes,
            directDirectionIds: directDirectionIds,
            directTransitStops: directTransitStops,
            directStops: directStops,
            directEvidence: directEvidence,
            directRouteSegments: directRouteData.segmentsByRoute,
            directRouteStops: directRouteData.stopsByRoute,
            directArrivalMinutes: directArrivalMinutes,
            transferBoardingStops: transferBoardingStops,
            transferRoutes: transferRoutes,
            transferDirectionIds: transferDirectionIds,
            transferTransitStops: transferTransitStops,
            transferOnlyStops: transferOnlyStops,
            transferEvidence: transferEvidence,
            transferRouteSegments: transferRouteData.segmentsByRoute,
            transferRouteStops: transferRouteData.stopsByRoute,
            transferArrivalMinutes: transferArrivalMinutes,
            twoTransferBoardingStops: twoTransferBoardingStops,
            twoTransferRoutes: twoTransferRoutes,
            twoTransferDirectionIds: twoTransferDirectionIds,
            twoTransferTransitStops: twoTransferTransitStops,
            twoTransferOnlyStops: twoTransferOnlyStops,
            twoTransferEvidence: twoTransferEvidence,
            twoTransferRouteSegments: twoTransferRouteData.segmentsByRoute,
            twoTransferRouteStops: twoTransferRouteData.stopsByRoute,
            twoTransferArrivalMinutes: twoTransferArrivalMinutes,
        };

        return analysisCache;
    }

    // Keep synchronous API for individual trip lookups; map refresh is async.
    function drainSteps(iterator) {
        while (true) {
            const next = iterator.next();
            if (next.done) return next.value;
        }
    }

    function analyzeSelection() {
        return drainSteps(analyzeSelectionSteps());
    }

    async function analyzeSelectionAsync(token, skipGeometry = false) {
        const iterator = analyzeSelectionSteps(skipGeometry);
        let started = performance.now();
        while (true) {
            if (token !== renderGeneration) return null;
            const next = iterator.next();
            if (next.done) return next.value;
            if (performance.now() - started >= 3) {
                await eventLoopYield();
                started = performance.now();
            }
        }
    }

    async function appendRouteGroup(container, title, routeKeys, transferCount, token) {
        if (routeKeys.size === 0) {
            return;
        }

        const heading = document.createElement('div');
        heading.className = 'route-group-title';

        if (transferCount === 0) {
            heading.classList.add('route-group-title-direct');
        }

        heading.textContent = title;
        container.appendChild(heading);

        const ordered = Array.from(routeKeys);

        ordered.sort((a, b) => {
            const ar = routes[a];
            const br = routes[b];
            const aa = ar && ar.ref ? ar.ref : '';
            const bb = br && br.ref ? br.ref : '';

            return aa.localeCompare(bb, undefined, {
                numeric: true,
            });
        });

        let processedChips = 0;
        for (const key of ordered) {
            if (++processedChips % 32 === 0) {
                await eventLoopYield();
                if (token !== renderGeneration) return;
            }
            const route = routes[key];

            if (!route) {
                continue;
            }

            const item = document.createElement('span');
            item.className = 'route-chip';
            item.style.background = routeColor(key);
            item.style.opacity = ['1', '0.65', '0.5'][transferCount];
            item.textContent = routeLabel(route);
            item.style.cursor = 'pointer';

            if (
                selectedRouteFocus &&
                selectedRouteFocus.routeKey === key &&
                selectedRouteFocus.transferCount === transferCount
            ) {
                item.style.outline = '3px solid #111';
                item.style.outlineOffset = '2px';
            }

            item.addEventListener('click', () => {
                const same =
                    selectedRouteFocus &&
                    selectedRouteFocus.routeKey === key &&
                    selectedRouteFocus.transferCount === transferCount;

                selectedRouteFocus = same ? null : { routeKey: key, transferCount: transferCount };
                scheduleRender();
            });

            container.appendChild(item);
        }
    }

    function focusedRouteStops(analysis) {
        if (!selectedRouteFocus) {
            return null;
        }

        const stopMaps = [
            analysis.directRouteStops,
            analysis.transferRouteStops,
            analysis.twoTransferRouteStops,
        ];
        const routeStops = stopMaps[selectedRouteFocus.transferCount].get(
            selectedRouteFocus.routeKey,
        );

        if (!routeStops) {
            selectedRouteFocus = null;
            return null;
        }

        return routeStops;
    }

    function isFocusedRoute(routeKey, transferCount) {
        return (
            selectedRouteFocus &&
            selectedRouteFocus.routeKey === routeKey &&
            selectedRouteFocus.transferCount === transferCount
        );
    }

    function drawFocusedRouteStops(analysis) {
        const routeStops = focusedRouteStops(analysis);

        if (!routeStops || !selectedRouteFocus) {
            return;
        }

        const color = routeColor(selectedRouteFocus.routeKey);

        for (const stopIndex of routeStops) {
            const stop = stops[stopIndex];
            const marker = L.circleMarker([stop.lat, stop.lon], {
                radius: 7,
                weight: 3,
                color: color,
                fillColor: '#fff',
                fillOpacity: 1,
                bubblingMouseEvents: false,
                pane: 'stopPane',
            });

            marker.bindPopup(
                '<b>' +
                    escapeHtml(stop.name || TEXT.stop) +
                    '</b><br>' +
                    escapeHtml(routeLabel(routes[selectedRouteFocus.routeKey])) +
                    "<br><span style='color:#666'>" +
                    TEXT.doubleClick +
                    '</span>',
            );

            marker.on('click', (event) => {
                suppressMapClickUntil = performance.now() + 600;

                if (event.originalEvent) {
                    L.DomEvent.stop(event.originalEvent);
                }
            });

            marker.on('dblclick', (event) => {
                suppressMapClickUntil = performance.now() + 600;

                if (event.originalEvent) {
                    L.DomEvent.stop(event.originalEvent);
                }

                marker.closePopup();
                showTripToStop(stopIndex);
            });

            marker.addTo(stopLayer);
        }
    }

    async function updateRouteList(directRoutes, transferRoutes, twoTransferRoutes, token) {
        const container = document.getElementById('routes');
        container.innerHTML = '';

        await appendRouteGroup(container, TEXT.groupDirect, directRoutes, 0, token);

        if (maxTransfers >= 1) {
            await appendRouteGroup(container, TEXT.groupTransfer, transferRoutes, 1, token);
        }

        if (maxTransfers >= 2) {
            await appendRouteGroup(container, TEXT.groupTwoTransfers, twoTransferRoutes, 2, token);
        }
    }

    async function renderSelection(
        progressive = false,
        token = renderGeneration,
        preserveWave = false,
    ) {
        if (!preserveWave) clearResult();
        updateControls();

        if (!selectedPoint) {
            return;
        }

        analysisSummary.hidden = false;
        tripPanel.hidden = true;

        if (!preserveWave) {
            drawOrigin(selectedPoint.lat, selectedPoint.lon, !progressive);
        }

        const skipGeometry = progressive && preserveWave && !selectedRouteFocus;
        const analysis = await analyzeSelectionAsync(token, skipGeometry);
        if (!analysis || token !== renderGeneration) return;
        const focusStops = focusedRouteStops(analysis);
        const hasFocus = !!selectedRouteFocus && !!focusStops;
        if (hasFocus) clearProgressiveRoutes();

        if (maxTransfers >= 2) {
            let paintedtwoTransferRoutes = 0;
            for (const routeKey of progressive && preserveWave && !hasFocus
                ? []
                : analysis.twoTransferRoutes) {
                if (++paintedtwoTransferRoutes % 4 === 0) {
                    await eventLoopYield();
                    if (token !== renderGeneration) return;
                }
                if (hasFocus && !isFocusedRoute(routeKey, 2)) {
                    continue;
                }

                drawRoute(routeKey, 2, analysis.twoTransferRouteSegments.get(routeKey));
            }

            if (!hasFocus) {
                let placedtwoTransferOnlyStops = 0;
                for (const stopIndex of analysis.twoTransferOnlyStops) {
                    if (++placedtwoTransferOnlyStops % 64 === 0) {
                        await eventLoopYield();
                        if (token !== renderGeneration) return;
                    }
                    if (!preserveWave) {
                        drawCoverage(
                            stops[stopIndex],
                            2,
                            analysis.twoTransferArrivalMinutes.get(stopIndex),
                        );
                    }
                    if (progressive && preserveWave) {
                        queueProgressiveStop(stopIndex, 2, token);
                    } else {
                        drawStop(stopIndex, 2);
                    }
                }
            }
        }

        if (maxTransfers >= 1) {
            let paintedtransferRoutes = 0;
            for (const routeKey of progressive && preserveWave && !hasFocus
                ? []
                : analysis.transferRoutes) {
                if (++paintedtransferRoutes % 4 === 0) {
                    await eventLoopYield();
                    if (token !== renderGeneration) return;
                }
                if (hasFocus && !isFocusedRoute(routeKey, 1)) {
                    continue;
                }

                drawRoute(routeKey, 1, analysis.transferRouteSegments.get(routeKey));
            }

            if (!hasFocus) {
                let placedtransferOnlyStops = 0;
                for (const stopIndex of analysis.transferOnlyStops) {
                    if (++placedtransferOnlyStops % 64 === 0) {
                        await eventLoopYield();
                        if (token !== renderGeneration) return;
                    }
                    if (!preserveWave) {
                        drawCoverage(
                            stops[stopIndex],
                            1,
                            analysis.transferArrivalMinutes.get(stopIndex),
                        );
                    }
                    if (progressive && preserveWave) {
                        queueProgressiveStop(stopIndex, 1, token);
                    } else {
                        drawStop(stopIndex, 1);
                    }
                }
            }
        }

        let painteddirectRoutes = 0;
        for (const routeKey of progressive && preserveWave && !hasFocus
            ? []
            : analysis.directRoutes) {
            if (++painteddirectRoutes % 4 === 0) {
                await eventLoopYield();
                if (token !== renderGeneration) return;
            }
            if (hasFocus && !isFocusedRoute(routeKey, 0)) {
                continue;
            }

            drawRoute(routeKey, 0, analysis.directRouteSegments.get(routeKey));
        }

        if (!hasFocus) {
            let placeddirectStops = 0;
            for (const stopIndex of analysis.directStops) {
                if (++placeddirectStops % 64 === 0) {
                    await eventLoopYield();
                    if (token !== renderGeneration) return;
                }
                if (!preserveWave) {
                    drawCoverage(stops[stopIndex], 0, analysis.directArrivalMinutes.get(stopIndex));
                }
                if (progressive && preserveWave) {
                    queueProgressiveStop(stopIndex, 0, token);
                } else {
                    drawStop(stopIndex, 0);
                }
            }
        }

        if (!hasFocus) {
            if (progressive && !preserveWave) startProgressiveCoverage(token);
            else if (!progressive) flushCoverage();
        }

        document.getElementById('nearby-count').textContent = analysis.nearbyStops.length;

        document.getElementById('direct-route-count').textContent = analysis.directRoutes.size;

        document.getElementById('direct-stop-count').textContent = analysis.directStops.size;

        document.getElementById('transfer-route-count').textContent =
            maxTransfers >= 1 ? analysis.transferRoutes.size : 0;

        document.getElementById('transfer-stop-count').textContent =
            maxTransfers >= 1 ? analysis.transferOnlyStops.size : 0;

        document.getElementById('two-transfer-route-count').textContent =
            maxTransfers >= 2 ? analysis.twoTransferRoutes.size : 0;

        document.getElementById('two-transfer-stop-count').textContent =
            maxTransfers >= 2 ? analysis.twoTransferOnlyStops.size : 0;

        if (hasFocus) {
            drawFocusedRouteStops(analysis);
        }

        await updateRouteList(
            analysis.directRoutes,
            analysis.transferRoutes,
            analysis.twoTransferRoutes,
            token,
        );
    }

    function scheduleRender() {
        profileAbort('new settings or point');
        updateControls();
        const token = ++renderGeneration;
        if (renderFrame !== null) cancelAnimationFrame(renderFrame);
        if (!selectedPoint) return;
        profileBegin(token);
        profilePhaseStart('Clear previous results');
        clearResult();
        document.getElementById('routes').replaceChildren();
        for (const id of [
            'nearby-count',
            'direct-route-count',
            'direct-stop-count',
            'transfer-route-count',
            'transfer-stop-count',
            'two-transfer-route-count',
            'two-transfer-stop-count',
        ]) {
            document.getElementById(id).textContent = '0';
        }
        profilePhaseEnd('Clear previous results');
        renderFrame = requestAnimationFrame(async () => {
            renderFrame = null;
            try {
                if (token !== renderGeneration) return;
                if (targetPoint) {
                    renderTrip(targetPoint);
                    return;
                }
                // Show the origin first; build the wave indexes without blocking the UI.
                drawOrigin(selectedPoint.lat, selectedPoint.lon, false);
                analysisSummary.hidden = false;
                tripPanel.hidden = true;
                const started = performance.now();
                const walkBefore = { ...walkingStats };
                const transferBefore = { ...transferStats };
                // Only the spatial index is needed. Do not construct an unused
                // 2 * edges reverse-lookup index (tens of MB on the Moscow graph).
                profilePhaseStart('Walking snap index');
                if (!(await warmWalkSegmentGrid(token))) return;
                profilePhaseEnd('Walking snap index');
                if (token !== renderGeneration) return;
                profilePhaseStart('Start coverage wave');
                const wave = startProgressiveCoverage(token, true);
                if (runProfile) runProfile.wave = wave;
                positionRouteCanvases();
                profilePhaseEnd('Start coverage wave');
                const onEvidence = (stopIndex, candidate, tier, cached = false) => {
                    if (token !== renderGeneration) return;
                    if (runProfile) runProfile.evidenceEvents++;
                    if (candidate.totalMinutes <= maxTravelMinutes + 0.0001) {
                        wave.addSource(tier, stopIndex, candidate.totalMinutes);
                        queueProgressiveStop(stopIndex, tier, token);
                        if (cached) queueProgressiveCandidateLeg(candidate, tier, token);
                    }
                };
                const onRideEdge = (routeKey, direction, pos, tier) => {
                    queueProgressiveRouteEdge(routeKey, direction, pos, tier, token);
                };
                profilePhaseStart('Total transport routing');
                if (!(await ensureReachabilitySearchAsync(token, onEvidence, onRideEdge))) return;
                profilePhaseEnd('Total transport routing');
                if (token !== renderGeneration) return;
                // RAPTOR takes less than a second. Seed one complete multi-source
                // walk search to avoid revisiting millions of vertices for late stops.
                profilePhaseStart('Seed final walking wave');
                wave.begin();
                profilePhaseEnd('Seed final walking wave');
                const searchMs = performance.now() - started;
                profilePhaseStart('Final result analysis and DOM');
                await renderSelection(true, token, true);
                profilePhaseEnd('Final result analysis and DOM');
                if (token !== renderGeneration) return;
                console.info('Reachability search complete (painting may continue):', {
                    search: Math.round(searchMs),
                    finalize: Math.round(performance.now() - started - searchMs),
                    stopCount: displayedStopIndexes.size,
                    transitEdges: routeEdgeRecords.map((items) => items.length),
                    firstTransitPaint:
                        routeFirstPaintMs === null ? null : Math.round(routeFirstPaintMs - started),
                    transferArcs: transferStats.arcsScanned - transferBefore.arcsScanned,
                    transferCpuMs: Math.round(transferStats.cpuMs - transferBefore.cpuMs),
                    walkingQueries: walkingStats.queries - walkBefore.queries,
                    walkingCpuMs: Math.round(walkingStats.cpuMs - walkBefore.cpuMs),
                    walkingYields: walkingStats.yields - walkBefore.yields,
                    walkingYieldMs: Math.round(walkingStats.yieldMs - walkBefore.yieldMs),
                });
                const p = runProfile;
                if (p && p.token === token) {
                    p.resultsReadyMs = profileElapsed(p);
                    p.searchDone = true;
                    p.currentPhase = 'Finishing Canvas';
                    p.cpu.walkingDijkstra = walkingStats.cpuMs - walkBefore.cpuMs;
                    p.cpu.walkingYieldsMs = walkingStats.yieldMs - walkBefore.yieldMs;
                    p.cpu.transferCompute = transferStats.cpuMs - transferBefore.cpuMs;
                    p.frames.transferArcs = transferStats.arcsScanned - transferBefore.arcsScanned;
                    console.info('[map] ROUTING COMPLETE; waiting for Canvas', profileSnapshot(p));
                    profileUpdateStatus(p);
                    profileFinish(p);
                }
            } catch (error) {
                console.error('[map] RUN FAILED', error);
                if (runProfile && runProfile.token === token) {
                    profileAbort('error: ' + error.message);
                }
            }
        });
    }

    for (const input of modeInputs) {
        input.addEventListener('change', () => {
            enabledModes = selectedTransportModes();
            boardingOptionsCache.clear();
            reachabilitySearchCache.clear();
            itineraryCache.clear();
            analysisCacheKey = null;
            analysisCache = null;
            selectedRouteFocus = null;
            tripAlternativeIndex = 0;
            scheduleRender();
        });
    }

    walkTimeInput.addEventListener('input', scheduleRender);

    transferCountInput.addEventListener('input', scheduleRender);

    travelTimeLimitInput.addEventListener('input', scheduleRender);

    map.on('zoomstart', () => {
        suspendRoutesForZoom();
        stopCanvasZooming = true;
        cancelStopCanvasRedraw();
        stopCanvas.style.visibility = 'hidden';
        clearStopPixels();
    });
    map.on('zoomend', () => {
        resumeRoutesAfterZoom();
        stopCanvasZooming = false;
        redrawStopCanvas();
        stopCanvas.style.visibility = 'visible';
    });
    map.on('moveend', () => {
        if (!stopCanvasZooming) redrawStopCanvas();
        scheduleRouteViewportRedraw();
    });
    map.on('resize', () => {
        redrawStopCanvas();
        scheduleRouteViewportRedraw();
    });
    map.on('zoomstart', onCoverageZoomStart);
    map.on('zoomend', onCoverageZoomEnd);
    map.getContainer().addEventListener('wheel', onCoverageWheel, { passive: true });

    // Pan and resize reuse cached geometry; zoom never shows stale pixels.
    map.on('moveend', requestCoverageViewportRedraw);
    map.on('resize', requestCoverageViewportRedraw);

    circleOpacityInput.addEventListener('input', () => {
        circleOpacity = Number(circleOpacityInput.value) / 100.0;
        circleOpacityValue.textContent = Math.round(circleOpacity * 100) + '%';
        coverageCanvas.style.opacity = String(circleOpacity);
    });

    function backToReachability() {
        targetPoint = null;
        setTargetPickMode(false);
        tripAlternativeIndex = 0;
        scheduleRender();
    }

    tripBackTop.addEventListener('click', backToReachability);
    tripBack.addEventListener('click', backToReachability);

    function resetSelection() {
        profileAbort('Reset');
        timingStatus.textContent = 'Ready';
        ++renderGeneration;
        if (renderFrame !== null) cancelAnimationFrame(renderFrame);
        renderFrame = null;
        cancelPendingMapClick();
        map.closePopup();
        selectedPoint = null;
        targetPoint = null;
        selectedRouteFocus = null;
        tripAlternativeIndex = 0;
        analysisCacheKey = null;
        analysisCache = null;
        reachabilitySearchCache.clear();
        itineraryCache.clear();
        setTargetPickMode(false);
        clearResult();
        analysisSummary.hidden = true;
        tripPanel.hidden = true;
        tripAlternatives.replaceChildren();
        tripSteps.replaceChildren();
        document.getElementById('routes').replaceChildren();
        for (const id of [
            'nearby-count',
            'direct-route-count',
            'direct-stop-count',
            'transfer-route-count',
            'transfer-stop-count',
            'two-transfer-route-count',
            'two-transfer-stop-count',
        ]) {
            document.getElementById(id).textContent = '0';
        }
        resetOrigin.disabled = true;
        legendOriginText.textContent = 'Choose a starting point';
    }

    resetOrigin.addEventListener('click', resetSelection);

    function selectStartPoint(latlng) {
        selectedPoint = {
            lat: latlng.lat,
            lon: latlng.lng,
        };
        resetOrigin.disabled = false;
        targetPoint = null;
        setTargetPickMode(false);
        tripAlternativeIndex = 0;
        selectedRouteFocus = null;

        analysisCacheKey = null;
        analysisCache = null;
        reachabilitySearchCache.clear();
        itineraryCache.clear();
        scheduleRender();
    }

    function cancelPendingMapClick() {
        if (pendingMapClickTimer === null) {
            return;
        }

        clearTimeout(pendingMapClickTimer);
        pendingMapClickTimer = null;
    }

    map.on('click', (event) => {
        if (targetPickMode && selectedPoint) {
            cancelPendingMapClick();
            suppressMapClickUntil = performance.now() + 600;
            showTripToPoint({
                lat: event.latlng.lat,
                lon: event.latlng.lng,
            });
            return;
        }

        const stopIndex = nearestDisplayedStop(event.latlng, 14);

        if (stopIndex !== null) {
            cancelPendingMapClick();
            suppressMapClickUntil = performance.now() + 600;
            openStopPopup(stopIndex);
            return;
        }

        if (performance.now() < suppressMapClickUntil) {
            return;
        }

        if (event.originalEvent && event.originalEvent._stopped) {
            return;
        }

        const latlng = {
            lat: event.latlng.lat,
            lng: event.latlng.lng,
        };

        cancelPendingMapClick();
        pendingMapClickTimer = setTimeout(() => {
            pendingMapClickTimer = null;
            selectStartPoint(latlng);
        }, MAP_DOUBLE_CLICK_DELAY_MS);
    });

    map.on('dblclick', (event) => {
        cancelPendingMapClick();
        suppressMapClickUntil = performance.now() + 600;

        if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent);
        }

        if (!selectedPoint) {
            selectStartPoint(event.latlng);
            return;
        }

        const stopIndex = nearestDisplayedStop(event.latlng, 14);
        if (stopIndex !== null) {
            showTripToStop(stopIndex);
            return;
        }

        tripAlternativeIndex = 0;
        showTripToPoint({
            lat: event.latlng.lat,
            lon: event.latlng.lng,
        });
    });

    updateControls();
    analysisSummary.hidden = true;
    bootPhase('LOADING MAP TILES', 'Waiting for vector map tiles to become visible...');
    await baseMapReady;
    console.info('[map] BASE MAP READY', Math.round(performance.now() - bootStarted), 'ms');
    console.info(
        '[map] BOOT COMPLETE',
        Math.round(performance.now() - bootStarted),
        'ms; ready to select origin',
    );
    bootComplete();
})().catch((error) => {
    console.error('Transport map initialization failed:', error);
    bootFailed(error);
    const container = document.getElementById('map');
    if (container) {
        container.textContent = 'Could not initialize map: ' + error.message;
        container.style.padding = '24px';
        container.style.boxSizing = 'border-box';
        container.style.color = '#b91c1c';
    }
});
