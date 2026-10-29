const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'web', 'routes.js'), 'utf8');
const start = src.indexOf('let yieldMessageChannel = null;');
const end = src.indexOf('\nasync function scanRaptorRoundAsync(', start);
assert(start >= 0 && end > start, 'yield implementation exists');
const implementation = src.slice(start, end);

async function run() {
    let timerCalls = 0;
    let channelCount = 0;
    class FakeMessageChannel {
        constructor() {
            channelCount++;
            this.port1 = { onmessage: null };
            this.port2 = {
                postMessage: () => queueMicrotask(() => this.port1.onmessage({ data: 0 })),
            };
        }
    }
    const channelCtx = {
        MessageChannel: FakeMessageChannel,
        setTimeout: (fn) => {
            timerCalls++;
            setTimeout(fn, 0);
        },
    };
    vm.createContext(channelCtx);
    vm.runInContext(implementation + '\nthis.yieldFn = eventLoopYield;', channelCtx);
    await Promise.all(Array.from({ length: 50 }, () => channelCtx.yieldFn()));
    assert.equal(channelCount, 1, 'reuse a single channel');
    assert.equal(timerCalls, 0, 'Safari path must not use setTimeout');

    let schedulerCalls = 0;
    const chromium = {
        scheduler: {
            yield: async () => {
                schedulerCalls++;
            },
        },
        MessageChannel: FakeMessageChannel,
        setTimeout: () => {
            throw Error('timer not expected');
        },
    };
    vm.createContext(chromium);
    vm.runInContext(implementation + '\nthis.yieldFn = eventLoopYield;', chromium);
    await chromium.yieldFn();
    assert.equal(schedulerCalls, 1, 'prefer scheduler.yield when present');
    assert.equal(channelCount, 1, 'do not allocate a channel in Chromium');

    let fallbackCalls = 0;
    const fallback = {
        setTimeout: (fn) => {
            fallbackCalls++;
            queueMicrotask(fn);
        },
    };
    vm.createContext(fallback);
    vm.runInContext(implementation + '\nthis.yieldFn = eventLoopYield;', fallback);
    await fallback.yieldFn();
    assert.equal(fallbackCalls, 1, 'setTimeout is last resort');
    console.log(
        'All eventLoopYield tests passed (Safari MessageChannel, Chrome scheduler.yield, fallback).',
    );
}
run().catch((e) => {
    console.error(e);
    process.exit(1);
});
