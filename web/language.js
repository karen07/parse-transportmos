/* Select the locale before first paint; Russian is the project default. */
const transportLanguage = (() => {
    const root = document.documentElement;
    const key = 'transport-map-language';
    const available = root.dataset.languages.split(' ');
    const normalize = (value) => {
        const code = (value || '').toLowerCase();
        return available.includes(code) ? code : code.split('-')[0];
    };
    const valid = (value) => available.includes(value);
    const requested = normalize(new URL(location.href).searchParams.get('lang'));
    let saved;
    try {
        saved = normalize(localStorage.getItem(key));
    } catch (_) {
        /* No storage. */
    }
    let language = valid(requested) ? requested : valid(saved) ? saved : 'ru';
    const initialUrl = new URL(location.href);
    initialUrl.searchParams.set('lang', language);
    if (initialUrl.href !== location.href) history.replaceState(history.state, '', initialUrl.href);
    if (language !== 'ru') root.dataset.localePending = 'true';
    function select(code) {
        if (!valid(code)) return;
        const next = new URL(location.href);
        try {
            localStorage.setItem(key, code);
            next.searchParams.set('lang', code);
        } catch (_) {
            next.searchParams.set('lang', code);
        }
        language = code;
        history.replaceState(history.state, '', next.href);
    }
    return {
        get language() {
            return language;
        },
        select,
    };
})();
