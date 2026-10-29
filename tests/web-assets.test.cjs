const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const site = path.join(root, 'dist');
const source = path.join(root, 'web');

function text(dir, file) {
    return fs.readFileSync(path.join(dir, file), 'utf8');
}

test('application scripts and styles are copied from standalone sources', () => {
    for (const file of ['routes.js', 'routes.css', 'routes-data-worker.js', 'boot-loader.js']) {
        assert.deepEqual(
            fs.readFileSync(path.join(site, file)),
            fs.readFileSync(path.join(source, file)),
            `Site asset differs from source: ${file}`,
        );
    }
});

test('first paint is standalone and does not await a stylesheet or JS fetch', () => {
    const html = text(site, 'routes.html');
    const config = JSON.parse(text(site, 'routes-config.json'));
    const css = text(source, 'boot-loader.css')
        .split('\n')
        .filter((line) => line.trim())
        .join('\n');
    assert.doesNotMatch(html, /@@[A-Z_]+@@/);
    assert.match(html, /<style>\s*#boot-overlay/);
    assert.ok(html.includes(css), 'critical loader CSS must be inline');
    assert.match(html, /id="boot-roads"/);
    assert.match(html, /id="boot-road-path" d="\s*M\d+/);
    assert.match(html, /rel="preload" as="style"/);
    assert.doesNotMatch(html, /href="boot-loader\.css/);
    assert.match(html, /data-map-script="routes\.js\?v=[0-9.]+"/);
    assert.doesNotMatch(html, /<script src="routes\.js/);
    assert.match(html, /name="transport-config" content="routes-config\.json"/);
    assert.equal(config.data_file, 'routes.json.gz');
    assert.equal(config.data_worker_file, 'routes-data-worker.js');
    assert.equal(config.maplibre_worker_file, 'routes-maplibre-worker.js');
    assert.equal(config.version, '0.15.14');
});

test('loader does not fetch road JSON and does not fake progress', () => {
    const html = text(site, 'routes.html');
    const loader = text(source, 'boot-loader.js');
    assert.match(html, /OpenStreetMap contributors/);
    assert.doesNotMatch(html, /ascii\.rest|Night Highway/);
    assert.match(loader, /new Path2D\(geometry\)/);
    assert.doesNotMatch(loader, /fetch\('moscow-major-roads\.json'/);
    assert.match(loader, /if \(total > 0\)/);
    assert.match(loader, /requestAnimationFrame\(\(\) => requestAnimationFrame\(startMap\)\)/);
});
