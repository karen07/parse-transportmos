/* Shared map and loader theme, selected before first paint. */
const transportBootTheme = (() => {
    const root = document.documentElement;
    const key = 'transport-map-boot-theme';
    const valid = (value) => value === 'light' || value === 'dark';
    const requested = new URL(location.href).searchParams.get('theme');
    let saved;
    try {
        saved = localStorage.getItem(key);
    } catch (_) {
        /* No storage. */
    }
    root.dataset.bootTheme = valid(requested) ? requested : valid(saved) ? saved : 'light';

    const initialUrl = new URL(location.href);
    initialUrl.searchParams.set('theme', root.dataset.bootTheme);
    if (initialUrl.href !== location.href) history.replaceState(history.state, '', initialUrl.href);

    function updateButtons() {
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.content = root.dataset.bootTheme === 'dark' ? '#303640' : '#ffffff';
        for (const button of document.querySelectorAll('[data-theme-value]')) {
            button.setAttribute(
                'aria-pressed',
                String(button.dataset.themeValue === root.dataset.bootTheme),
            );
        }
    }
    function select(value) {
        if (!valid(value)) return;
        if (root.dataset.bootTheme === value) return;
        root.dataset.bootTheme = value;
        const url = new URL(location.href);
        try {
            localStorage.setItem(key, value);
            url.searchParams.set('theme', value);
        } catch (_) {
            url.searchParams.set('theme', value);
        }
        // Keep the URL override consistent without restarting the map or loader.
        if (url.href !== location.href) history.replaceState(history.state, '', url.href);
        updateButtons();
        document.dispatchEvent(new Event('transport-theme-change'));
    }
    document.addEventListener(
        'DOMContentLoaded',
        () => {
            updateButtons();
            for (const button of document.querySelectorAll('[data-theme-value]')) {
                button.addEventListener('click', () => select(button.dataset.themeValue));
            }
        },
        { once: true },
    );
    return {
        get dark() {
            return root.dataset.bootTheme === 'dark';
        },
    };
})();
