const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'site-osmlab', 'routes.js'), 'utf8');
const start = src.indexOf('function waitForBaseMapReady(');
const end = src.indexOf('\nfunction loadTransportData()', start);
assert(start >= 0 && end > start);
const ctx = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
};
vm.createContext(ctx);
vm.runInContext(src.slice(start, end), ctx);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function makeMap() {
    const listeners = new Map();
    let visible = false;
    let tilesLoaded = false;
    const gl = {
        on(name, cb) {
            if (!listeners.has(name)) listeners.set(name, new Set());
            listeners.get(name).add(cb);
        },
        off(name, cb) {
            listeners.get(name)?.delete(cb);
        },
        emit(name, extra = {}) {
            for (const cb of [...(listeners.get(name) || [])]) cb({ type: name, ...extra });
        },
        areTilesLoaded: () => tilesLoaded,
        getCanvas: () => ({ width: 1024, height: 768 }),
        queryRenderedFeatures: () => (visible ? [{ type: 'Feature' }] : []),
        setVisible(v = true) {
            visible = v;
        },
        setTilesLoaded(v = true) {
            tilesLoaded = v;
        },
        handlerCount: () => [...listeners.values()].reduce((sum, ls) => sum + ls.size, 0),
    };
    return gl;
}
async function run() {
    // All MapLibre events including idle may fire on an empty background.
    const empty = makeMap();
    const p1 = ctx.waitForBaseMapReady({ getMaplibreMap: () => empty }, 1400);
    let resolved = false;
    p1.then(() => {
        resolved = true;
    }).catch(() => {});
    empty.setTilesLoaded();
    empty.emit('load');
    empty.emit('idle');
    await sleep(500);
    assert.equal(resolved, false, 'do not show an empty map even after idle');
    empty.setVisible();
    empty.emit('render'); // Real rendered vector features.
    await p1;
    assert.equal(empty.handlerCount(), 0, 'listeners must be removed');

    // Cached map, no subsequent load/idle/render events.
    const cached = makeMap();
    cached.setVisible();
    cached.setTilesLoaded();
    await ctx.waitForBaseMapReady({ getMaplibreMap: () => cached }, 1600);
    assert.equal(cached.handlerCount(), 0);

    // Safari case: a valid vector map paints but idle NEVER fires.
    const partial = makeMap();
    const startAt = Date.now();
    const p2 = ctx.waitForBaseMapReady({ getMaplibreMap: () => partial }, 5000);
    partial.setVisible();
    partial.emit('render');
    await p2;
    assert(Date.now() - startAt >= 2400, 'partial tiles require stable visible frame grace period');
    assert.equal(partial.handlerCount(), 0);

    // No visible tiles at all: show a bounded failure with a useful error.
    const broken = makeMap();
    const p3 = ctx.waitForBaseMapReady({ getMaplibreMap: () => broken }, 110);
    broken.emit('error', { error: new Error('HTTP 404') });
    await assert.rejects(p3, /HTTP 404/);
    assert.equal(broken.handlerCount(), 0);
    console.log('All MapLibre readiness tests passed (empty, cached, no idle, timeout).');
}
run().catch((e) => {
    console.error(e);
    process.exit(1);
});
