/* Load one locale catalog before the application starts. No map data is duplicated. */
const transportI18n = (() => {
    const api = { messages: {}, language: 'ru' };
    const version = document.querySelector('meta[name="application-version"]').content;
    const resource = (name) =>
        new URL('i18n/' + name + '?v=' + encodeURIComponent(version), document.baseURI);
    async function read(name) {
        const response = await fetch(resource(name));
        if (!response.ok) throw new Error('Locale resource HTTP ' + response.status);
        return response.json();
    }
    api.t = (key, values = {}) =>
        (api.messages[key] ?? key).replace(/\{(\w+)\}/g, (match, name) =>
            Object.hasOwn(values, name) ? String(values[name]) : match,
        );
    let statusKeys = new Map();
    api.label = (value) => {
        if (statusKeys.has(value)) return api.t(statusKeys.get(value));
        const round = /^RAPTOR round (\d+)$/.exec(value);
        if (round) return api.t('round', { round: round[1] });
        const walk = /^Walking transfers after round (\d+)$/.exec(value);
        if (walk) return api.t('roundWalk', { round: walk[1] });
        return value;
    };
    function applyPage() {
        document.documentElement.lang = api.language;
        for (const element of document.querySelectorAll('[data-i18n]')) {
            if (element.dataset.i18nDynamic === 'true') continue;
            const node = Array.from(element.childNodes).find(
                (item) => item.nodeType === 3 && item.textContent.trim(),
            );
            const value = api.t(element.dataset.i18n, {
                speed: document.documentElement.dataset.walkSpeed,
            });
            if (node) node.textContent = node.textContent.replace(/\S[\s\S]*\S|\S/, () => value);
            else element.textContent = value;
        }
        for (const attr of ['title', 'aria-label']) {
            for (const element of document.querySelectorAll('[data-i18n-' + attr + ']')) {
                element.setAttribute(attr, api.t(element.getAttribute('data-i18n-' + attr)));
            }
        }
    }
    function updateSwitches() {
        for (const button of document.querySelectorAll('[data-lang]')) {
            button.setAttribute('aria-pressed', String(button.dataset.lang === api.language));
        }
    }
    function bindSwitches(manifest) {
        for (const group of document.querySelectorAll('[data-language-switch]')) {
            for (const entry of manifest) {
                let button = group.querySelector(`[data-lang="${entry.code}"]`);
                if (!button) {
                    button = document.createElement('button');
                    button.type = 'button';
                    button.dataset.lang = entry.code;
                    button.lang = entry.code;
                    button.textContent = entry.code.toUpperCase();
                    group.append(button);
                }
                button.title = entry.name;
                button.setAttribute('aria-label', entry.name);
                button.setAttribute('aria-pressed', String(entry.code === api.language));
                button.addEventListener('click', () => {
                    if (entry.code !== api.language) api.select(entry.code).catch(console.error);
                });
            }
        }
    }
    let manifest = [];
    const catalogs = new Map();
    let selection = 0;
    async function applyLanguage(code, persist = false) {
        if (!manifest.some((entry) => entry.code === code)) return;
        const token = ++selection;
        if (!catalogs.has(code)) catalogs.set(code, await read(code + '.json'));
        if (token !== selection) return;
        // Keep the object identity: the routing UI retains a reference to it.
        Object.assign(api.messages, catalogs.get('ru').messages, catalogs.get(code).messages);
        api.language = code;
        if (persist) transportLanguage.select(code);
        applyPage();
        updateSwitches();
        document.dispatchEvent(new Event('transport-language-change'));
    }
    api.select = (code) => applyLanguage(code, true);
    api.ready = (async () => {
        const [entries, english, russian] = await Promise.all([
            read('index.json'),
            read('en.json'),
            read('ru.json'),
        ]);
        manifest = entries;
        catalogs.set('ru', russian);
        catalogs.set('en', english);
        statusKeys = new Map(Object.entries(english.messages).map(([key, value]) => [value, key]));
        bindSwitches(manifest);
        try {
            await applyLanguage(transportLanguage.language);
        } catch (error) {
            console.warn('[i18n] Falling back to Russian:', error);
            await applyLanguage('ru');
        }
    })().finally(() => {
        delete document.documentElement.dataset.localePending;
    });
    return api;
})();
