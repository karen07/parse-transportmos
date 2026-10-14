const TRANSPORT_DATA_BASE64 = [
@@PAYLOAD_LINES@@
].join("");

function loadExternalScript(url, isLoaded) {
    return new Promise((resolve, reject) => {
        if (isLoaded()) {
            resolve();
            return;
        }

        const script = document.createElement("script");
        script.src = url;
        script.onload = () => {
            if (isLoaded()) {
                resolve();
            } else {
                reject(new Error("Map library did not initialize: " + url));
            }
        };
        script.onerror = () => {
            reject(new Error("Failed to load map library: " + url));
        };
        document.head.appendChild(script);
    });
}

async function loadMapLibraries() {
    await loadExternalScript(
        "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
        () => !!window.L
    );
    await loadExternalScript(
        "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js",
        () => !!window.maplibregl
    );
    await loadExternalScript(
        "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.22/" +
            "leaflet-maplibre-gl.js",
        () => !!window.L.maplibreGL
    );
}

async function loadTransportData() {
    const binary = atob(TRANSPORT_DATA_BASE64);
    const bytes = new Uint8Array(binary.length);

    for (let i = 0; i < binary.length; ++i) {
        bytes[i] = binary.charCodeAt(i);
    }

    if (typeof DecompressionStream !== "function") {
        throw new Error(
            "This browser does not support DecompressionStream(gzip)."
        );
    }

    const stream = new Blob([bytes])
        .stream()
        .pipeThrough(new DecompressionStream("gzip"));

    const text = await new Response(stream).text();
    return JSON.parse(text);
}

(async () => {
await loadMapLibraries();
const data = await loadTransportData();

const WALK_SPEED_M_PER_MIN = @@WALK_SPEED@@;
const BUS_SPEED_M_PER_MIN = 320.0;
const TRAM_SPEED_M_PER_MIN = 300.0;
const TROLLEYBUS_SPEED_M_PER_MIN = 280.0;
const METRO_SPEED_M_PER_MIN = 630.0;
const MCC_SPEED_M_PER_MIN = 780.0;
const MCD_SPEED_M_PER_MIN = 900.0;
const STOP_DELAY_MIN = 0.35;
const BOARDING_WAIT_MIN = 4.0;
const MODE_SPEEDS = {
    bus: BUS_SPEED_M_PER_MIN,
    tram: TRAM_SPEED_M_PER_MIN,
    trolleybus: TROLLEYBUS_SPEED_M_PER_MIN,
    metro: METRO_SPEED_M_PER_MIN,
    mcc: MCC_SPEED_M_PER_MIN,
    mcd: MCD_SPEED_M_PER_MIN
};
const MODE_BOARDING_WAITS = {
    bus: 4.0, tram: 4.0, trolleybus: 4.0,
    metro: 2.5, mcc: 4.0, mcd: 5.0
};
const ROUGH_STOP_MIN = 1.4;
const WALK_PREFERENCE_EXTRA = 0.8;
const MAX_EVIDENCE_PER_PATH = 4;
const MAX_TRANSFER_EXTENSION_OPTIONS = 2;
const MAX_TRIP_ALTERNATIVES = 8;
const RAPTOR_ROUNDS = 3;
const MAX_RAPTOR_ONBOARD_LABELS = 4;
const MAX_RAPTOR_TRANSFER_LABELS_PER_ROUTE = 1;
const MAX_RAPTOR_TRANSFER_LABELS_PER_STOP = 8;
const REASONABLE_TIME_FACTOR = 1.5;
const REASONABLE_TIME_EXTRA_MIN = 10.0;
const GRID_SIZE = @@GRID_SIZE@@;
const EARTH_RADIUS_M = 6371000.0;

const ORIGIN_LON = @@ORIGIN_LON@@;
const ORIGIN_LAT = @@ORIGIN_LAT@@;
const ORIGIN_LAT_RAD = ORIGIN_LAT * Math.PI / 180.0;

const stops = data.stops;
const routes = data.routes;
const grid = data.grid;
const modeInputs = Array.from(document.querySelectorAll(".mode-input"));
let enabledModes = new Set(
    modeInputs.filter(input => input.checked).map(input => input.value)
);

function isRouteEnabled(routeKey) {
    const route = routes[routeKey];
    return !!route && enabledModes.has(route.mode || route.route);
}

function boardingWaitForRoute(routeKey) {
    const route = routes[routeKey];
    const mode = route ? (route.mode || route.route) : "bus";
    return MODE_BOARDING_WAITS[mode] ?? BOARDING_WAIT_MIN;
}

const TEXT = {
    stop: "Stop",
    stopGenitive: "stop",
    transfers: "Transfers",
    tripTitle: "Trip",
    backToReachability: "Back to reachability",
    noTransfers: "No transfers",
    oneTransfer: "One transfer",
    twoTransfers: "Two transfers",
    accessDirect: "Reachable: no transfers",
    accessTransfer: "Reachable: one transfer",
    accessTwoTransfers: "Reachable: two transfers",
    doubleClick: "Double-click: show trip",
    destination: "Destination",
    boarding: "Boarding",
    transferExit: "Transfer: exit",
    transferBoarding: "Transfer: boarding",
    groupDirect: "No transfers",
    groupTransfer: "After one transfer",
    groupTwoTransfers: "After two transfers",
    restoreFailedStart: "Could not restore a path to <b>",
    restoreFailedEnd: "</b> for the current walking radius and transfer mode.",
    walkPrefix: "Walk ~<b>",
    walkToStop: " min</b> to stop <b>",
    transportNotNeeded: "</b>. Transit is not needed.",
    rideStopsSuffix: " stops).",
    transferWalkPrefix: "Walk ~<b>",
    minutesColon: " min</b>: ",
    optionPrefix: "Option ",
    walkOnly: "walk",
    walkShort: "walk",
    minutesShort: "min",
    stopsShort: "stops",
};

const walkTimeInput = document.getElementById("walk-time");
const transferCountInput = document.getElementById("transfer-count");
const travelTimeLimitInput = document.getElementById("travel-time-limit");
const circleOpacityInput = document.getElementById("circle-opacity");
const walkTimeValue = document.getElementById("walk-time-value");
const transferModeValue = document.getElementById("transfer-mode-value");
const travelTimeLimitValue = document.getElementById(
    "travel-time-limit-value"
);
const transferLabel = document.getElementById("transfer-label");
const circleOpacityValue = document.getElementById("circle-opacity-value");
const legendOriginText = document.getElementById("legend-origin-text");
const analysisSummary = document.getElementById("analysis-summary");
const tripPanel = document.getElementById("trip-panel");
const tripTitle = document.getElementById("trip-title");
const tripAlternatives = document.getElementById("trip-alternatives");
const tripSteps = document.getElementById("trip-steps");
const tripBack = document.getElementById("trip-back");

const TRAVEL_TIME_INFINITY_SLIDER_VALUE = 185;
let walkMinutes = Number(walkTimeInput.value);
let walkRadius = walkMinutes * WALK_SPEED_M_PER_MIN;
let maxTransfers = Number(transferCountInput.value);
let maxTravelMinutes = 60;
let circleOpacity = Number(circleOpacityInput.value) / 100.0;
let selectedPoint = null;
let selectedRouteFocus = null;
let renderFrame = null;
let nearbyStopCache = new Map();
let analysisCacheKey = null;
let analysisCache = null;
let targetPoint = null;
let targetPickMode = false;
let tripAlternativeIndex = 0;
const MAP_DOUBLE_CLICK_DELAY_MS = 260;
let pendingMapClickTimer = null;
let suppressMapClickUntil = 0;
let displayedStopIndexes = new Set();
let stopMarkers = new Map();
const directionShapeCache = new WeakMap();
const routeContinuationCache = new WeakMap();
const routeLegTraversalCache = new Map();
const boardingOptionsCache = new Map();
const routeEdgeMetricsCache = new Map();
const reachabilitySearchCache = new Map();
const itineraryCache = new Map();
let travelRenderTimer = null;

const map = L.map(
    "map",
    {
        preferCanvas: true,
        minZoom: 1
    }
).setView(
    [ORIGIN_LAT, ORIGIN_LON],
    10
);

map.doubleClickZoom.disable();

function createMapPane(name, zIndex, pointerEvents) {
    const pane = map.createPane(name);
    pane.style.zIndex = String(zIndex);
    pane.style.pointerEvents = pointerEvents;
}

createMapPane("coveragePane", 410, "none");
createMapPane("routePane", 420, "none");
createMapPane("tripWalkPane", 430, "none");
createMapPane("tripRoutePane", 440, "none");
createMapPane("stopPane", 650, "auto");
createMapPane("tripMarkerPane", 660, "auto");
createMapPane("originPane", 700, "none");

const MODE_NAMES = {
    heading: @@MODE_HEADING@@,
    bus: @@MODE_BUS@@,
    tram: @@MODE_TRAM@@,
    trolleybus: @@MODE_TROLLEYBUS@@,
    metro: @@MODE_METRO@@,
    mcc: @@MODE_MCC@@,
    mcd: @@MODE_MCD@@
};
const MODE_HINT = @@MODE_HINT@@;

const modeHeading = document.getElementById("transport-modes-heading");
modeHeading.textContent = MODE_NAMES.heading;
document.querySelector("#transport-modes .mode-grid")
    .setAttribute("aria-label", MODE_NAMES.heading);

document.querySelectorAll("[data-mode-name]").forEach(element => {
    element.textContent = MODE_NAMES[element.dataset.modeName];
});
document.getElementById("transport-modes-hint").textContent = MODE_HINT;

const vectorMapLayer = L.maplibreGL({
    style: "https://tiles.openfreemap.org/styles/bright",
    attribution:
        '<a href="https://openfreemap.org/">OpenFreeMap</a> | ' +
        '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> | ' +
        '<a href="https://www.openstreetmap.org/copyright">' +
        'OpenStreetMap contributors</a>'
}).addTo(map);

const COVERAGE_SVG_NS = "http://www.w3.org/2000/svg";
const coverageSvg = document.createElementNS(COVERAGE_SVG_NS, "svg");
const coverageGroup = document.createElementNS(COVERAGE_SVG_NS, "g");
const coveragePaths = [0, 1, 2].map(() =>
    document.createElementNS(COVERAGE_SVG_NS, "path")
);

coverageSvg.style.position = "absolute";
coverageSvg.style.pointerEvents = "none";
coverageSvg.style.overflow = "visible";
coverageSvg.classList.add("leaflet-zoom-animated");
coverageSvg.appendChild(coverageGroup);

for (const transferCount of [2, 1, 0]) {
    const path = coveragePaths[transferCount];
    path.setAttribute("stroke", "none");
    path.setAttribute("fill-rule", "nonzero");
    coverageGroup.appendChild(path);
}

map.getPane("coveragePane").appendChild(coverageSvg);

const twoTransferRouteLayer = L.layerGroup().addTo(map);
const transferRouteLayer = L.layerGroup().addTo(map);
const directRouteLayer = L.layerGroup().addTo(map);
const stopLayer = L.layerGroup().addTo(map);
const originLayer = L.layerGroup().addTo(map);
const tripWalkLayer = L.layerGroup().addTo(map);
const tripRouteLayer = L.layerGroup().addTo(map);
const tripMarkerLayer = L.layerGroup().addTo(map);

function escapeHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

function project(lon, lat) {
    const x =
        EARTH_RADIUS_M *
        ((lon - ORIGIN_LON) * Math.PI / 180.0) *
        Math.cos(ORIGIN_LAT_RAD);

    const y =
        EARTH_RADIUS_M *
        ((lat - ORIGIN_LAT) * Math.PI / 180.0);

    return [x, y];
}

function routeColor(key) {
    let hash = 0;

    for (let i = 0; i < key.length; ++i) {
        hash = ((hash << 5) - hash) + key.charCodeAt(i);
        hash |= 0;
    }

    return "hsl(" +
        (Math.abs(hash) % 360) +
        ",70%,42%)";
}

function updateTargetControls() {
    map.getContainer().classList.toggle(
        "target-pick-mode",
        targetPickMode
    );
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
        rawTravelTimeLimit >= TRAVEL_TIME_INFINITY_SLIDER_VALUE
            ? Infinity
            : rawTravelTimeLimit;

    if (newWalkRadius !== walkRadius) {
        nearbyStopCache.clear();
        reachabilitySearchCache.clear();
        itineraryCache.clear();
    }

    if (
        newMaxTransfers !== maxTransfers ||
        newMaxTravelMinutes !== maxTravelMinutes
    ) {
        itineraryCache.clear();
    }

    walkMinutes = newWalkMinutes;
    walkRadius = newWalkRadius;
    maxTransfers = newMaxTransfers;
    maxTravelMinutes = newMaxTravelMinutes;
    circleOpacity = Number(circleOpacityInput.value) / 100.0;

    walkTimeValue.textContent =
        walkMinutes.toFixed(0) +
        " min (~" +
        Math.round(walkRadius) +
        " m)";

    transferLabel.textContent = TEXT.transfers;
    tripTitle.textContent = TEXT.tripTitle;
    tripBack.textContent = TEXT.backToReachability;

    const transferModeLabels = [
        TEXT.noTransfers,
        TEXT.oneTransfer,
        TEXT.twoTransfers
    ];
    transferModeValue.textContent = transferModeLabels[maxTransfers];

    travelTimeLimitValue.textContent = Number.isFinite(maxTravelMinutes)
        ? Math.round(maxTravelMinutes) + " min"
        : "No limit";

    circleOpacityValue.textContent =
        Math.round(circleOpacity * 100) + "%";

    legendOriginText.textContent =
        walkMinutes.toFixed(0) +
        " min (~" +
        Math.round(walkRadius) +
        " m) from selected point";

    updateTargetControls();
}

function findNearbyStopsXY(x, y) {
    const gx = Math.floor(x / GRID_SIZE);
    const gy = Math.floor(y / GRID_SIZE);
    const cellRadius = Math.ceil(walkRadius / GRID_SIZE);
    const radiusSquared = walkRadius * walkRadius;
    const candidates = new Set();

    for (let dx = -cellRadius; dx <= cellRadius; ++dx) {
        for (let dy = -cellRadius; dy <= cellRadius; ++dy) {
            const key =
                String(gx + dx) +
                ":" +
                String(gy + dy);

            const cell = grid[key];

            if (!cell) {
                continue;
            }

            for (const stopIndex of cell) {
                candidates.add(stopIndex);
            }
        }
    }

    const result = [];

    for (const stopIndex of candidates) {
        const stop = stops[stopIndex];
        if (!stop.routes.some(isRouteEnabled)) {
            continue;
        }
        const dx = stop.x - x;
        const dy = stop.y - y;

        if (dx * dx + dy * dy <= radiusSquared) {
            result.push(stopIndex);
        }
    }

    return result;
}

function findNearbyStops(lat, lon) {
    const point = project(lon, lat);
    return findNearbyStopsXY(point[0], point[1]);
}

function findNearbyStopsFromStop(stopIndex) {
    const cached = nearbyStopCache.get(stopIndex);

    if (cached) {
        return cached;
    }

    const stop = stops[stopIndex];
    const result = findNearbyStopsXY(stop.x, stop.y);

    nearbyStopCache.set(stopIndex, result);
    return result;
}

function clearResult() {
    pendingCoverageDisks = [[], [], []];
    activeCoverageDisks = [[], [], []];
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
    stopMarkers.clear();
}

function routeLabel(route) {
    if (!route) {
        return "?";
    }
    const names = {
        bus: "\u0410\u0432\u0442\u043E\u0431\u0443\u0441",
        tram: "\u0422\u0440\u0430\u043C\u0432\u0430\u0439",
        trolleybus: (
            "\u0422\u0440\u043E\u043B\u043B"
            + "\u0435\u0439\u0431\u0443\u0441"
        ),
        metro: "\u041C\u0435\u0442\u0440\u043E",
        mcc: "\u041C\u0426\u041A",
        mcd: "\u041C\u0426\u0414"
    };
    const type = route.mode || route.route || "";
    const ref = route.ref || "?";
    return (names[type] || type) + " " + ref;
}

function drawOrigin(lat, lon) {
    const originWalkRadius = Number.isFinite(maxTravelMinutes)
        ? Math.min(
            walkRadius,
            Math.max(0, maxTravelMinutes) * WALK_SPEED_M_PER_MIN
        )
        : walkRadius;

    if (originWalkRadius > 0) {
        L.circle(
            [lat, lon],
            {
                radius: originWalkRadius,
                weight: 2,
                color: "#d32f2f",
                opacity: 0.85,
                fillColor: "#ef5350",
                fillOpacity: 0.025,
                interactive: false,
                pane: "originPane"
            }
        ).addTo(originLayer);
    }

    L.circleMarker(
        [lat, lon],
        {
            radius: 7,
            weight: 2,
            color: "#111",
            fillColor: "#fff",
            fillOpacity: 1,
            interactive: false,
            pane: "originPane"
        }
    ).addTo(originLayer);
}

function drawRoute(routeKey, transferCount, reachableSegments) {
    const route = routes[routeKey];

    if (!route || !reachableSegments) {
        return;
    }

    const color = routeColor(routeKey);
    const routeLayers = [
        directRouteLayer,
        transferRouteLayer,
        twoTransferRouteLayer
    ];
    const targetLayer = routeLayers[transferCount];
    const weights = [4, 2.8, 2.2];
    const opacities = [0.85, 0.48, 0.34];
    const accessLabels = [
        "no transfers",
        "one transfer",
        "two transfers"
    ];

    for (const segment of reachableSegments.values()) {
        const geometry = routeSegmentGeometry(segment);

        if (!geometry) {
            continue;
        }

        const direction = segment.direction;
        const feature = {
            type: "Feature",
            properties: {},
            geometry: geometry
        };

        const layer = L.geoJSON(
            feature,
            {
                pane: "routePane",
                interactive: false,
                style: {
                    color: color,
                    weight: weights[transferCount],
                    opacity: opacities[transferCount]
                }
            }
        );

        const from = stops[direction.stops[segment.startPos]];
        const to = stops[direction.stops[segment.endPos]];

        layer.bindPopup(
            "<b>" +
            escapeHtml(routeLabel(route)) +
            "</b><br>" +
            escapeHtml(from ? from.name : "?") +
            " &rarr; " +
            escapeHtml(to ? to.name : "?") +
            "<br>" +
            accessLabels[transferCount]
        );

        layer.addTo(targetLayer);
    }
}

function coverageRadiusForArrival(arrivalMinutes) {
    if (!Number.isFinite(maxTravelMinutes)) {
        return walkRadius;
    }

    if (!Number.isFinite(arrivalMinutes)) {
        return 0;
    }

    const remainingMinutes = Math.max(
        0,
        maxTravelMinutes - arrivalMinutes
    );

    return Math.min(
        walkRadius,
        remainingMinutes * WALK_SPEED_M_PER_MIN
    );
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
        radius: radius
    });
}

function coverageCirclePath(stop, radiusMeters) {
    const center = map.latLngToLayerPoint([stop.lat, stop.lon]);
    const latRadians = stop.lat * Math.PI / 180.0;
    const metersPerLonDegree = Math.max(
        1.0,
        111320.0 * Math.cos(latRadians)
    );
    const edge = map.latLngToLayerPoint([
        stop.lat,
        stop.lon + radiusMeters / metersPerLonDegree
    ]);
    const radiusPixels = Math.max(
        0.5,
        Math.abs(edge.x - center.x)
    );
    const x = center.x;
    const y = center.y;
    const r = radiusPixels;

    return [
        "M", x - r, y,
        "A", r, r, 0, 1, 0, x + r, y,
        "A", r, r, 0, 1, 0, x - r, y,
        "Z"
    ].join(" ");
}

function positionCoverageOverlay() {
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    const size = map.getSize();

    L.DomUtil.setPosition(coverageSvg, topLeft);
    coverageSvg.setAttribute("width", String(size.x));
    coverageSvg.setAttribute("height", String(size.y));
    coverageSvg.setAttribute(
        "viewBox",
        [topLeft.x, topLeft.y, size.x, size.y].join(" ")
    );
}

function clearCoverageOverlay() {
    for (const path of coveragePaths) {
        path.setAttribute("d", "");
    }
}

function redrawCoverageOverlay() {
    positionCoverageOverlay();

    const colors = ["#3388ff", "#ff9800", "#7c3aed"];

    /*
     * All three transfer classes are opaque INSIDE one SVG group.
     * The group opacity is then applied once.  Drawing 2 -> 1 -> 0
     * means the best (fewest-transfer) class wins in overlaps, while
     * overlapping disks within one class never become darker.
     */
    coverageGroup.setAttribute("opacity", String(circleOpacity));

    for (const transferCount of [2, 1, 0]) {
        const path = coveragePaths[transferCount];
        const disks = activeCoverageDisks[transferCount];
        const data = disks.map(
            disk => coverageCirclePath(disk.stop, disk.radius)
        ).join(" ");

        path.setAttribute("fill", colors[transferCount]);
        path.setAttribute("fill-opacity", "1");
        path.setAttribute("d", data);
    }
}

function flushCoverage() {
    activeCoverageDisks = pendingCoverageDisks.map(
        disks => disks.slice()
    );
    redrawCoverageOverlay();
}

function stopRouteDetails(stopIndex) {
    const stop = stops[stopIndex];
    const rows = [];
    const ordered = Array.from(stop.routes).sort(
        (a, b) => routeLabel(routes[a]).localeCompare(
            routeLabel(routes[b]),
            undefined,
            { numeric: true }
        )
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
                escapeHtml(direction.from || "?") +
                " &rarr; " +
                escapeHtml(direction.to || "?")
            );
        }

        rows.push(
            "<div><b>" +
            escapeHtml(routeLabel(route)) +
            "</b>" +
            (directions.length
                ? ": " + directions.join(" / ")
                : "") +
            "</div>"
        );
    }

    return rows.join("");
}

function stopMarkerRadiusForZoom() {
    return 2.5;
}

function updateStopMarkerSizes() {
    const radius = stopMarkerRadiusForZoom();

    for (const marker of stopMarkers.values()) {
        if (marker && typeof marker.setRadius === "function") {
            marker.setRadius(radius);
        }
    }
}

function drawStop(stopIndex, transferCount) {
    const stop = stops[stopIndex];
    const colors = ["#1565c0", "#ef6c00", "#6d28d9"];
    const fillColors = ["#42a5f5", "#ffb74d", "#a78bfa"];
    const color = colors[transferCount];
    const fillColor = fillColors[transferCount];
    const radius = stopMarkerRadiusForZoom();
    const weight = 1;

    const marker = L.circleMarker(
        [stop.lat, stop.lon],
        {
            radius: radius,
            weight: weight,
            color: color,
            fillColor: fillColor,
            fillOpacity: 0.9,
            bubblingMouseEvents: false,
            pane: "stopPane"
        }
    );

    const accessLabels = [
        TEXT.accessDirect,
        TEXT.accessTransfer,
        TEXT.accessTwoTransfers
    ];
    const access = accessLabels[transferCount];

    marker.bindPopup(
        "<b>" +
        escapeHtml(stop.name || TEXT.stop) +
        "</b><br>" +
        access +
        "<br><br>" +
        stopRouteDetails(stopIndex) +
        "<br><span style=\"color:#666\">" +
        TEXT.doubleClick +
        "</span>"
    );

    function stopMapSelection(event) {
        suppressMapClickUntil = performance.now() + 600;

        if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent);
        }
    }

    marker.on(
        "click",
        event => {
            stopMapSelection(event);
        }
    );

    marker.on(
        "dblclick",
        event => {
            stopMapSelection(event);
            marker.closePopup();
            showTripToStop(stopIndex);
        }
    );

    marker.addTo(stopLayer);
    displayedStopIndexes.add(stopIndex);
    stopMarkers.set(stopIndex, marker);
}

function nearestDisplayedStop(latlng, maxPixels) {
    if (displayedStopIndexes.size === 0) {
        return null;
    }

    const clickPoint = map.latLngToContainerPoint(latlng);
    let bestStopIndex = null;
    let bestDistance2 = maxPixels * maxPixels;

    for (const stopIndex of displayedStopIndexes) {
        const stop = stops[stopIndex];
        const point = map.latLngToContainerPoint(
            [stop.lat, stop.lon]
        );
        const dx = point.x - clickPoint.x;
        const dy = point.y - clickPoint.y;
        const distance2 = dx * dx + dy * dy;

        if (distance2 <= bestDistance2) {
            bestDistance2 = distance2;
            bestStopIndex = stopIndex;
        }
    }

    return bestStopIndex;
}

function openStopPopup(stopIndex) {
    const marker = stopMarkers.get(stopIndex);

    if (marker) {
        marker.openPopup();
    }
}

function stopDistanceMeters(aIndex, bIndex) {
    const a = stops[aIndex];
    const b = stops[bIndex];
    return Math.hypot(a.x - b.x, a.y - b.y);
}

function pointToStopDistanceMeters(point, stopIndex) {
    const xy = project(point.lon, point.lat);
    const stop = stops[stopIndex];
    return Math.hypot(stop.x - xy[0], stop.y - xy[1]);
}

function pointToPointDistanceMeters(a, b) {
    const axy = project(a.lon, a.lat);
    const bxy = project(b.lon, b.lat);
    return Math.hypot(axy[0] - bxy[0], axy[1] - bxy[1]);
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
        const startPositions = stopPositions(
            direction,
            startStopIndex
        );

        for (const startPos of startPositions) {
            for (
                let endPos = startPos + 1;
                endPos < direction.stops.length;
                ++endPos
            ) {
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
                    rideStops: endPos - startPos
                });
            }
        }
    }

    return result;
}

function routeSpeedMetersPerMin(routeKey) {
    const route = routes[routeKey];
    const mode = route ? (route.mode || route.route) : "bus";
    return MODE_SPEEDS[mode] ?? BUS_SPEED_M_PER_MIN;
}

function legParts(leg) {
    if (leg.parts && leg.parts.length) {
        return leg.parts;
    }

    return [{
        direction: leg.direction,
        startPos: leg.startPos,
        endPos: leg.endPos
    }];
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

    for (const part of legParts(leg)) {
        for (let pos = part.startPos; pos < part.endPos; ++pos) {
            const a = part.direction.stops[pos];
            const b = part.direction.stops[pos + 1];
            result += stopDistanceMeters(a, b);
        }
    }

    return result;
}

// Routing must never derive edge weights from drawable geometry.
// Distances use consecutive confirmed OSM route stops plus mode circuity.
const MODE_DISTANCE_FACTORS = {
    bus: 1.25,
    tram: 1.18,
    trolleybus: 1.25,
    metro: 1.10,
    mcc: 1.08,
    mcd: 1.08
};

function legDistanceMeters(leg) {
    const route = routes[leg.routeKey];
    const mode = route ? (route.mode || route.route) : "bus";
    const factor = MODE_DISTANCE_FACTORS[mode] ?? 1.20;
    return stopChainDistanceMeters(leg) * factor;
}

function legEstimatedMinutes(leg) {
    const distance = legDistanceMeters(leg);
    const speed = routeSpeedMetersPerMin(leg.routeKey);

    return {
        distance: distance,
        minutes:
            distance / speed +
            leg.rideStops * STOP_DELAY_MIN
    };
}

function prepareItineraryMetrics(itinerary) {
    if (Number.isFinite(itinerary.generalizedMinutes)) {
        return itinerary;
    }

    const walkTime = itinerary.walkMeters / WALK_SPEED_M_PER_MIN;
    itinerary.walkMinutes = walkTime;

    if (itinerary.type === "walk") {
        itinerary.transitMeters = 0;
        itinerary.boardings = 0;
        itinerary.totalMinutes = walkTime;
        itinerary.generalizedMinutes =
            walkTime * (1.0 + WALK_PREFERENCE_EXTRA);
        return itinerary;
    }

    const leg1 = legEstimatedMinutes(itinerary.leg1);
    let transitMeters = leg1.distance;
    let transitMinutes = leg1.minutes;
    let boardings = 1;

    if (itinerary.type === "transfer" || itinerary.type === "two-transfer") {
        const leg2 = legEstimatedMinutes(itinerary.leg2);
        transitMeters += leg2.distance;
        transitMinutes += leg2.minutes;
        boardings = 2;
    }

    if (itinerary.type === "two-transfer") {
        const leg3 = legEstimatedMinutes(itinerary.leg3);
        transitMeters += leg3.distance;
        transitMinutes += leg3.minutes;
        boardings = 3;
    }

    itinerary.transitMeters = transitMeters;
    itinerary.boardings = boardings;
    const waitingMinutes = candidateLegs(itinerary).reduce(
        (total, leg) => total + boardingWaitForRoute(leg.routeKey), 0
    );
    itinerary.totalMinutes = walkTime + transitMinutes + waitingMinutes;
    itinerary.generalizedMinutes =
        itinerary.totalMinutes +
        walkTime * WALK_PREFERENCE_EXTRA;

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
    if (itinerary.type === "two-transfer") {
        return 2;
    }

    return itinerary.type === "transfer" ? 1 : 0;
}

function compareItineraries(a, b) {
    prepareItineraryMetrics(a);
    prepareItineraryMetrics(b);

    const transferDiff =
        itineraryTransferCount(a) - itineraryTransferCount(b);

    if (transferDiff !== 0) {
        return transferDiff;
    }

    const scoreDiff =
        a.generalizedMinutes - b.generalizedMinutes;

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

    if (itinerary.type === "direct") {
        boardings = 1;
    } else if (itinerary.type === "transfer") {
        boardings = 2;
    } else if (itinerary.type === "two-transfer") {
        boardings = 3;
    }

    return (
        itinerary.walkMeters / WALK_SPEED_M_PER_MIN +
        itinerary.rideStops * ROUGH_STOP_MIN +
        (boardings ? candidateLegs(itinerary).reduce(
            (total, leg) => total + boardingWaitForRoute(leg.routeKey), 0
        ) : 0)
    );
}

function roughGeneralizedMinutes(itinerary) {
    const walkTime =
        itinerary.walkMeters / WALK_SPEED_M_PER_MIN;

    return (
        roughItineraryMinutes(itinerary) +
        walkTime * WALK_PREFERENCE_EXTRA
    );
}

function evidenceDominates(a, b) {
    const noMoreWalk = a.walkMeters <= b.walkMeters + 1.0;
    const noMoreStops = a.rideStops <= b.rideStops;
    const strictlyBetter =
        a.walkMeters < b.walkMeters - 1.0 ||
        a.rideStops < b.rideStops;

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

    const ordered = bucket.slice().sort(
        (a, b) =>
            roughGeneralizedMinutes(a) -
            roughGeneralizedMinutes(b)
    );

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

    if (bucket.some(item => evidenceDominates(item, candidate))) {
        return;
    }

    bucket = bucket.filter(
        item => !evidenceDominates(candidate, item)
    );
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

        if (
            !current ||
            roughGeneralizedMinutes(path) <
                roughGeneralizedMinutes(current)
        ) {
            bestByLastRoute.set(routeKey, path);
        }
    }

    return Array.from(bestByLastRoute.values())
        .sort(
            (a, b) =>
                roughGeneralizedMinutes(a) -
                roughGeneralizedMinutes(b)
        )
        .slice(0, MAX_TRANSFER_EXTENSION_OPTIONS);
}

function addTransferBoardOption(
    boardEvidence,
    boardStopIndex,
    candidate
) {
    if (!withinTravelTimeLimit(candidate)) {
        return;
    }

    let options = boardEvidence.get(boardStopIndex) || [];
    const routeKey = candidate.leg2.routeKey;
    const existingIndex = options.findIndex(
        item => item.leg2.routeKey === routeKey
    );

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

    options.sort(
        (a, b) =>
            roughGeneralizedMinutes(a) -
            roughGeneralizedMinutes(b)
    );

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

    return (
        noSlower &&
        noMoreWalk &&
        noMoreBoardings &&
        strictlyBetter
    );
}

function itineraryIdentityKey(itinerary) {
    if (itinerary.type === "walk") {
        return "walk";
    }

    const legs = candidateLegs(itinerary).map(
        leg => [
            leg.routeKey,
            String(legStartStopIndex(leg)),
            String(legEndStopIndex(leg)),
            routeLegPathKey(leg)
        ].join(":")
    );
    const transfers = [];

    if (itinerary.leg2) {
        transfers.push([
            itinerary.transferAlightStopIndex,
            itinerary.transferBoardStopIndex
        ].join("-"));
    }

    if (itinerary.leg3) {
        transfers.push([
            itinerary.secondTransferAlightStopIndex,
            itinerary.secondTransferBoardStopIndex
        ].join("-"));
    }

    return legs.join(">") + "|" + transfers.join(">");
}

function filterReasonableItineraries(itineraries) {
    const unique = new Map();

    for (const itinerary of itineraries) {
        if (
            !withinTravelTimeLimit(itinerary) ||
            itineraryHasTechnicalSamePlaceLeg(itinerary)
        ) {
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
        if (pareto.some(item => itineraryDominates(item, candidate))) {
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
        const bestTime = Math.min(
            ...group.map(item => item.totalMinutes)
        );
        const maxTime = Math.max(
            bestTime * REASONABLE_TIME_FACTOR,
            bestTime + REASONABLE_TIME_EXTRA_MIN
        );

        reasonable.push(
            ...group.filter(
                item => item.totalMinutes <= maxTime
            )
        );
    }

    return reasonable
        .sort(compareItineraries)
        .slice(0, MAX_TRIP_ALTERNATIVES);
}

function relationKey(direction) {
    if (
        direction.relation_id !== null &&
        direction.relation_id !== undefined
    ) {
        return String(direction.relation_id);
    }

    return [
        direction.from || "",
        direction.to || "",
        direction.stops.join(",")
    ].join(":");
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
    const key = selectionKey() + ":" + String(targetStopIndex);
    const cached = itineraryCache.get(key);

    if (cached) {
        return cached;
    }

    const result = [];
    const targetWalk = pointToStopDistanceMeters(
        selectedPoint,
        targetStopIndex
    );

    if (targetWalk <= walkRadius) {
        result.push({
            type: "walk",
            walkMeters: targetWalk,
            rideStops: 0,
            initialWalk: targetWalk
        });
    }

    const analysis = analyzeSelection();

    result.push(
        ...findDirectItineraries(
            targetStopIndex,
            analysis
        )
    );

    if (maxTransfers >= 1) {
        result.push(
            ...findTransferItineraries(
                targetStopIndex,
                analysis
            )
        );
    }

    if (maxTransfers >= 2) {
        result.push(
            ...findTwoTransferItineraries(
                targetStopIndex,
                analysis
            )
        );
    }

    const filtered = filterReasonableItineraries(result);
    itineraryCache.set(key, filtered);
    return filtered;
}

function copyItineraryForTargetPoint(
    itinerary,
    targetStopIndex,
    finalWalk
) {
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
            itinerary.generalizedMinutes +
            finalWalkMinutes * (1.0 + WALK_PREFERENCE_EXTRA)
    };
}

function targetPointKey(point) {
    return [
        point.lat.toFixed(6),
        point.lon.toFixed(6)
    ].join(":");
}

function findPointItineraries(point) {
    const key = selectionKey() + ":point:" + targetPointKey(point);
    const cached = itineraryCache.get(key);

    if (cached) {
        return cached;
    }

    const result = [];
    const directWalk = pointToPointDistanceMeters(
        selectedPoint,
        point
    );

    if (directWalk <= walkRadius) {
        result.push({
            type: "walk",
            walkMeters: directWalk,
            rideStops: 0,
            initialWalk: directWalk,
            finalWalk: directWalk,
            targetStopIndex: null
        });
    }

    const analysis = analyzeSelection();
    const targetStops = findNearbyStops(point.lat, point.lon);

    for (const stopIndex of targetStops) {
        const finalWalk = pointToStopDistanceMeters(
            point,
            stopIndex
        );

        const direct = findDirectItineraries(
            stopIndex,
            analysis
        );

        for (const itinerary of direct) {
            result.push(
                copyItineraryForTargetPoint(
                    itinerary,
                    stopIndex,
                    finalWalk
                )
            );
        }

        if (maxTransfers >= 1) {
            const transfer = findTransferItineraries(
                stopIndex,
                analysis
            );

            for (const itinerary of transfer) {
                result.push(
                    copyItineraryForTargetPoint(
                        itinerary,
                        stopIndex,
                        finalWalk
                    )
                );
            }
        }

        if (maxTransfers >= 2) {
            const twoTransfer = findTwoTransferItineraries(
                stopIndex,
                analysis
            );

            for (const itinerary of twoTransfer) {
                result.push(
                    copyItineraryForTargetPoint(
                        itinerary,
                        stopIndex,
                        finalWalk
                    )
                );
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

    if (geometry.type === "LineString") {
        return geometry.coordinates ? [geometry.coordinates] : [];
    }

    if (geometry.type === "MultiLineString") {
        return geometry.coordinates || [];
    }

    if (geometry.type === "GeometryCollection") {
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
        .filter(line => line && line.length >= 2)
        .map(
            line => line.map(
                coord => [coord[0], coord[1]]
            )
        );
    const parts = [];
    let current = null;

    for (const line of sourceLines) {
        if (!current) {
            current = line.slice();
            continue;
        }

        const currentEnd = current[current.length - 1];
        const gap = segmentLengthMeters(
            currentEnd,
            line[0]
        );

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
        t = (
            (stop.x - a[0]) * vx +
            (stop.y - a[1]) * vy
        ) / length2;
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
        coord: [lon, lat]
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

    parts.forEach(
        (part, partIndex) => {
            for (let i = 0; i + 1 < part.length; ++i) {
                segments.push({
                    order: order,
                    partIndex: partIndex,
                    segmentIndex: i,
                    a: part[i],
                    b: part[i + 1],
                    aXY: project(part[i][0], part[i][1]),
                    bXY: project(part[i + 1][0], part[i + 1][1])
                });
                ++order;
            }
        }
    );

    const anchors = [];
    let minProgress = 0;

    for (const stopIndex of direction.stops) {
        const anchor = chooseStopAnchor(
            segments,
            stopIndex,
            minProgress
        );

        anchors.push(anchor);

        if (anchor) {
            minProgress = anchor.progress + 1e-6;
        }
    }

    const shape = {
        parts: parts,
        anchors: anchors
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

    for (
        let i = start.segmentIndex + 1;
        i <= end.segmentIndex;
        ++i
    ) {
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

    if (
        startPos === undefined ||
        endPos === undefined ||
        startPos < 0 ||
        endPos <= startPos
    ) {
        return null;
    }

    const shape = getDirectionShape(direction);
    const start = shape.anchors[startPos];
    const end = shape.anchors[endPos];

    if (!start || !end || end.progress <= start.progress) {
        return null;
    }

    if (start.partIndex === end.partIndex) {
        const coordinates = samePartSegment(
            shape.parts[start.partIndex],
            start,
            end
        );

        if (coordinates.length < 2) {
            return null;
        }

        return {
            type: "LineString",
            coordinates: coordinates
        };
    }

    const lines = [];
    const first = firstPartSegment(
        shape.parts[start.partIndex],
        start
    );

    if (first.length >= 2) {
        lines.push(first);
    }

    for (
        let partIndex = start.partIndex + 1;
        partIndex < end.partIndex;
        ++partIndex
    ) {
        const part = shape.parts[partIndex];

        if (part.length >= 2) {
            lines.push(part);
        }
    }

    const last = lastPartSegment(
        shape.parts[end.partIndex],
        end
    );

    if (last.length >= 2) {
        lines.push(last);
    }

    if (lines.length === 0) {
        return null;
    }

    if (lines.length === 1) {
        return {
            type: "LineString",
            coordinates: lines[0]
        };
    }

    return {
        type: "MultiLineString",
        coordinates: lines
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

    return { type: "LineString", coordinates: coords };
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
        const geometry = routeSinglePartGeometry(part) || stopChainGeometry(
            part.direction,
            part.startPos,
            part.endPos
        );

        if (geometry) {
            lines.push(...geometryLines(geometry));
        }
    }

    if (lines.length === 0) {
        return null;
    }

    if (lines.length === 1) {
        return {
            type: "LineString",
            coordinates: lines[0]
        };
    }

    return {
        type: "MultiLineString",
        coordinates: lines
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

        if (
            last &&
            last[0] === latlng[0] &&
            last[1] === latlng[1]
        ) {
            return;
        }

        result.push(latlng);
    }

    for (const part of legParts(leg)) {
        if (!part || !part.direction || !part.direction.stops) {
            continue;
        }

        const startPos = Math.max(0, Number(part.startPos) || 0);
        const endPos = Math.min(
            part.direction.stops.length - 1,
            Number(part.endPos) || 0
        );

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
    const fallback = [[
        stops[startStopIndex].lat,
        stops[startStopIndex].lon
    ], [
        stops[endStopIndex].lat,
        stops[endStopIndex].lon
    ]];

    try {
        const geometry = routeSinglePartGeometry({
            direction: direction,
            startPos: startPos,
            endPos: endPos
        });

        if (!geometry) {
            return [fallback];
        }

        const lines = geometryLines(geometry)
            .filter(line => line && line.length >= 2)
            .map(
                line => line
                    .map(coord => [coord[1], coord[0]])
                    .filter(
                        latlng =>
                            Number.isFinite(latlng[0]) &&
                            Number.isFinite(latlng[1])
                    )
            )
            .filter(line => line.length >= 2);

        if (!lines.length) {
            return [fallback];
        }

        const first = lines[0][0];
        const lastLine = lines[lines.length - 1];
        const last = lastLine[lastLine.length - 1];
        const startError = tripGeometryEndpointDistanceMeters(
            first,
            startStopIndex
        );
        const endError = tripGeometryEndpointDistanceMeters(
            last,
            endStopIndex
        );

        /*
         * A route geometry can pass close to the same stop several times.
         * If map matching selected another branch of a loop, reject only
         * this stop-to-stop edge instead of corrupting the whole trip leg.
         */
        if (startError > 250.0 || endError > 250.0) {
            console.warn(
                "Trip edge geometry rejected",
                startError,
                endError,
                startStopIndex,
                endStopIndex
            );
            return [fallback];
        }

        return lines;
    } catch (error) {
        console.warn(
            "Trip edge geometry fallback",
            error,
            startStopIndex,
            endStopIndex
        );
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
            route &&
            route.directions &&
            route.directions.includes(part.direction);

        if (
            (part.routeKey && part.routeKey !== leg.routeKey) ||
            !directionBelongsToRoute
        ) {
            console.error(
                "Trip leg/geometry route mismatch",
                leg.routeKey,
                part.routeKey,
                relationKey(part.direction)
            );
            continue;
        }

        const startPos = Math.max(0, Number(part.startPos) || 0);
        const endPos = Math.min(
            part.direction.stops.length - 1,
            Number(part.endPos) || 0
        );

        for (let pos = startPos; pos < endPos; ++pos) {
            result.push(
                ...tripEdgeGeometryLines(
                    part.direction,
                    pos,
                    pos + 1
                )
            );
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
        console.warn("No drawable trip geometry", leg);
        return false;
    }

    const startStopIndex = legStartStopIndex(leg);
    const endStopIndex = legEndStopIndex(leg);
    const startStop = stops[startStopIndex];
    const endStop = stops[endStopIndex];
    const popupHtml =
        startStop && endStop
            ? "<b>" +
                escapeHtml(routeLabel(route)) +
                "</b><br>" +
                escapeHtml(startStop.name || TEXT.stop) +
                " &rarr; " +
                escapeHtml(endStop.name || TEXT.stop)
            : null;

    let drawn = false;

    for (const latlngs of lines) {
        const cleanLatLngs = latlngs.filter(
            latlng =>
                latlng &&
                latlng.length >= 2 &&
                Number.isFinite(latlng[0]) &&
                Number.isFinite(latlng[1])
        );

        if (cleanLatLngs.length < 2) {
            continue;
        }

        try {
            const layer = L.polyline(
                cleanLatLngs,
                {
                    pane: "tripRoutePane",
                    interactive: false,
                    color: color,
                    weight: 6,
                    opacity: 0.95,
                    lineCap: "round",
                    lineJoin: "round"
                }
            );

            if (popupHtml) {
                layer.bindPopup(popupHtml);
            }

            layer.addTo(tripRouteLayer);
            drawn = true;
        } catch (error) {
            console.warn("Trip polyline pane fallback", error);

            try {
                const fallbackLayer = L.polyline(
                    cleanLatLngs,
                    {
                        interactive: false,
                        color: color,
                        weight: 6,
                        opacity: 0.95
                    }
                );

                if (popupHtml) {
                    fallbackLayer.bindPopup(popupHtml);
                }

                fallbackLayer.addTo(tripRouteLayer);
                drawn = true;
            } catch (fallbackError) {
                console.error(
                    "Unable to draw trip polyline",
                    fallbackError,
                    cleanLatLngs
                );
            }
        }
    }

    return drawn;
}

function drawTripWalk(lat1, lon1, lat2, lon2) {
    L.polyline(
        [
            [lat1, lon1],
            [lat2, lon2]
        ],
        {
            color: "#555",
            weight: 3,
            opacity: 0.85,
            dashArray: "5 7",
            interactive: false,
            pane: "tripWalkPane"
        }
    ).addTo(tripWalkLayer);
}

function drawTripMarker(stopIndex, label, color) {
    const stop = stops[stopIndex];
    const marker = L.circleMarker(
        [stop.lat, stop.lon],
        {
            radius: 7,
            weight: 2,
            color: color,
            fillColor: "#fff",
            fillOpacity: 1,
            bubblingMouseEvents: false,
            pane: "tripMarkerPane"
        }
    );

    marker.bindPopup(
        "<b>" +
        escapeHtml(label) +
        "</b><br>" +
        escapeHtml(stop.name || TEXT.stop) +
        "<br><br>" +
        stopRouteDetails(stopIndex)
    );

    marker.addTo(tripMarkerLayer);
}

function drawTripPoint(point, label, color) {
    const marker = L.circleMarker(
        [point.lat, point.lon],
        {
            radius: 8,
            weight: 3,
            color: color,
            fillColor: "#fff",
            fillOpacity: 1,
            bubblingMouseEvents: false,
            interactive: false,
            pane: "tripMarkerPane"
        }
    );

    marker.bindPopup("<b>" + escapeHtml(label) + "</b>");
    marker.addTo(tripMarkerLayer);

    L.circle(
        [point.lat, point.lon],
        {
            radius: walkRadius,
            weight: 2,
            color: color,
            opacity: 0.75,
            fillColor: color,
            fillOpacity: Math.min(circleOpacity, 0.08),
            dashArray: "6 6",
            interactive: false,
            pane: "tripWalkPane"
        }
    ).addTo(tripWalkLayer);
}

function minutesForMeters(meters) {
    return Math.max(0, meters / WALK_SPEED_M_PER_MIN);
}

function addTripStep(text, className) {
    const step = document.createElement("div");
    step.className = "trip-step " + className;
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
            " (" +
            (startStop && startStop.name ? startStop.name : "?") +
            " -> " +
            (endStop && endStop.name ? endStop.name : "?") +
            ")";
    }

    return result;
}

function itinerarySummary(itinerary, index) {
    const prefix =
        TEXT.optionPrefix +
        String(index + 1) +
        ": ";

    prepareItineraryMetrics(itinerary);

    if (itinerary.type === "walk") {
        return (
            prefix +
            TEXT.walkOnly +
            " ~" +
            itinerary.totalMinutes.toFixed(1) +
            " " +
            TEXT.minutesShort
        );
    }

    let routeText = legSummary(itinerary.leg1);

    if (itinerary.type === "transfer" || itinerary.type === "two-transfer") {
        routeText += " -> " + legSummary(itinerary.leg2);
    }

    if (itinerary.type === "two-transfer") {
        routeText += " -> " + legSummary(itinerary.leg3);
    }

    return (
        prefix +
        routeText +
        ", ~" +
        itinerary.totalMinutes.toFixed(1) +
        " " +
        TEXT.minutesShort +
        ", " +
        TEXT.walkShort +
        " " +
        minutesForMeters(itinerary.walkMeters).toFixed(1) +
        " " +
        TEXT.minutesShort +
        ", " +
        String(itinerary.rideStops) +
        " " +
        TEXT.stopsShort
    );
}

function drawTripAlternatives(
    point,
    itineraries,
    selectedIndex
) {
    tripAlternatives.innerHTML = "";

    if (itineraries.length <= 1) {
        return;
    }

    itineraries.forEach(
        (itinerary, index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "trip-option";
            button.textContent = itinerarySummary(
                itinerary,
                index
            );

            if (index === selectedIndex) {
                button.classList.add("trip-option-active");
            }

            button.addEventListener(
                "click",
                () => {
                    tripAlternativeIndex = index;
                    renderTrip(point);
                }
            );

            tripAlternatives.appendChild(button);
        }
    );
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
        " &rarr; " +
        escapeHtml(TEXT.destination) +
        ".",
        "trip-step-walk"
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
    tripSteps.innerHTML = "";

    if (tripAlternativeIndex >= itineraries.length) {
        tripAlternativeIndex = 0;
    }

    drawTripAlternatives(
        point,
        itineraries,
        tripAlternativeIndex
    );

    L.circleMarker(
        [selectedPoint.lat, selectedPoint.lon],
        {
            radius: 7,
            weight: 2,
            color: "#111",
            fillColor: "#fff",
            fillOpacity: 1,
            bubblingMouseEvents: false,
            interactive: false,
            pane: "originPane"
        }
    ).addTo(originLayer);

    drawTripPoint(point, TEXT.destination, "#16a34a");

    if (itineraries.length === 0) {
        addTripStep(
            "No route found to the selected point for the current " +
            "walking radius and transfer mode.",
            "trip-step-walk"
        );
        return;
    }

    const itinerary = itineraries[tripAlternativeIndex];

    if (itinerary.type === "walk") {
        drawTripWalk(
            selectedPoint.lat,
            selectedPoint.lon,
            point.lat,
            point.lon
        );
        addTripStep(
            TEXT.walkPrefix +
            minutesForMeters(itinerary.walkMeters).toFixed(1) +
            TEXT.minutesColon +
            escapeHtml(TEXT.destination) +
            ".",
            "trip-step-walk"
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
        console.error("Invalid first trip leg", itinerary);
        addTripStep(
            "Unable to draw the selected route.",
            "trip-step-walk"
        );
        return;
    }

    drawTripWalk(
        selectedPoint.lat,
        selectedPoint.lon,
        board.lat,
        board.lon
    );
    drawTripMarker(leg1StartStopIndex, TEXT.boarding, "#1565c0");
    drawTripRouteLeg(leg1, "#1565c0");

    addTripStep(
        TEXT.walkPrefix +
        minutesForMeters(itinerary.initialWalk).toFixed(1) +
        TEXT.walkToStop +
        escapeHtml(board.name || TEXT.stop) +
        "</b>.",
        "trip-step-walk"
    );

    addTripStep(
        "<b>" +
        escapeHtml(routeLabel(route1)) +
        "</b>: " +
        escapeHtml(board.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(alight1.name || TEXT.stop) +
        " (" +
        leg1.rideStops +
        TEXT.rideStopsSuffix,
        "trip-step-leg1"
    );

    if (itinerary.type === "direct") {
        drawTripMarker(
            targetStopIndex,
            TEXT.destination,
            "#1565c0"
        );
        drawTripWalk(
            targetStop.lat,
            targetStop.lon,
            point.lat,
            point.lon
        );
        addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
        return;
    }

    const transferBoard = stops[itinerary.transferBoardStopIndex];
    const leg2 = itinerary.leg2;
    const leg2EndStopIndex = legEndStopIndex(leg2);
    const route2 = routes[leg2.routeKey];

    if (!transferBoard || !route2 || stops[leg2EndStopIndex] === undefined) {
        console.error("Invalid second trip leg", itinerary);
        addTripStep(
            "Unable to draw the selected route.",
            "trip-step-walk"
        );
        return;
    }

    drawTripMarker(
        itinerary.transferAlightStopIndex,
        TEXT.transferExit,
        "#1565c0"
    );
    drawTripMarker(
        itinerary.transferBoardStopIndex,
        TEXT.transferBoarding,
        "#ef6c00"
    );

    if (
        itinerary.transferAlightStopIndex !==
        itinerary.transferBoardStopIndex
    ) {
        drawTripWalk(
            alight1.lat,
            alight1.lon,
            transferBoard.lat,
            transferBoard.lon
        );
    }

    drawTripRouteLeg(leg2, "#ef6c00");

    addTripStep(
        TEXT.transferWalkPrefix +
        minutesForMeters(itinerary.transferWalk).toFixed(1) +
        TEXT.minutesColon +
        escapeHtml(alight1.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(transferBoard.name || TEXT.stop) +
        ".",
        "trip-step-walk"
    );

    const alight2 = stops[leg2EndStopIndex];

    addTripStep(
        "<b>" +
        escapeHtml(routeLabel(route2)) +
        "</b>: " +
        escapeHtml(transferBoard.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(alight2.name || TEXT.stop) +
        " (" +
        leg2.rideStops +
        TEXT.rideStopsSuffix,
        "trip-step-leg2"
    );

    if (itinerary.type === "transfer") {
        drawTripMarker(targetStopIndex, TEXT.destination, "#ef6c00");
        drawTripWalk(
            targetStop.lat,
            targetStop.lon,
            point.lat,
            point.lon
        );
        addFinalWalkStep(targetStop, itinerary.finalWalk || 0);
        return;
    }

    const secondTransferBoard = stops[
        itinerary.secondTransferBoardStopIndex
    ];
    const leg3 = itinerary.leg3;
    const leg3EndStopIndex = legEndStopIndex(leg3);
    const route3 = routes[leg3.routeKey];

    if (!route3 || stops[leg3EndStopIndex] === undefined) {
        console.error("Invalid third trip leg", itinerary);
        addTripStep(
            "Unable to draw the selected route.",
            "trip-step-walk"
        );
        return;
    }

    drawTripMarker(
        itinerary.secondTransferAlightStopIndex,
        TEXT.transferExit,
        "#ef6c00"
    );
    drawTripMarker(
        itinerary.secondTransferBoardStopIndex,
        TEXT.transferBoarding,
        "#7c3aed"
    );

    if (
        itinerary.secondTransferAlightStopIndex !==
        itinerary.secondTransferBoardStopIndex
    ) {
        drawTripWalk(
            alight2.lat,
            alight2.lon,
            secondTransferBoard.lat,
            secondTransferBoard.lon
        );
    }

    addTripStep(
        TEXT.transferWalkPrefix +
        minutesForMeters(itinerary.secondTransferWalk).toFixed(1) +
        TEXT.minutesColon +
        escapeHtml(alight2.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(secondTransferBoard.name || TEXT.stop) +
        ".",
        "trip-step-walk"
    );

    drawTripRouteLeg(leg3, "#7c3aed");
    drawTripMarker(targetStopIndex, TEXT.destination, "#7c3aed");

    addTripStep(
        "<b>" +
        escapeHtml(routeLabel(route3)) +
        "</b>: " +
        escapeHtml(secondTransferBoard.name || TEXT.stop) +
        " &rarr; " +
        escapeHtml(targetStop.name || TEXT.stop) +
        " (" +
        leg3.rideStops +
        TEXT.rideStopsSuffix,
        "trip-step-leg3"
    );

    drawTripWalk(
        targetStop.lat,
        targetStop.lon,
        point.lat,
        point.lon
    );
    addFinalWalkStep(targetStop, itinerary.finalWalk || 0);

}

function showTripToPoint(point) {
    if (!selectedPoint) {
        return;
    }

    targetPoint = {
        lat: point.lat,
        lon: point.lon
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
        lon: stop.lon
    });
}

function selectionKey() {
    if (!selectedPoint) {
        return "";
    }

    return [
        selectedPoint.lat.toFixed(6),
        selectedPoint.lon.toFixed(6),
        walkRadius.toFixed(1),
        Array.from(enabledModes).sort().join(","),
        String(maxTransfers),
        Number.isFinite(maxTravelMinutes)
            ? maxTravelMinutes.toFixed(0)
            : "inf"
    ].join(":");
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

        if (
            endPos + 1 < current.startPos ||
            current.endPos + 1 < startPos
        ) {
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

    const key = directionId + ":" + startPos + ":" + endPos;
    routeSegments.set(key, {
        routeKey: segment.routeKey,
        direction: segment.direction,
        startStopIndex: startStopIndex,
        endStopIndex: endStopIndex,
        startPos: startPos,
        endPos: endPos,
        rideStops: endPos - startPos
    });
}

function routeDirectionContinuations(route) {
    const cached = routeContinuationCache.get(route);

    if (cached) {
        return cached;
    }

    const byStartStop = new Map();

    for (const direction of route.directions) {
        if (!direction.stops || direction.stops.length === 0) {
            continue;
        }

        const startStopIndex = direction.stops[0];
        const list = byStartStop.get(startStopIndex) || [];
        list.push(direction);
        byStartStop.set(startStopIndex, list);
    }

    const continuations = new Map();

    for (const direction of route.directions) {
        if (!direction.stops || direction.stops.length === 0) {
            continue;
        }

        const endStopIndex = direction.stops[direction.stops.length - 1];
        continuations.set(
            relationKey(direction),
            byStartStop.get(endStopIndex) || []
        );
    }

    routeContinuationCache.set(route, continuations);
    return continuations;
}

function cloneRouteLegParts(parts) {
    return parts.map(part => ({ ...part }));
}

function routeLegPathKey(leg) {
    return legParts(leg)
        .map(
            part =>
                (part.routeKey || leg.routeKey) +
                ":" +
                relationKey(part.direction) +
                ":" +
                String(part.startPos) +
                "-" +
                String(part.endPos)
        )
        .join(">");
}

function routeLegsFromBoard(routeKey, startDirection, startPos) {
    const cacheKey =
        routeKey +
        "|" +
        relationKey(startDirection) +
        "|" +
        String(startPos);
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
    const initialParts = [{
        routeKey: routeKey,
        direction: startDirection,
        startPos: startPos,
        endPos: startPos
    }];
    const queue = [{
        direction: startDirection,
        pos: startPos,
        parts: initialParts,
        rideStops: 0,
        rideMinutes: 0
    }];
    const bestStateMinutes = new Map();
    const result = [];
    const EPSILON = 1e-7;

    bestStateMinutes.set(
        relationKey(startDirection) + ":" + String(startPos),
        0
    );

    while (queue.length) {
        queue.sort((a, b) => a.rideMinutes - b.rideMinutes);
        const state = queue.shift();
        const stateKey =
            relationKey(state.direction) + ":" + String(state.pos);
        const known = bestStateMinutes.get(stateKey);

        if (
            known !== undefined &&
            state.rideMinutes > known + EPSILON
        ) {
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
                rideStops: 1
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
                parts: nextParts
            };

            result.push(leg);

            const nextStateKey =
                relationKey(state.direction) + ":" + String(nextPos);
            const previous = bestStateMinutes.get(nextStateKey);

            if (
                previous === undefined ||
                nextMinutes + EPSILON < previous
            ) {
                bestStateMinutes.set(nextStateKey, nextMinutes);
                queue.push({
                    direction: state.direction,
                    pos: nextPos,
                    parts: nextParts,
                    rideStops: nextRideStops,
                    rideMinutes: nextMinutes
                });
            }

            continue;
        }

        const nextDirections =
            continuations.get(relationKey(state.direction)) || [];

        for (const nextDirection of nextDirections) {
            if (!nextDirection.stops || nextDirection.stops.length === 0) {
                continue;
            }

            const nextStateKey = relationKey(nextDirection) + ":0";
            const previous = bestStateMinutes.get(nextStateKey);

            if (
                previous !== undefined &&
                previous <= state.rideMinutes + EPSILON
            ) {
                continue;
            }

            const nextParts = cloneRouteLegParts(state.parts);
            nextParts.push({
                routeKey: routeKey,
                direction: nextDirection,
                startPos: 0,
                endPos: 0
            });
            bestStateMinutes.set(nextStateKey, state.rideMinutes);
            queue.push({
                direction: nextDirection,
                pos: 0,
                parts: nextParts,
                rideStops: state.rideStops,
                rideMinutes: state.rideMinutes
            });
        }
    }

    routeLegTraversalCache.set(cacheKey, result);
    return result;
}

function collectReachableRouteData(evidence, legName) {
    const segmentsByRoute = new Map();
    const stopsByRoute = new Map();

    for (const [stopIndex, paths] of evidence.entries()) {
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
                    rideStops: part.endPos - part.startPos
                });
            }
        }
    }

    return {
        segmentsByRoute: segmentsByRoute,
        stopsByRoute: stopsByRoute
    };
}

function collectBestArrivalMinutes(evidence) {
    const result = new Map();

    if (!Number.isFinite(maxTravelMinutes)) {
        return result;
    }

    for (const [stopIndex, paths] of evidence.entries()) {
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
        return "";
    }

    return direction.stop_roles[pos] || "";
}

function stopAllowsBoarding(direction, pos) {
    const role = directionStopRole(direction, pos);
    return !role.endsWith("_exit_only");
}

function stopAllowsAlighting(direction, pos) {
    const role = directionStopRole(direction, pos);
    return !role.endsWith("_entry_only");
}

function normalizedStopName(stopIndex) {
    const stop = stops[stopIndex];
    return stop && stop.name
        ? stop.name.trim().toLocaleLowerCase()
        : "";
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
                    boardPos: boardPos
                });
            }
        }
    }

    boardingOptionsCache.set(stopIndex, result);
    return result;
}

function routeEdgeMetrics(routeKey, direction, pos) {
    const key =
        routeKey +
        "|" +
        relationKey(direction) +
        "|" +
        String(pos);
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
        rideStops: 1
    };
    const metrics = legEstimatedMinutes(edge);
    routeEdgeMetricsCache.set(key, metrics);
    return metrics;
}
