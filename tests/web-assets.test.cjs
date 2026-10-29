const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const site = path.join(root, 'site-osmlab');
const source = path.join(root, 'web');

function text(dir, file) {
    return fs.readFileSync(path.join(dir, file), 'utf8');
}

test('web scripts and styles are copied from standalone sources', () => {
    for (const file of [
        'routes.js',
        'routes.css',
        'routes-data-worker.js',
        'boot-loader.js',
        'boot-loader.css',
    ]) {
        assert.deepEqual(
            fs.readFileSync(path.join(site, file)),
            fs.readFileSync(path.join(source, file)),
            `Site asset differs from source: ${file}`,
        );
    }
});

test('generated HTML loads separate sources and resolved config', () => {
    const html = text(site, 'routes.html');
    const config = JSON.parse(text(site, 'routes-config.json'));
    assert.doesNotMatch(html, /@@[A-Z_]+@@/);
    assert.match(html, /src="boot-loader\.js\?v=[0-9.]+" defer/);
    assert.match(html, /src="routes\.js\?v=[0-9.]+" defer/);
    assert.match(html, /href="boot-loader\.css\?v=[0-9.]+"/);
    assert.match(html, /name="transport-config" content="routes-config\.json"/);
    assert.equal(config.data_file, 'routes.json.gz');
    assert.equal(config.data_worker_file, 'routes-data-worker.js');
    assert.equal(config.maplibre_worker_file, 'routes-maplibre-worker.js');
    assert.equal(config.version, '0.15.12');
});

test('loader shows no invented percentages and credits OpenStreetMap', () => {
    const html = text(site, 'routes.html');
    const loader = text(source, 'boot-loader.js');
    assert.match(html, /id="boot-network"/);
    assert.doesNotMatch(html, /ascii\.rest|Night Highway/);
    assert.match(html, /OpenStreetMap contributors/);
    assert.match(loader, /bootPhase\(stage, detail\)/);
    assert.match(loader, /is-indeterminate/);
    assert.match(loader, /if \(total > 0\)/);
});

test('road loader uses one stroke and propagates from geographic center', () => {
    const loader = text(source, 'boot-loader.js');
    assert.match(loader, /const roadWidth = 1\.55/);
    assert.doesNotMatch(loader, /widthByClass|colorByClass|lastSpawn|candidates/);
    assert.match(loader, /mask\.globalCompositeOperation = 'source-in'/);
    assert.match(loader, /createRadialGradient\(w \/ 2, h \/ 2, 0,/);
    assert.match(loader, /const phase = \(\(now - waveStart\) % waveDuration\)/);
});
