(function() {
    'use strict';

    var API = '/wp-json/kairui/v1/track';
    var BATCH_INTERVAL = 5000;
    var BATCH_MAX = 20;
    var HEARTBEAT = 15000;

    var queue = [];
    var lastPage = location.pathname;
    var scrollMarks = {25: false, 50: false, 75: false, 100: false};

    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return m ? m[2] : '';
    }

    function enqueue(type, data) {
        var d = data || {};
        d.type = type;
        d.ts = Date.now();
        queue.push(d);
        if (queue.length >= BATCH_MAX) flush();
    }

    function flush() {
        if (!queue.length) return;
        var batch = queue.splice(0);
        navigator.sendBeacon(API, JSON.stringify(batch));
    }

    // Page view
    enqueue('page_view', { url: lastPage });

    // Product view detection
    var body = document.body;
    if (body.classList.contains('single-product') || body.classList.contains('product-template-default')) {
        var pidEl = document.querySelector('[data-product_id]') || document.querySelector('input[name="add-to-cart"]');
        if (pidEl) {
            var pid = parseInt(pidEl.dataset ? pidEl.dataset.product_id : pidEl.value) || 0;
            var priceEl = document.querySelector('.price .amount, .product-price .amount, .woocommerce-Price-amount');
            var price = priceEl ? parseFloat(priceEl.textContent.replace(/[^0-9.]/g, '')) || 0 : 0;
            if (pid) enqueue('product_view', { product_id: pid, product_price: price });
        }
    }

    // Category view
    if (body.classList.contains('archive') || body.classList.contains('product-category')) {
        enqueue('category_view', { url: location.pathname });
    }

    // Cart page
    if (body.classList.contains('woocommerce-cart')) {
        enqueue('view_cart', { url: location.pathname });
    }

    // Checkout page
    if (body.classList.contains('woocommerce-checkout')) {
        enqueue('begin_checkout', { url: location.pathname });
    }

    // Search
    var searchForm = document.querySelector('.search-form, .woocommerce-product-search');
    if (searchForm) {
        searchForm.addEventListener('submit', function() {
            var q = searchForm.querySelector('input[type="search"], input[name="s"]');
            if (q && q.value) enqueue('search', { extra: { query: q.value } });
        });
    }

    // Scroll depth
    var sentinel = document.createElement('div');
    sentinel.style.cssText = 'position:absolute;top:0;left:0;width:1px;height:1px;pointer-events:none;z-index:-1';
    document.body.appendChild(sentinel);
    ['25','50','75','100'].forEach(function(pct) {
        sentinel.style.top = (document.body.scrollHeight * pct / 100) + 'px';
        var obs = new IntersectionObserver((function(entries) {
            if (entries[0].isIntersecting && !scrollMarks[this]) {
                scrollMarks[this] = true;
                enqueue('scroll_' + this, { url: lastPage });
            }
        }).bind(pct));
        obs.observe(sentinel);
    });

    // Heartbeat
    setInterval(function() { enqueue('heartbeat', { url: location.pathname }); }, HEARTBEAT);

    // Batch flush
    setInterval(flush, BATCH_INTERVAL);

    // Flush on exit
    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'hidden') flush();
    });

    // Exit intent
    document.addEventListener('mouseleave', function once(e) {
        if (e.clientY < 0) {
            enqueue('exit_intent', { url: location.pathname });
            document.removeEventListener('mouseleave', once);
        }
    });
})();
