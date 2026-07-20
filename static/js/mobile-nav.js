// Mobile navbar behavior: hamburger toggle + tap-driven dropdowns.
// Desktop (hover-capable, wide viewport) is unaffected — the hover CSS rule
// handles dropdowns there, and the hamburger button is hidden via CSS.
document.addEventListener('DOMContentLoaded', function () {
    var navbar = document.querySelector('.navbar');
    var toggle = document.querySelector('.nav-toggle');
    if (!navbar || !toggle) return;

    toggle.addEventListener('click', function () {
        var isOpen = navbar.classList.toggle('nav-open');
        toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    var dropbtns = navbar.querySelectorAll('.dropbtn');
    dropbtns.forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            if (!window.matchMedia('(max-width: 900px)').matches) return;

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
