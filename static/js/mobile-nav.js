// Mobile navbar behavior: hamburger toggle + tap-driven dropdowns.
// Desktop (hover-capable AND wide) is unaffected — the hover CSS rule
// handles dropdowns there, and the hamburger button is hidden via CSS.
// The trigger condition here must match styles.css's hamburger-mode media
// query exactly: (max-width: 900px), (hover: none) — a touch-primary device
// wider than 900px (tablet, large phone landscape) has no hover, so it needs
// the tap-toggle path even though it isn't "narrow".
document.addEventListener('DOMContentLoaded', function () {
    var navbar = document.querySelector('.navbar');
    var toggle = document.querySelector('.nav-toggle');
    if (!navbar || !toggle) return;

    var mobileNavQuery = '(max-width: 900px), (hover: none)';

    toggle.addEventListener('click', function () {
        var isOpen = navbar.classList.toggle('nav-open');
        toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    var dropbtns = navbar.querySelectorAll('.dropbtn');
    dropbtns.forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            if (!window.matchMedia(mobileNavQuery).matches) return;

            e.preventDefault();
            var parent = btn.closest('.nav-item');
            if (!parent) return;

            var wasOpen = parent.classList.contains('open');

            navbar.querySelectorAll('.nav-item.open').forEach(function (item) {
                item.classList.remove('open');
            });

            if (!wasOpen) {
                parent.classList.add('open');
            }
        });
    });
});
