// Test the actual cost function extracted from the browser source.
// No DOM or external map libraries required.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const js = fs.readFileSync(
    path.join(__dirname, "../transportmap/assets/map.js"), "utf8"
);
const start = js.indexOf("function stopChainDistanceMeters(leg)");
const end = js.indexOf("function prepareItineraryMetrics(", start);
assert.ok(start >= 0 && end > start);
const functions = js.slice(start, end);
const context = {
    routes: { "mcc:test": { mode: "mcc" }, "bus:7": { mode: "bus" } },
    stopDistanceMeters(a, b) {
        return Math.abs(a - b) * 1000;
    },
    legParts(leg) {
        return [leg];
    },
    routeSegmentGeometry() {
        throw new Error("routing must not call the rendering geometry");
    },
    routeSpeedMetersPerMin(key) {
        return key.startsWith("mcc") ? 780 : 320;
    },
    STOP_DELAY_MIN: 0.35
};
vm.createContext(context);
vm.runInContext(functions, context);
const leg = {
    routeKey: "mcc:test",
    direction: { stops: [1, 2, 3] },
    startPos: 0,
    endPos: 2,
    rideStops: 2
};
assert.equal(context.legDistanceMeters(leg), 2160);
const metrics = context.legEstimatedMinutes(leg);
assert.equal(metrics.distance, 2160);
assert.equal(metrics.minutes, 2160 / 780 + 2 * 0.35);
console.log("Graph costs do not depend on rendered geometry: OK");
