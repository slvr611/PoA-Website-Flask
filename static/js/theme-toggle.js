// Dark/light theme toggle. The actual attribute is set as early as possible
// by an inline script in layout.html's <head> (before this file even loads)
// to avoid a flash of the wrong theme on repeat visits; this file just wires
// up the button so the user can flip it, and keeps localStorage in sync.
document.addEventListener('DOMContentLoaded', function () {
    var btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;

    var root = document.documentElement;

    function currentTheme() {
        return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    }

    function updateButton() {
        var isLight = currentTheme() === 'light';
        btn.innerHTML = isLight ? '&#9788;' : '&#9789;';
        btn.title = isLight ? 'Switch to dark mode' : 'Switch to light mode';
    }

    btn.addEventListener('click', function () {
        var next = currentTheme() === 'light' ? 'dark' : 'light';
        root.setAttribute('data-theme', next);
        try { localStorage.setItem('poa-theme', next); } catch (e) {}
        updateButton();
    });

    updateButton();
});
