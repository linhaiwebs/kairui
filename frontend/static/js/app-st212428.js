const { createApp, ref, reactive, computed, onMounted, watch, nextTick } = Vue;

const app = createApp({
    setup() {
        const isLoggedIn = ref(false);
        const currentUser = ref('');
        const currentUserRole = ref('');
        const currentUserId = ref(null);
        const currentPanelEnv = ref(null);
        const currentPage = ref('dashboard');
        const loading = ref(false);
        const toast = reactive({ show: false, message: '', type: 'success' });
        const modal = reactive({ show: false, title: '', content: '', onConfirm: null, loading: false, progress: '' });
        const loginForm = reactive({ username: '', password: '', tab: 'admin' });
        const loginError = ref('');
        const sites = ref([]);
        const searchQuery = ref('');
        const filteredSites = computed(() => {
            if (!searchQuery.value) return sites.value;
            const q = searchQuery.value.toLowerCase();
            return sites.value.filter(s =>
                (s.site_name || '').toLowerCase().includes(q) ||
                (s.url || '').toLowerCase().includes(q) ||
                (s.tag || '').toLowerCase().includes(q)
            );
        });
        const panelConnected = ref(false);
        const panelWebsites = ref([]);
        const panelInstalledApps = ref([]);
        const panelGroups = ref([]);

        // ---- 3-Step Wizard ----
        const wizardStep = ref(1);
        const wizardOpen = ref(false);
        const wizardMode = ref('single');
        const wizardSiteId = ref(null);
        const createForm = reactive({
            site_name: '', url: '', admin_name: 'admin', admin_password: '',
            tag: '', security_id: '', http_username: '', http_password: '',
            verify_certificate: true, ssl_version: 'auto',
            domains: '', base_port: 8081, db_service: 'mariadb', website_group_id: 1,
        });
        const createProgress = reactive({ show: false, message: '', results: [] });
        const wpInstallStatuses = reactive({});
        const wpPollingTimers = reactive({});

        // Step 1 - Cloudflare DNS + Brand Kit
        const cfConnected = ref(false);
        const cfToken = ref('');
        const cfAccounts = ref([]);
        const cfSelectedAccountId = ref('');
        const wizardBrandKitId = ref(null);
        const brandKitsForWizard = ref([]);
        const profileTestState = ref({ testing: false, result: null, message: '' });

        // Edit
        const showEditModal = ref(false);
        const editForm = reactive({});
        const editingSiteId = ref('');

        // Feed Products (GMC)
        const feedSiteId = ref('');
        const feedProducts = ref([]);
        const showFeedProductModal = ref(false);
        const feedEditId = ref(null);
        const feedEditForm = reactive({
            title: '', description: '', price: '', currency: 'USD',
            availability: 'in_stock', brand: '', gtin: '', mpn: '',
            google_product_category: '', product_type: '',
            image_url: '', link: '', condition: 'new', shipping: '',
        });

        // 筛品 - collapsible menu & sub-pages
        const feedMenuOpen = ref(false);
        const sourceTab = ref('walmart');
        const feedStats = ref(null);
        const feedStatsLoading = ref(false);

        // 网站产品 Sales Stats page
        const wooStats = ref(null);
        const wooStatsLoading = ref(false);
        const wooStatsPeriod = ref('month');
        const wooStatsDateMin = ref('');
        const wooStatsDateMax = ref('');

        // 筛品 - Walmart tab state
        const walmartCategories = ref([]);  // grouped: [{group, items: [{key, label, url, cached_count}]}]
        const walmartSelectedCategory = ref('');
        const walmartProducts = ref([]);
        const walmartLoading = ref(false);
        const walmartError = ref('');
        const walmartFetchLimit = ref(0);  // 0 = fetch all
        const walmartEnriching = ref(false);
        const walmartEnrichProgress = ref('');
        const generatedFeed = ref([]);
        const walmartPage = ref(1);
        const walmartPerPage = ref(20);
        const walmartPagedProducts = computed(() => {
            const src = Array.isArray(walmartProducts.value) ? walmartProducts.value : [];
            const start = (walmartPage.value - 1) * walmartPerPage.value;
            return src.slice(start, start + walmartPerPage.value);
        });
        const walmartTotalPages = computed(() => {
            const src = Array.isArray(walmartProducts.value) ? walmartProducts.value : [];
            const perPage = walmartPerPage.value || 20;
            const pages = Math.ceil(src.length / perPage);
            return (Number.isFinite(pages) && pages > 0) ? pages : 1;
        });

        // 筛品 - 爆品导入 tab state
        const amazonSearchResults = ref([]);
        const amazonSearchLoading = ref(false);
        const amazonSearchError = ref('');
        const amazonSelectedIndices = ref(new Set());
        const amazonSearchProgress = ref('');
        const showAmazonImportModal = ref(false);
        const amazonImportModalText = ref('');
        const showAmazonUrlModal = ref(false);
        const amazonUrlModalText = ref('');
        const showConvertLogModal = ref(false);
        const convertLogLines = ref([]);  // [{ok, idx, title, error, id}]
        const convertLogProgress = ref('');
        const converting = ref(false);

        // Feed page state
        const feedSelectedIndices = ref(new Set());
        const showFeedDescModal = ref(false);
        const feedDescContent = ref('');

        // 网站产品 products page state
        const wooProducts = ref([]);
        const wooSelectedIndices = ref(new Set());
        const wooConverting = ref(false);
        const wooConvertProgress = ref('');

        // Site sync state (shared by Feed + Woo pages)
        const feedSyncSiteId = ref(null);
        const wooSyncSiteId = ref(null);
        const syncingFeed = ref(false);
        const syncingWoo = ref(false);
        const wooGeneratingFeed = ref(false);

        // Deploy progress overlay
        const deployOverlay = reactive({ show: false, message: '', domains: [], results: [], done: false,
            step: 'progress' });  // 'progress' | 'demo-import'
        const postInstallSite = ref(null);  // { site_id, domain, admin_name, admin_password }
        // Demo import state
        const demos = ref([]);
        const demosLoading = ref(false);
        const demoPluginReady = ref(false);
        const selectedDemoId = ref('');
        const selectedCategory = ref('all');
        const demoImporting = ref(false);
        const demoImportStatus = ref('');
        const demoCategories = computed(() => {
            const cats = new Set();
            demos.value.forEach(d => {
                (d.categories || []).forEach(c => cats.add(c));
            });
            return Array.from(cats);
        });
        const filteredDemos = computed(() => {
            if (selectedCategory.value === 'all') return demos.value;
            return demos.value.filter(d => (d.categories || []).includes(selectedCategory.value));
        });
        // Silent install tracking (per site_id)
        const silentInstallSites = reactive({});  // { site_id: { step: 'ai-config'|'cf-ssl', status: 'running'|'done'|'failed', message: '' } }
        // Pipeline timeline status (per site_id)
        const pipelineStatuses = reactive({});  // { site_id: { wp_deployed, demo_imported, demo_name, brand_configured, gmc_registered } }
        const tooltipSiteId = ref(null);
        // Meta tag injection modal
        const metaModal = reactive({
            show: false, site: null, siteId: null, metaTag: '', submitting: false,
        });
        // Environment selection modal (shown to operators after login)
        const envSelectModal = reactive({
            show: false, environments: [], loading: false,
            selectedEnvId: null, submitting: false,
        });
        // Demo import modal (triggered from timeline icon)
        const demoModal = reactive({
            show: false, site: null, siteId: null,
            demos: [], loading: false, selectedDemoId: '',
            importing: false, status: '', category: 'all',
        });
        const demoModalCategories = computed(() => {
            const cats = new Set();
            demoModal.demos.forEach(d => { (d.categories || []).forEach(c => cats.add(c)); });
            return Array.from(cats);
        });
        const demoModalFiltered = computed(() => {
            if (demoModal.category === 'all') return demoModal.demos;
            return demoModal.demos.filter(d => (d.categories || []).includes(demoModal.category));
        });
        // Unified Brand Config (merged AI + 网站产品 + Logo + Footer)
        const brandConfigBrandName = ref('');
        const brandConfigRunning = ref(false);
        const brandConfigError = ref('');
        const brandConfigSteps = ref([
            { label: 'AI 生成标题和副标题', status: 'pending', message: '' },
            { label: 'AI 生成站点图标', status: 'pending', message: '' },
            { label: '设置网站标题和副标题', status: 'pending', message: '' },
            { label: '上传站点图标', status: 'pending', message: '' },
            { label: '注册及角色设置', status: 'pending', message: '' },
            { label: '时区设置', status: 'pending', message: '' },
            { label: '保存 网站产品 配置', status: 'pending', message: '' },
            { label: '上传品牌 Logo', status: 'pending', message: '' },
            { label: '设置站点 Logo', status: 'pending', message: '' },
            { label: '保存页脚信息', status: 'pending', message: '' },
            { label: '配置税率', status: 'pending', message: '' },
            { label: '配置免费配送', status: 'pending', message: '' },
        ]);
        const brandConfigKey = ref('');
        const brandConfigSelectedKitId = ref(null);

        // Progress percentage computed
        const currentProgressPct = computed(() => {
            const total = deployOverlay.domains.length || 1;
            const done = deployOverlay.domains.filter(d => d.status === 'installed' || d.status === 'failed').length;
            return Math.round(done / total * 100);
        });
        const currentProgressLabel = computed(() => {
            const done = deployOverlay.domains.filter(d => d.status === 'installed' || d.status === 'failed').length;
            const fail = deployOverlay.domains.filter(d => d.status === 'failed').length;
            return `${done}/${deployOverlay.domains.length}${fail ? ` (${fail}失败)` : ''}`;
        });

        // Legacy - kept for backward compat
        const aiBrandName = ref('');
        const aiConfigRunning = ref(false);
        const aiConfigError = ref('');
        const aiConfigSteps = ref([]);
        const aiConfigKey = ref('');

        // Simplified 网站产品 form
        const wooConfigForm = reactive({
            address: '', city: '', country_state: '', postcode: '', allowed_countries: ''
        });
        const wooConfigSaving = ref(false);

        // Brand Kit Apply (step 5)
        const brandKitApplyStatus = ref({ running: false, steps: [], message: '' });
        const brandKitApplyConfigKey = ref('');
        const applyBrandKitForm = reactive({
            brand_kit_id: null, footer_address: '', footer_phone: '', footer_email: ''
        });
        const brandKitsForSelect = ref([]);
        const brandKitApplying = ref(false);

        // Config
        const globalConfig = reactive({
            default_admin_name: 'admin', default_admin_password: '',
            default_plugins: [], default_themes: [], db_service: 'mariadb',
        });
        const deepseekApiKeys = ref(['']);
        const deepseekConnected = ref(false);
        const deepseekVisibleKeys = reactive({});
        const deepseekKeyErrors = reactive({});
        const crawlbaseApiKeys = ref(['']);
        const crawlbaseConnected = ref(false);
        const crawlbaseVisibleKeys = reactive({});
        const crawlbaseKeyErrors = reactive({});
        const cloakbrowserProfiles = ref([]);
        // Google MC
        const mcRegistering = ref({});
        const mcFeedUrls = ref({});
        const mcProfileDir = ref('');
        // GMC 任务日志窗口
        const taskLogVisible = ref(false);
        const taskLogTitle = ref('');
        const taskLogLines = ref([]);
        const taskLogStatus = ref('running');  // running | success | failed
        const taskLogResult = ref(null);
        const taskLogAfter = ref(0);
        const taskLogPollTimer = ref(null);
        const taskLogRef = ref(null);  // DOM ref for auto-scroll
        const fingerprintEnabled = computed({
            get: () => globalConfig.fingerprint_enabled === 'true',
            set: (val) => { globalConfig.fingerprint_enabled = val ? 'true' : 'false'; }
        });
        // 环境测试未完成时禁用创建按钮
        const envTestBlocking = computed(() => {
            if (!wizardBrandKitId.value) return false;
            const kit = brandKitsForWizard.value.find(k => k.id === wizardBrandKitId.value);
            if (!kit || !kit.cloakbrowser_profile_name) return false;
            return profileTestState.value.testing || profileTestState.value.result !== true;
        });

        // Brand Kits
        const brandKits = ref([]);
        const brandKitsLoading = ref(false);
        const showBrandKitModal = ref(false);
        const brandKitEditId = ref(null);
        const brandKitForm = reactive({
            name: '', industry: '', proxy: '', proxy_id: null, google_account_id: null, cloakbrowser_profile_name: ''
        });
        // Proxy Pool
        const proxies = ref([]);
        const availableProxies = ref([]);
        const importingProxies = ref(false);
        const importingProxyText = ref('');
        const importingProxyType = ref('http');

        // Google Account Pool
        const googleAccounts = ref([]);
        const availableGoogleAccounts = ref([]);
        const importingGoogleAccounts = ref(false);
        const googleAccountsText = ref('');

        const brandKitGenerating = reactive({});
        // User Management
        const users = ref([]);
        const showUserModal = ref(false);
        const userEditId = ref(null);
        const userForm = reactive({ username: '', password: '', role: 'operator', panel_environment_id: null });
        const userFormError = ref('');
        // Fingerprint Categories & Profile Mapping
        const fingerprintCategories = ref([]);
        const activeFingerprintCategory = ref(null);
        const importingFingerprintText = ref('');
        const importingFingerprints = ref(false);
        const importFingerprintResult = ref('');
        const importFileInput = ref(null);

        const profileCategories = ref([]);  // profile_name → category_id mapping
        const newCategoryName = ref('');
        // Settings Tabs
        const settingsActiveTab = ref('wordpress');
        const settingsTabs = [
            { key: 'wordpress', label: 'WordPress 默认' },
            { key: 'panel', label: '1Panel 环境' },
            { key: 'agnes', label: 'Agnes AI' },
            { key: 'deepseek', label: 'DeepSeek API' },
            { key: 'crawlbase', label: 'Crawlbase' },
            { key: 'cloudflare', label: 'Cloudflare' },
            { key: 'google_account', label: '谷歌账户' },
            { key: 'fingerprint', label: '指纹环境' },
        ];
        const agnesApiKey = ref('');
        const agnesVisibleKey = ref(false);
        const agnesVerifying = ref(false);
        const agnesConnected = ref(false);
        const panelEnvironments = ref([]);
        const showPanelEnvModal = ref(false);
        const panelEnvEditId = ref(null);
        const panelEnvForm = reactive({ name: '', host: '', port: 3500, api_key: '', cf_account_id: null });
        const panelEnvFormError = ref('');
        const showBrandKitDetail = ref(false);
        const brandKitDetail = ref(null);
        const brandKitDetailTab = ref('info');
        const brandKitWooForm = reactive({
            address: '', city: '', country_state: '', postcode: '', allowed_countries: ''
        });
        const brandKitFooterForm = reactive({
            address: '', phone: '', email: ''
        });
        const brandKitTaxForm = reactive({
            tax_enabled: true,
            prices_include_tax: false,
            tax_rate_name: '',
            tax_rate: '',
            tax_rate_country: 'US',
            tax_rate_state: '',
        });
        const brandKitShippingForm = reactive({
            zone_name: 'Free Shipping',
            country: 'US',
            min_amount: '',
        });
        const brandKitConfigSaving = ref(false);

        // ---- Utility ----
        function showToast(message, type = 'success') {
            toast.message = message; toast.type = type; toast.show = true;
            setTimeout(() => { toast.show = false; }, 3000);
        }
        function showModal(title, content, onConfirm) {
            modal.title = title; modal.content = content; modal.onConfirm = onConfirm; modal.show = true;
        }
        // ---- Auth ----
        async function handleLogin() {
            loginError.value = ''; loading.value = true;
            try {
                const resp = await API.login(loginForm.username, loginForm.password);
                if (resp.code === 200) {
                    isLoggedIn.value = true; currentUser.value = resp.data.username;
                    currentUserRole.value = resp.data.role || ''; currentUserId.value = resp.data.user_id || null;
                    currentPanelEnv.value = resp.data.panel_environment || null;
                    showToast('登录成功');
                    if (resp.data.role === 'operator') {
                        openEnvSelectModal();
                    } else {
                        loadInitialData();
                    }
                }
                else { loginError.value = resp.message || '用户名或密码错误'; }
            } catch (e) { loginError.value = '连接错误'; } finally { loading.value = false; }
        }
        function handleLogout() { API.logout(); isLoggedIn.value = false; currentUser.value = ''; currentUserRole.value = ''; currentUserId.value = null; currentPanelEnv.value = null; currentPage.value = 'dashboard'; }

        // ---- Environment Selection Modal (operator) ----
        async function openEnvSelectModal() {
            envSelectModal.show = true;
            envSelectModal.loading = true;
            envSelectModal.environments = [];
            envSelectModal.selectedEnvId = null;
            try {
                const resp = await API.request('GET', '/api/panel/environments');
                if (resp.code === 200) envSelectModal.environments = resp.data || [];
            } catch (e) {}
            envSelectModal.loading = false;
        }
        async function submitEnvSelection() {
            if (!envSelectModal.selectedEnvId) return;
            envSelectModal.submitting = true;
            try {
                const resp = await API.request('PUT', '/api/user/panel-environment', {
                    panel_environment_id: envSelectModal.selectedEnvId,
                });
                if (resp.code === 200) {
                    currentPanelEnv.value = resp.data.panel_environment || null;
                    envSelectModal.show = false;
                    loadInitialData();
                } else {
                    showToast(resp.message || '设置失败', 'error');
                }
            } catch (e) { showToast('设置失败', 'error'); }
            envSelectModal.submitting = false;
        }

        // ---- Data ----
        async function loadInitialData() {
            loading.value = true;
            try { await Promise.all([loadSites(), checkPanelStatus(), loadPanelData(), loadConfig(), loadCfAccounts(), checkCfStatus(), loadWalmartCategories()]); }
            finally { loading.value = false; }
        }
        async function loadSites() {
            try { const resp = await API.getSites(); if (resp.code === 200) sites.value = resp.data || []; } catch (e) {}
            // Load pipeline status for all sites
            sites.value.forEach(s => loadPipelineStatus(s.id));
            // Load MC feed URLs for GMC automation page
            sites.value.forEach(s => loadMCStatusForSite(s));
        }
        // Pipeline timeline helpers
        async function loadPipelineStatus(siteId) {
            try {
                const resp = await API.request('GET', `/api/sites/${siteId}/pipeline-status`);
                if (resp.code === 200) {
                    // Preserve runtime-only fields that the server doesn't return
                    const prev = pipelineStatuses[siteId] || {};
                    pipelineStatuses[siteId] = { ...resp.data, demo_importing: prev.demo_importing, silent_step: prev.silent_step };
                }
            } catch (e) {}
        }
        async function refreshPipelineStatus(siteId) {
            await loadPipelineStatus(siteId);
        }
        function stitchProgressTitle(site) {
            const s = pipelineStatuses[site.id] || {};
            const progress = s.stitch_screen_progress || [];
            if (!progress.length) {
                if (s.design_complete) return '设计完成' + (s.design_label ? ' (' + s.design_label + ')' : '');
                if (s.design_generating) return 'Stitch AI正在生成设计...';
                return '商城设计';
            }
            const done = progress.filter(p => p.status === 'complete').length;
            const total = progress.length;
            let title = '设计进度 (' + done + '/' + total + '):\n';
            for (const p of progress) {
                const icon = p.status === 'complete' ? '✅' : p.status === 'generating' ? '🔄' : '⏳';
                title += icon + ' ' + p.name + '\n';
            }
            return title;
        }
        function siteStatusText(site) {
            const s = pipelineStatuses[site.id] || {};
            const w = wpInstallStatuses[site.id];
            const isStatic = s.site_type === 'static';
            if (s.gmc_registered && !isStatic) return '已完成';
            if (isStatic) {
                // 4-step flow: DNS → 1Panel创建 → 设计生成 → 上传文件 → 上线
                if (s.files_uploaded) return '已上线';
                if (s.design_complete) return '正在上传文件...';
                if (s.design_generating) return 'Stitch AI生成设计中...';
                if (s.design_started) return '正在生成设计...';
                if (s.site_created) return '正在生成页面...';
                if (s.dns_resolved) return '正在创建站点...';
                return '等待部署';
            }
            if (s.brand_configured) {
                if (s.silent_step === 'cf-ssl') return '正在安装SSL...';
                return '处理中...';
            }
            if (s.demo_imported) {
                if (s.silent_step === 'ai-config') return '正在配置品牌...';
                return '处理中...';
            }
            if (s.demo_importing) return '正在导入演示...';
            if (s.wp_deployed) return '点击导入演示';
            if (w && w.status === 'installing') return '正在安装WordPress...';
            return '等待部署';
        }
        function pipelineLineState(site, stage) {
            const s = pipelineStatuses[site.id] || {};
            const isStatic = s.site_type === 'static';
            if (isStatic) {
                // Static: stage1 (create) → stage2 (upload) → stage3 (brand)
                if (stage === 'stage1') {
                    if (s.files_uploaded || s.site_created) return 'done';
                    if (s.dns_resolved) return 'connecting';
                    return 'inactive';
                }
                if (stage === 'stage2') {
                    if (s.files_uploaded) return 'done';
                    if (s.site_created && !s.files_uploaded) return 'connecting';
                    return 'inactive';
                }
                if (stage === 'stage3') {
                    if (s.brand_configured || s.gmc_registered) return 'done';
                    if (s.files_uploaded && !s.brand_configured) return 'connecting';
                    return 'inactive';
                }
                return 'inactive';
            }
            // WordPress: demo / kit / gmc
            if (stage === 'demo') {
                if (s.demo_imported) return 'done';
                if (s.wp_deployed && !s.demo_imported) return 'connecting';
                return 'inactive';
            }
            if (stage === 'kit') {
                if (s.brand_configured) return 'done';
                if (s.demo_imported && !s.brand_configured) return 'connecting';
                return 'inactive';
            }
            if (stage === 'gmc') {
                if (s.gmc_registered) return 'done';
                if (s.brand_configured && !s.gmc_registered) return 'connecting';
                return 'inactive';
            }
            return 'inactive';
        }
        // Demo import modal (triggered from timeline icon)
        async function openDemoImportForSite(site) {
            demoModal.site = site;
            demoModal.siteId = site.id;
            demoModal.show = true;
            demoModal.loading = true;
            demoModal.selectedDemoId = '';
            demoModal.importing = false;
            demoModal.status = '';
            demoModal.category = 'all';
            try {
                const resp = await API.getPrebuiltDemos(site.id);
                if (resp.code === 200) demoModal.demos = resp.data || [];
            } catch (e) { demoModal.demos = []; }
            demoModal.loading = false;
        }
        async function startDemoImportFromModal() {
            if (!demoModal.selectedDemoId || demoModal.importing) return;
            const siteId = demoModal.siteId;
            const demoId = demoModal.selectedDemoId;
            // Close modal immediately, run import in background
            demoModal.show = false;
            demoModal.importing = true;
            demoModal.status = '';
            if (!pipelineStatuses[siteId]) pipelineStatuses[siteId] = {};
            pipelineStatuses[siteId].demo_importing = true;
            showToast('正在导入演示数据...');
            (async () => {
                try {
                    await API.importPrebuiltDemo(siteId, demoId);
                    let attempts = 0;
                    let stopped = false;
                    const poll = setInterval(async () => {
                        if (stopped) return;
                        attempts++;
                        try {
                            const resp = await API.getPrebuiltDemoStatus(siteId, demoId);
                            if (stopped) return;
                            const data = resp.data || resp;
                            if (data.status === 'success') {
                                stopped = true;
                                clearInterval(poll);
                                demoModal.importing = false;
                                if (pipelineStatuses[siteId]) pipelineStatuses[siteId].demo_importing = false;
                                await refreshPipelineStatus(siteId);
                                startSilentInstall(siteId);
                            } else if (data.status === 'failed') {
                                stopped = true;
                                clearInterval(poll);
                                demoModal.importing = false;
                                if (!pipelineStatuses[siteId]) pipelineStatuses[siteId] = {};
pipelineStatuses[siteId].demo_importing = false;
                                showToast('演示导入失败: ' + (data.message || '未知错误'), 'error');
                            }
                        } catch (e) {}
                        if (!stopped && attempts >= 60) {
                            stopped = true;
                            clearInterval(poll);
                            demoModal.importing = false;
                            if (!pipelineStatuses[siteId]) pipelineStatuses[siteId] = {};
pipelineStatuses[siteId].demo_importing = false;
                            showToast('演示导入超时', 'error');
                        }
                    }, 5000);
                } catch (e) {
                    demoModal.importing = false;
                    if (!pipelineStatuses[siteId]) pipelineStatuses[siteId] = {};
pipelineStatuses[siteId].demo_importing = false;
                    showToast('导入请求失败: ' + e.message, 'error');
                }
            })();
        }
        async function checkPanelStatus() {
            try { const resp = await API.panelStatus(); panelConnected.value = resp.data?.connected || false; } catch (e) { panelConnected.value = false; }
        }
        async function loadPanelData() {
            try {
                const [w, i, g] = await Promise.all([API.panelSearchWebsites(), API.panelSearchInstalled(), API.panelSearchGroups()]);
                if (w.code === 200) panelWebsites.value = w.data?.items || [];
                if (i.code === 200) panelInstalledApps.value = i.data?.items || [];
                if (g.code === 200) panelGroups.value = g.data || [];
            } catch (e) {}
        }
        async function loadConfig() {
            try { const resp = await API.getConfig(); if (resp.code === 200) { Object.assign(globalConfig, resp.data); createForm.admin_name = resp.data.default_admin_name || 'admin'; createForm.db_service = resp.data.db_service || 'mariadb'; if (resp.data.deepseek_api_key) { const dk = resp.data.deepseek_api_key; const parsed = dk.startsWith('[') ? JSON.parse(dk) : [dk]; if (Array.isArray(parsed) && parsed.length) { deepseekApiKeys.value = parsed; deepseekConnected.value = true; } } if (resp.data.crawlbase_api_key) { const ck = resp.data.crawlbase_api_key; const parsed = ck.startsWith('[') ? JSON.parse(ck) : [ck]; if (Array.isArray(parsed) && parsed.length) { crawlbaseApiKeys.value = parsed; crawlbaseConnected.value = true; } } if (resp.data.agnes_api_key) { const ak = resp.data.agnes_api_key; const parsed = ak.startsWith('[') ? JSON.parse(ak) : (ak.startsWith('\"') ? JSON.parse(ak) : [ak]); if (Array.isArray(parsed) && parsed.length) { agnesApiKey.value = parsed[0]; agnesConnected.value = true; } else if (typeof parsed === 'string' && parsed) { agnesApiKey.value = parsed; agnesConnected.value = true; } } } } catch (e) {}
        }
        async function refreshSites() {
            loading.value = true;
            try { await Promise.all([loadSites(), loadPanelData()]); showToast('数据已刷新'); } finally { loading.value = false; }
        }

        async function syncWithPanel() {
            loading.value = true;
            try {
                const resp = await API.panelSync(true);
                if (resp.code === 200) {
                    const d = resp.data;
                    let msg = `同步完成: 更新${d.updated}个, 清理${d.cleared}个`;
                    if (d.imported) msg += `, 导入${d.imported}个`;
                    if (d.untracked_websites > 0) msg += `, 发现${d.untracked_websites}个未同步的WordPress网站`;
                    if (d.orphaned_wp_apps > 0) msg += `, 发现${d.orphaned_wp_apps}个未关联网站的WordPress应用`;
                    if (d.errors && d.errors.length) msg += `, ${d.errors.length}个错误`;
                    showToast(msg);
                    await loadSites();
                } else { showToast(resp.message || '同步失败', 'error'); }
            } catch (e) { showToast('同步失败', 'error'); } finally { loading.value = false; }
        }

        // ---- Feed Products (GMC) ----
        async function loadFeedProducts() {
            if (!feedSiteId.value) { feedProducts.value = []; return; }
            try { const resp = await API.getFeedProducts(feedSiteId.value); if (resp.code === 200) feedProducts.value = resp.data || []; } catch (e) {}
        }
        function openFeedProductModal(product) {
            if (product) {
                feedEditId.value = product.id;
                Object.assign(feedEditForm, product);
            } else {
                feedEditId.value = null;
                Object.keys(feedEditForm).forEach(k => feedEditForm[k] = k === 'currency' ? 'USD' : k === 'availability' ? 'in_stock' : k === 'condition' ? 'new' : '');
            }
            showFeedProductModal.value = true;
        }
        function closeFeedProductModal() { showFeedProductModal.value = false; }
        async function handleSaveFeedProduct() {
            if (!feedEditForm.title.trim()) { showToast('商品标题不能为空', 'error'); return; }
            try {
                let resp;
                if (feedEditId.value) {
                    resp = await API.updateFeedProduct(feedEditId.value, feedEditForm);
                } else {
                    resp = await API.createFeedProduct(feedSiteId.value, feedEditForm);
                }
                if (resp.code === 200) { showToast(feedEditId.value ? '商品已更新' : '商品已添加'); closeFeedProductModal(); await loadFeedProducts(); }
                else { showToast(resp.message || '操作失败', 'error'); }
            } catch (e) { showToast('操作失败', 'error'); }
        }
        async function handleDeleteFeedProduct(product) {
            if (!confirm(`确定删除商品 "${product.title}"?`)) return;
            try { await API.deleteFeedProduct(product.id); showToast('商品已删除'); await loadFeedProducts(); } catch (e) { showToast('删除失败', 'error'); }
        }
        async function handleImportSampleProducts() {
            if (!feedSiteId.value) { showToast('请先选择一个站点', 'error'); return; }
            try {
                const resp = await API.createSampleFeedProducts(feedSiteId.value);
                if (resp.code === 200) { showToast(resp.message || '示例商品已导入'); await loadFeedProducts(); }
                else { showToast(resp.message || '导入失败', 'error'); }
            } catch (e) { showToast('导入失败', 'error'); }
        }
        async function handleExportFeed() {
            if (!feedSiteId.value) { showToast('请先选择一个站点', 'error'); return; }
            try { await API.exportFeedProducts(feedSiteId.value); showToast('Feed XML 已导出'); } catch (e) { showToast('导出失败', 'error'); }
        }

        // ---- 筛品 Dashboard & Navigation ----
        function toggleFeedMenu() {
            feedMenuOpen.value = !feedMenuOpen.value;
        }
        function setSourceTab(tab) {
            sourceTab.value = tab;
        }
        async function loadFeedStats() {
            feedStatsLoading.value = true;
            try {
                const resp = await API.getFeedStats();
                if (resp.code === 200) {
                    feedStats.value = resp.data;
                }
            } catch (e) {
                // silent fail
            } finally {
                feedStatsLoading.value = false;
            }
        }
        async function loadWooStats() {
            wooStatsLoading.value = true;
            try {
                const params = { period: wooStatsPeriod.value };
                if (wooStatsDateMin.value) params.date_min = wooStatsDateMin.value;
                if (wooStatsDateMax.value) params.date_max = wooStatsDateMax.value;
                const resp = await API.getWooCommerceStats(params);
                if (resp.code === 200) {
                    wooStats.value = resp.data;
                }
            } catch (e) {
                // silent fail
            } finally {
                wooStatsLoading.value = false;
            }
        }
        function setWooStatsPeriod(period) {
            wooStatsPeriod.value = period;
            wooStatsDateMin.value = '';
            wooStatsDateMax.value = '';
            loadWooStats();
        }
        function formatMoney(val) {
            const n = Number(val) || 0;
            return n.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
        }
        function formatInt(val) {
            const n = Number(val) || 0;
            return n.toLocaleString('en-US', {maximumFractionDigits:0});
        }

        // ---- 筛品 Walmart ----
        async function loadWalmartCategories() {
            try {
                const resp = await API.getWalmartCategories();
                if (resp && resp.code === 200) {
                    walmartCategories.value = Array.isArray(resp.data) ? resp.data : [];
                }
            } catch (e) { /* silent */ }
        }
        async function fetchWalmartBestsellers() {
            if (!walmartSelectedCategory.value) {
                walmartError.value = '请选择商品大类';
                return;
            }
            walmartLoading.value = true;
            walmartError.value = '';
            walmartProducts.value = [];
            try {
                const resp = await API.fetchWalmartBestsellers(
                    walmartSelectedCategory.value,
                    walmartFetchLimit.value
                );
                if (resp.code === 200) {
                    walmartProducts.value = resp.data.products || [];
                    walmartPage.value = 1;
                    loadWalmartCategories();  // refresh cached counts after save
                    showToast(`已获取 ${walmartProducts.value.length} 件热销商品`, 'success');
                } else {
                    walmartError.value = resp.message || '获取失败';
                    showToast(walmartError.value, 'error');
                }
            } catch (e) {
                walmartError.value = e.message || '网络错误';
                showToast(walmartError.value, 'error');
            } finally {
                walmartLoading.value = false;
            }
        }
        async function loadPersistedWalmartProducts() {
            const cat = walmartSelectedCategory.value;
            if (!cat) { walmartProducts.value = []; return; }
            // Clear immediately to avoid stale data flash during async load
            walmartProducts.value = [];
            walmartPage.value = 1;
            try {
                const resp = await API.loadWalmartProducts(cat);
                if (resp && resp.code === 200 && resp.data && Array.isArray(resp.data.products)) {
                    walmartProducts.value = resp.data.products;
                    if (walmartProducts.value.length) {
                        walmartPage.value = 1;
                    }
                }
            } catch (e) { /* silent */ }
        }
        async function exportWalmartData(format) {
            if (!walmartProducts.value.length) {
                showToast('没有可导出的数据', 'error');
                return;
            }
            try {
                await API.exportWalmartData(
                    walmartProducts.value,
                    walmartSelectedCategory.value || 'all',
                    format
                );
                showToast(format === 'json' ? 'JSON 已导出' : 'Excel 已导出', 'success');
            } catch (e) {
                showToast('导出失败: ' + (e.message || '未知错误'), 'error');
            }
        }

        function walmartGoPage(n) {
            const total = walmartTotalPages.value;
            if (n < 1) n = 1;
            if (n > total) n = total;
            walmartPage.value = n;
        }

        async function enrichWalmartProducts() {
            if (!walmartProducts.value.length) {
                showToast('没有可处理的热销数据', 'error');
                return;
            }
            const urls = walmartProducts.value.map(p => p.source_url).filter(Boolean);
            if (!urls.length) {
                showToast('商品链接无效', 'error');
                return;
            }
            walmartEnriching.value = true;
            walmartEnrichProgress.value = `处理中 0 / ${urls.length} ...`;
            try {
                const resp = await API.enrichWalmartProducts(urls, walmartSelectedCategory.value);
                if (resp.code === 200) {
                    showToast(`数据异步完成: ${resp.data.ok} 成功 / ${resp.data.fail} 失败`, resp.data.fail ? 'error' : 'success');
                    await loadGeneratedFeed();
                } else {
                    showToast(resp.message || '异步失败', 'error');
                }
            } catch (e) {
                showToast('异步失败: ' + (e.message || '网络错误'), 'error');
            } finally {
                walmartEnriching.value = false;
                walmartEnrichProgress.value = '';
            }
        }
        async function loadGeneratedFeed() {
            try {
                const resp = await API.listGeneratedFeed();
                if (resp.code === 200) generatedFeed.value = resp.data || [];
            } catch (e) { /* silent */ }
        }
        async function clearGeneratedFeed() {
            if (!confirm('确定清除所有已生成的 Feed 数据？')) return;
            try {
                const resp = await API.clearGeneratedFeed();
                if (resp.code === 200) {
                    generatedFeed.value = [];
                    showToast('Feed 数据已清除', 'success');
                }
            } catch (e) { showToast('清除失败', 'error'); }
        }

        // ---- 筛品 爆品导入 ----
        async function loadPersistedAmazonResults() {
            try {
                const resp = await API.loadAmazonSearchResults();
                if (resp.code === 200 && resp.data && resp.data.length) {
                    amazonSearchResults.value = resp.data;
                    amazonSearchProgress.value = `已加载 ${resp.data.length} 件历史产品`;
                }
            } catch (e) { /* silent */ }
        }

        async function deleteSelectedAmazonProducts() {
            if (!amazonSelectedIndices.value.size) {
                showToast('请先选择要删除的产品', 'error');
                return;
            }
            const selectedIds = Array.from(amazonSelectedIndices.value)
                .map(i => amazonSearchResults.value[i])
                .filter(p => p && p.id)
                .map(p => p.id);
            if (!selectedIds.length) {
                showToast('选中的产品没有有效的数据库ID', 'error');
                return;
            }
            if (!confirm(`确定删除选中的 ${selectedIds.length} 件产品？`)) return;
            try {
                const resp = await API.deleteAmazonSearchResults(selectedIds);
                if (resp.code === 200) {
                    // Remove from local list
                    const idSet = new Set(selectedIds);
                    amazonSearchResults.value = amazonSearchResults.value.filter(p => !idSet.has(p.id));
                    amazonSelectedIndices.value = new Set();
                    showToast(`已删除 ${resp.data.deleted} 件产品`, 'success');
                    amazonSearchProgress.value = `共 ${amazonSearchResults.value.length} 件产品`;
                } else {
                    showToast(resp.message || '删除失败', 'error');
                }
            } catch (e) {
                showToast('删除失败: ' + (e.message || '网络错误'), 'error');
            }
        }

        function openAmazonImportModal() {
            amazonImportModalText.value = '';
            showAmazonImportModal.value = true;
        }
        function closeAmazonImportModal() {
            showAmazonImportModal.value = false;
        }
        function startAmazonSearchFromModal() {
            const raw = amazonImportModalText.value.trim();
            if (!raw) { showToast('请输入产品名称', 'error'); return; }
            const names = raw.split('\n').map(n => n.trim()).filter(Boolean);
            if (!names.length) { showToast('请输入至少一个产品名称', 'error'); return; }
            closeAmazonImportModal();
            _runAmazonSearch(names);
        }
        function openAmazonUrlModal() {
            amazonUrlModalText.value = '';
            showAmazonUrlModal.value = true;
        }
        function closeAmazonUrlModal() {
            showAmazonUrlModal.value = false;
        }
        function startAmazonUrlImport() {
            const raw = amazonUrlModalText.value.trim();
            if (!raw) { showToast('请输入 Amazon 链接', 'error'); return; }
            const urls = raw.split('\n').map(u => u.trim()).filter(Boolean);
            if (!urls.length) { showToast('请输入至少一个链接', 'error'); return; }
            closeAmazonUrlModal();
            _runAmazonUrlImport(urls);
        }
        async function _runAmazonUrlImport(urls) {
            amazonSearchLoading.value = true;
            amazonSearchError.value = '';
            amazonSearchResults.value = [];
            amazonSelectedIndices.value = new Set();
            amazonSearchProgress.value = `开始导入 ${urls.length} 个链接...`;
            try {
                const token = API.token;
                const resp = await fetch('/api/shai-pin/amazon/direct-import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ urls }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ message: '导入失败' }));
                    amazonSearchError.value = err.message || `HTTP ${resp.status}`;
                    amazonSearchLoading.value = false;
                    return;
                }
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const msg = JSON.parse(line);
                            if (msg.type === 'start') {
                                amazonSearchProgress.value = `导入中 0 / ${msg.total} ...`;
                            } else if (msg.type === 'result') {
                                if (msg.products && msg.products.length) {
                                    amazonSearchResults.value.push(...msg.products);
                                }
                                amazonSearchProgress.value = `导入中 ${msg.total_so_far || amazonSearchResults.value.length} 件 / ${msg.total} 个链接`;
                            } else if (msg.type === 'done') {
                                amazonSearchProgress.value = `导入完成，共 ${msg.result_count} 件产品`;
                                if (msg.result_count === 0) {
                                    amazonSearchError.value = '所有链接均导入失败';
                                } else {
                                    showToast(`成功导入 ${msg.result_count} 件产品`, 'success');
                                }
                            }
                        } catch (e) { /* skip partial lines */ }
                    }
                }
            } catch (e) {
                amazonSearchError.value = e.message || '网络错误';
            } finally {
                amazonSearchLoading.value = false;
            }
        }
        function handleAmazonFileUpload(e) {
            const file = e.target.files && e.target.files[0];
            if (!file) return;
            const ext = (file.name || '').toLowerCase();
            if (!ext.endsWith('.txt') && !ext.endsWith('.csv')) {
                showToast('仅支持 .txt 或 .csv 文件', 'error');
                e.target.value = '';
                return;
            }
            const reader = new FileReader();
            reader.onload = function(ev) {
                const text = ev.target.result;
                let names = [];
                if (ext.endsWith('.csv')) {
                    // CSV: first column, skip header row
                    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
                    const start = lines.length && isNaN(lines[0].split(',')[0].trim()) ? 1 : 0;
                    names = lines.slice(start).map(l => l.split(',')[0].trim()).filter(Boolean);
                } else {
                    names = text.split('\n').map(l => l.trim()).filter(Boolean);
                }
                if (!names.length) { showToast('文件中未找到产品名称', 'error'); } else { _runAmazonSearch(names); }
            };
            reader.readAsText(file);
            e.target.value = '';
        }

        async function _runAmazonSearch(names) {
            amazonSearchLoading.value = true;
            amazonSearchError.value = '';
            amazonSearchResults.value = [];
            amazonSelectedIndices.value = new Set();
            amazonSearchProgress.value = `开始搜索 ${names.length} 个关键词...`;
            try {
                const token = API.token;
                const resp = await fetch('/api/shai-pin/amazon/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ product_names: names }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ message: '搜索失败' }));
                    amazonSearchError.value = err.message || `HTTP ${resp.status}`;
                    amazonSearchLoading.value = false;
                    return;
                }
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const msg = JSON.parse(line);
                            if (msg.type === 'start') {
                                amazonSearchProgress.value = `搜索中 0 / ${msg.total} ...`;
                            } else if (msg.type === 'result') {
                                if (msg.products && msg.products.length) {
                                    amazonSearchResults.value.push(...msg.products);
                                }
                                amazonSearchProgress.value = `搜索中 ${msg.total_so_far || amazonSearchResults.value.length} 件结果 / 已搜索 ${(msg.query_idx || 0) + 1} 个关键词`;
                            } else if (msg.type === 'done') {
                                amazonSearchProgress.value = `搜索完成，共 ${msg.result_count} 件产品`;
                                if (msg.result_count === 0) {
                                    amazonSearchError.value = '未找到匹配的产品，请尝试其他关键词';
                                } else {
                                    showToast(`找到 ${msg.result_count} 件产品`, 'success');
                                }
                            }
                        } catch (e) { /* skip partial lines */ }
                    }
                }
            } catch (e) {
                amazonSearchError.value = e.message || '网络错误';
            } finally {
                amazonSearchLoading.value = false;
            }
        }

        function toggleAmazonSelect(idx) {
            const s = new Set(amazonSelectedIndices.value);
            if (s.has(idx)) { s.delete(idx); } else { s.add(idx); }
            amazonSelectedIndices.value = s;
        }

        function selectAllAmazon() {
            if (amazonSelectedIndices.value.size === amazonSearchResults.value.length) {
                amazonSelectedIndices.value = new Set();
            } else {
                amazonSelectedIndices.value = new Set(amazonSearchResults.value.map((_, i) => i));
            }
        }

        function closeConvertLogModal() {
            if (!converting.value && !wooConverting.value) showConvertLogModal.value = false;
        }

        // ---- Feed 页面 ----
        function toggleFeedSelect(idx) {
            const s = new Set(feedSelectedIndices.value);
            if (s.has(idx)) { s.delete(idx); } else { s.add(idx); }
            feedSelectedIndices.value = s;
        }
        function selectAllFeed() {
            if (feedSelectedIndices.value.size === generatedFeed.value.length) {
                feedSelectedIndices.value = new Set();
            } else {
                feedSelectedIndices.value = new Set(generatedFeed.value.map((_, i) => i));
            }
        }
        async function deleteSelectedFeedItems() {
            if (!feedSelectedIndices.value.size) {
                showToast('请先选择要删除的产品', 'error');
                return;
            }
            const selectedIds = Array.from(feedSelectedIndices.value)
                .map(i => generatedFeed.value[i])
                .filter(p => p && p.id)
                .map(p => p.id);
            if (!selectedIds.length) {
                showToast('没有有效的产品', 'error');
                return;
            }
            if (!confirm(`确定删除选中的 ${selectedIds.length} 件 Feed 产品？`)) return;
            try {
                const resp = await API.deleteFeedItems(selectedIds);
                if (resp.code === 200) {
                    const idSet = new Set(selectedIds);
                    generatedFeed.value = generatedFeed.value.filter(p => !idSet.has(p.id));
                    feedSelectedIndices.value = new Set();
                    showToast(`已删除 ${resp.data.deleted} 件产品`, 'success');
                } else {
                    showToast(resp.message || '删除失败', 'error');
                }
            } catch (e) {
                showToast('删除失败: ' + (e.message || '网络错误'), 'error');
            }
        }
        function showFeedDescription(desc) {
            feedDescContent.value = desc;
            showFeedDescModal.value = true;
        }
        function buildFeedDetailText(p) {
            const parts = [];
            if (p.description) parts.push(p.description);
            if (p.features && p.features.length) parts.push('\n\n特性:\n' + p.features.map(f => '· ' + f).join('\n'));
            const ex = p.extra_data;
            if (ex && Object.keys(ex).length) {
                const info = [];
                if (ex.availability) info.push('库存: ' + ex.availability);
                if (ex.condition) info.push('成色: ' + ex.condition);
                if (ex.dimensions) info.push('尺寸: ' + ex.dimensions);
                if (ex.weight) info.push('重量: ' + ex.weight);
                if (ex.sellerName) info.push('卖家: ' + ex.sellerName + (ex.sellerUrl ? ' (' + ex.sellerUrl + ')' : ''));
                if (ex.sellerRating) info.push('卖家评分: ' + ex.sellerRating);
                if (ex.bestSellerRank) info.push('BSR: #' + ex.bestSellerRank);
                if (ex.estimatedSales) info.push('预估月销: ' + ex.estimatedSales);
                if (ex.answeredQuestions > 0) info.push('已答问题: ' + ex.answeredQuestions);
                if (ex.deliveryInfo) info.push('配送: ' + ex.deliveryInfo);
                if (ex.originalPrice && ex.originalPrice !== p.price) info.push('原价: ' + ex.originalPrice);
                if (ex.discount) info.push('折扣: ' + ex.discount);
                if (ex.isPrime) info.push('Prime: 是');
                if (ex.variants && ex.variants.length) info.push('变体数: ' + ex.variants.length);
                if (ex.specifications && Object.keys(ex.specifications).length) {
                    info.push('规格参数:');
                    Object.entries(ex.specifications).forEach(([k, v]) => info.push('  ' + k + ': ' + v));
                }
                if (info.length) parts.push('\n\n附加信息:\n' + info.join('\n'));
            }
            return parts.join('');
        }

        // ---- 网站产品 Products ----
        async function convertToWooCommerce() {
            if (!amazonSelectedIndices.value.size) {
                showToast('请先选择要转换的产品', 'error');
                return;
            }
            const selected = Array.from(amazonSelectedIndices.value)
                .map(i => amazonSearchResults.value[i])
                .filter(Boolean);
            if (!selected.length) {
                showToast('没有有效的产品数据', 'error');
                return;
            }
            if (wooConverting.value) return;
            wooConverting.value = true;
            convertLogLines.value = [];
            convertLogProgress.value = '';
            showConvertLogModal.value = true;
            try {
                const token = API.token;
                const resp = await fetch('/api/shai-pin/woocommerce/convert', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ products: selected }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ message: '转换失败' }));
                    convertLogProgress.value = '请求失败: ' + (err.message || `HTTP ${resp.status}`);
                    return;
                }
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const msg = JSON.parse(line);
                            if (msg.type === 'start') {
                                convertLogProgress.value = `开始转换 ${msg.total} 件产品...`;
                            } else if (msg.type === 'log') {
                                convertLogLines.value.push(msg.entry);
                                convertLogProgress.value = `转换中 ${msg.completed}/${msg.total} — ${msg.ok} 成功 / ${msg.fail} 失败`;
                            } else if (msg.type === 'done') {
                                convertLogProgress.value = `转换完成！${msg.ok} 成功 / ${msg.fail} 失败`;
                                if (msg.ok > 0) {
                                    await loadWooProducts();
                                    showToast(`转换完成: ${msg.ok} 件 网站产品 产品`, 'success');
                                    amazonSelectedIndices.value = new Set();
                                } else {
                                    showToast('转换失败，所有产品均未能获取', 'error');
                                }
                            }
                        } catch (e) { /* skip partial */ }
                    }
                }
            } catch (e) {
                convertLogProgress.value = '请求失败: ' + (e.message || '网络错误');
            } finally {
                wooConverting.value = false;
            }
        }
        async function loadWooProducts() {
            try {
                const resp = await API.getWooCommerceProducts();
                if (resp.code === 200) wooProducts.value = resp.data || [];
            } catch (e) { /* ignore */ }
        }
        function toggleWooSelect(idx) {
            const s = new Set(wooSelectedIndices.value);
            s.has(idx) ? s.delete(idx) : s.add(idx);
            wooSelectedIndices.value = s;
        }
        function selectAllWoo() {
            if (wooSelectedIndices.value.size === wooProducts.value.length) {
                wooSelectedIndices.value = new Set();
            } else {
                wooSelectedIndices.value = new Set(wooProducts.value.map((_, i) => i));
            }
        }
        async function deleteSelectedWooProducts() {
            if (!wooSelectedIndices.value.size) {
                showToast('请先选择要删除的产品', 'error');
                return;
            }
            const selectedIds = Array.from(wooSelectedIndices.value)
                .map(i => wooProducts.value[i])
                .filter(p => p && p.id)
                .map(p => p.id);
            if (!selectedIds.length) {
                showToast('没有有效的产品', 'error');
                return;
            }
            if (!confirm(`确定删除选中的 ${selectedIds.length} 件 网站产品 产品？`)) return;
            try {
                const resp = await API.deleteWooCommerceProducts(selectedIds);
                if (resp.code === 200) {
                    const idSet = new Set(selectedIds);
                    wooProducts.value = wooProducts.value.filter(p => !idSet.has(p.id));
                    wooSelectedIndices.value = new Set();
                    showToast(`已删除 ${resp.data.deleted} 件产品`, 'success');
                } else {
                    showToast(resp.message || '删除失败', 'error');
                }
            } catch (e) {
                showToast('删除失败: ' + (e.message || '网络错误'), 'error');
            }
        }

        // ---- Site Sync (Feed + 网站产品) ----
        const feedUrl = ref({});  // keyed by site_id
        async function createFeedForSite() {
            if (!feedSyncSiteId.value) { showToast('请先选择目标站点', 'error'); return; }
            if (syncingFeed.value) return;
            syncingFeed.value = true;
            try {
                const resp = await API.syncFeedToSite(feedSyncSiteId.value);
                if (resp.code === 200) {
                    feedUrl.value[feedSyncSiteId.value] = resp.data.feed_url || '';
                    // 更新 GMC 自动化页面的 Feed URL 显示
                    mcFeedUrls.value[feedSyncSiteId.value] = resp.data.feed_url || '';
                    // 更新站点列表时间线的 GMC 状态
                    if (pipelineStatuses[feedSyncSiteId.value]) {
                        pipelineStatuses[feedSyncSiteId.value].gmc_registered = true;
                    }
                    showToast(`Feed 创建成功！${resp.data.products} 件产品，文件大小 ${(resp.data.size_bytes / 1024).toFixed(1)} KB`, 'success');
                } else {
                    showToast(resp.message || '创建失败', 'error');
                }
            } catch (e) {
                showToast('创建失败: ' + (e.message || '网络错误'), 'error');
            }
            syncingFeed.value = false;
        }
        async function cleanFeedFromSite() {
            if (!feedSyncSiteId.value) { showToast('请先选择目标站点', 'error'); return; }
            if (!confirm('确定从该站点清理 Feed 文件？')) return;
            syncingFeed.value = true;
            try {
                const resp = await API.cleanFeedFromSite(feedSyncSiteId.value);
                if (resp.code === 200) {
                    delete feedUrl.value[feedSyncSiteId.value];
                    showToast('Feed 文件已清理', 'success');
                } else {
                    showToast(resp.message || '清理失败', 'error');
                }
            } catch (e) {
                showToast('清理失败: ' + (e.message || '网络错误'), 'error');
            }
            syncingFeed.value = false;
        }
        async function syncWooToSite() {
            if (!wooSyncSiteId.value) { showToast('请先选择目标站点', 'error'); return; }
            if (syncingWoo.value) return;
            syncingWoo.value = true;
            try {
                const resp = await API.syncWooToSite(wooSyncSiteId.value);
                if (resp.code === 200) {
                    showToast(`网站产品 同步完成！${resp.data.ok} 成功 / ${resp.data.fail} 失败`, resp.data.fail ? 'error' : 'success');
                } else {
                    showToast(resp.message || '同步失败', 'error');
                }
            } catch (e) {
                showToast('同步失败: ' + (e.message || '网络错误'), 'error');
            }
            syncingWoo.value = false;
        }
        async function cleanWooFromSite() {
            if (!wooSyncSiteId.value) { showToast('请先选择目标站点', 'error'); return; }
            if (!confirm('确定从该站点删除所有 网站产品 产品？此操作不可撤销。')) return;
            syncingWoo.value = true;
            try {
                const resp = await API.cleanWooFromSite(wooSyncSiteId.value);
                if (resp.code === 200) {
                    showToast(`已删除 ${resp.data.deleted} 件产品${resp.data.failed ? '，' + resp.data.failed + ' 件失败' : ''}`, 'success');
                } else {
                    showToast(resp.message || '清理失败', 'error');
                }
            } catch (e) {
                showToast('清理失败: ' + (e.message || '网络错误'), 'error');
            }
            syncingWoo.value = false;
        }

        async function generateFeedFromWoo() {
            if (!wooProducts.value.length) { showToast('没有可生成的产品', 'error'); return; }
            wooGeneratingFeed.value = true;
            converting.value = true;
            convertLogLines.value = [];
            convertLogProgress.value = '';
            showConvertLogModal.value = true;
            try {
                const token = API.token;
                const resp = await fetch('/api/shai-pin/woocommerce/generate-feed', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ message: '生成失败' }));
                    convertLogProgress.value = '请求失败: ' + (err.message || `HTTP ${resp.status}`);
                    return;
                }
                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const msg = JSON.parse(line);
                            if (msg.type === 'start') {
                                convertLogProgress.value = `开始生成 ${msg.total} 件产品 Feed...`;
                            } else if (msg.type === 'log') {
                                convertLogLines.value.push({ idx: msg.idx, title: msg.title, ok: msg.item_ok, error: msg.error });
                                convertLogProgress.value = `生成中 ${msg.completed}/${msg.total} — ${msg.ok} 成功 / ${msg.fail} 失败`;
                            } else if (msg.type === 'done') {
                                convertLogProgress.value = `生成完成！${msg.ok} 成功 / ${msg.fail} 失败`;
                                if (msg.ok > 0) {
                                    await loadGeneratedFeed();
                                    showToast(`已生成 ${msg.ok} 件 Feed`, 'success');
                                } else {
                                    showToast('生成失败，未能生成任何 Feed', 'error');
                                }
                            }
                        } catch (e) { /* skip partial */ }
                    }
                }
            } catch (e) {
                convertLogProgress.value = '请求失败: ' + (e.message || '网络错误');
            } finally {
                converting.value = false;
                wooGeneratingFeed.value = false;
            }
        }

        // ---- Cloudflare ----
        async function loadCfAccounts() {
            try { const resp = await API.cfListAccounts(); if (resp.code === 200) { cfAccounts.value = resp.data || []; cfConnected.value = cfAccounts.value.length > 0; } } catch (e) {}
        }
        async function checkCfStatus() {
            const accountId = cfSelectedAccountId.value;
            try { const resp = await API.cfStatus(accountId || undefined); cfConnected.value = resp.data?.connected || false; } catch (e) { cfConnected.value = false; }
        }
        async function cfVerify() {
            loading.value = true;
            try {
                if (!cfToken.value.trim()) { showToast('请输入Cloudflare API Token', 'error'); loading.value = false; return; }
                const resp = await API.cfVerifyToken(cfToken.value.trim());
                if (resp.code === 200) { cfConnected.value = true; showToast('Cloudflare授权成功'); await loadCfAccounts(); }
                else { showToast(resp.message || '验证失败', 'error'); }
            } catch (e) { showToast('验证失败', 'error'); } finally { loading.value = false; }
        }
        async function deepseekVerify() {
            loading.value = true;
            // Clear previous per-key errors
            Object.keys(deepseekKeyErrors).forEach(k => delete deepseekKeyErrors[k]);
            try {
                const keys = deepseekApiKeys.value.map(k => k.trim()).filter(Boolean);
                if (!keys.length) { showToast('请输入至少一个 DeepSeek API Key', 'error'); loading.value = false; return; }
                const resp = await API.deepseekVerify(JSON.stringify(keys));
                if (resp.code === 200) {
                    deepseekConnected.value = true;
                    showToast(resp.message || 'DeepSeek 全部验证通过');
                } else {
                    const results = resp.data?.results || [];
                    results.forEach(r => {
                        if (!r.ok) deepseekKeyErrors[r.index] = r.error;
                    });
                    const fails = results.filter(r => !r.ok);
                    const detail = fails.map(r => `#${r.index + 1}: ${r.error}`).join('；');
                    showToast((resp.message || '验证失败') + (detail ? ' — ' + detail : ''), 'error');
                }
            } catch (e) { showToast('网络错误: ' + (e.message || '未知'), 'error'); } finally { loading.value = false; }
        }
        async function agnesVerify() {
            agnesVerifying.value = true;
            try {
                const key = agnesApiKey.value.trim();
                if (!key) { showToast('请输入Agnes API Key', 'error'); return; }
                const resp = await API.verifyAgnesKey(key);
                if (resp.code === 200) {
                    agnesConnected.value = true;
                    showToast('Agnes AI 验证通过');
                } else {
                    showToast(resp.message || '验证失败', 'error');
                }
            } catch (e) { showToast('网络错误', 'error'); } finally { agnesVerifying.value = false; }
        }
        async function crawlbaseVerify() {
            loading.value = true;
            Object.keys(crawlbaseKeyErrors).forEach(k => delete crawlbaseKeyErrors[k]);
            try {
                const keys = crawlbaseApiKeys.value.map(k => k.trim()).filter(Boolean);
                if (!keys.length) { showToast('请输入至少一个 Crawlbase API Token', 'error'); loading.value = false; return; }
                const resp = await API.crawlbaseVerify(JSON.stringify(keys));
                if (resp.code === 200) {
                    crawlbaseConnected.value = true;
                    showToast(resp.message || 'Crawlbase 全部验证通过');
                } else {
                    const results = resp.data?.results || [];
                    results.forEach(r => {
                        if (!r.ok) crawlbaseKeyErrors[r.index] = r.error;
                    });
                    const fails = results.filter(r => !r.ok);
                    const detail = fails.map(r => `#${r.index + 1}: ${r.error}`).join('；');
                    showToast((resp.message || '验证失败') + (detail ? ' — ' + detail : ''), 'error');
                }
            } catch (e) { showToast('网络错误: ' + (e.message || '未知'), 'error'); } finally { loading.value = false; }
        }
        async function loadCloakbrowserProfiles() {
            const resp = await API.listCloakbrowserProfiles();
            if (resp.code === 200) { cloakbrowserProfiles.value = resp.data || []; }
        }
        async function handleDeleteCfAccount(id) {
            if (!confirm('确定删除此Cloudflare账号？')) return;
            const resp = await API.cfDeleteAccount(id);
            if (resp.code === 200) { showToast('账号已删除'); await loadCfAccounts(); } else { showToast(resp.message || '删除失败', 'error'); }
        }
        async function handleSetDefaultCfAccount(id) {
            const resp = await API.cfSetDefaultAccount(id);
            if (resp.code === 200) { showToast('已设为默认账号'); await loadCfAccounts(); } else { showToast(resp.message || '设置失败', 'error'); }
        }

        // ---- WordPress.com Functions ----
        // ---- 3-Step Wizard ----
        async function openWizard(mode = 'single') {
            wizardMode.value = mode; wizardStep.value = 1; wizardSiteId.value = null;
            createForm.site_name = ''; createForm.url = ''; createForm.admin_name = globalConfig.default_admin_name || 'admin';
            createForm.admin_password = globalConfig.default_admin_password || ''; createForm.tag = ''; createForm.security_id = '';
            createForm.http_username = ''; createForm.http_password = ''; createForm.verify_certificate = true; createForm.ssl_version = 'auto';
            createForm.domains = ''; createForm.base_port = 8081;
            // Fetch next available port
            try { const r = await API.getNextPort(); if (r.code === 200 && r.data) createForm.base_port = r.data.next_port; } catch (e) {}
            createProgress.show = false; createProgress.results = [];
            wizardBrandKitId.value = null;
            profileTestState.value = { testing: false, result: null, message: '' };
            await loadCfAccounts();
            await loadBrandKitsForWizard();
            // Auto-select first CF account if available (for 1Panel + DNS auto-config)
            if (cfAccounts.value.length > 0) {
                cfSelectedAccountId.value = cfAccounts.value[0].id;
            } else {
                cfSelectedAccountId.value = '';
            }
            wizardOpen.value = true;
        }
        function closeWizard() { wizardOpen.value = false; loadSites(); loadPanelData(); }
        function onWizardGroupChange() {
            const g = panelGroups.value.find(g => g.id === createForm.website_group_id);
            if (g && g.name) createForm.tag = g.name;
        }

        async function testProfileForWizard() {
            const kit = brandKitsForWizard.value.find(k => k.id === wizardBrandKitId.value);
            const profileName = kit?.cloakbrowser_profile_name;
            if (!profileName) { showToast('该套件暂未关联指纹环境', 'error'); return; }
            profileTestState.value = { testing: true, result: null, message: '正在测试...' };
            try {
                const resp = await API.testCloakbrowserProfile(profileName);
                if (resp.code === 200) {
                    profileTestState.value = { testing: false, result: true, message: resp.message || 'Profile 可用' };
                    showToast('指纹环境测试通过');
                } else {
                    profileTestState.value = { testing: false, result: false, message: resp.message || '测试失败' };
                    showToast(resp.message || '指纹环境测试失败', 'error');
                }
            } catch (e) {
                profileTestState.value = { testing: false, result: false, message: '测试请求失败' };
                showToast('测试请求失败', 'error');
            }
        }
        // Pipeline status polling when on sites page
        let pipelinePollTimer = null;
        watch(currentPage, (page) => {
            if (page === 'woocommerce-products') {
                loadWooProducts();
            }
            if (page === 'sites') {
                pipelinePollTimer = setInterval(() => {
                    sites.value.forEach(s => {
                        const ps = pipelineStatuses[s.id];
                        if (!ps) return;
                        // Static sites: poll while deploying, stop when complete
                        if (ps.site_type === 'static') {
                            if (!ps.files_uploaded) {
                                loadPipelineStatus(s.id);
                            }
                            return;
                        }
                        // WordPress sites (legacy)
                        if (!ps.wp_deployed) return;
                        if (!ps.brand_configured || !ps.gmc_registered || (!ps.demo_imported && !ps.demo_importing)) {
                            loadPipelineStatus(s.id);
                        }
                    });
                }, 3000);
            } else {
                if (pipelinePollTimer) { clearInterval(pipelinePollTimer); pipelinePollTimer = null; }
            }
        });

        // Watch brand kit selection → auto-test environment
        watch(wizardBrandKitId, async (newVal) => {
            profileTestState.value = { testing: false, result: null, message: '' };
            if (!newVal) return;
            const kit = brandKitsForWizard.value.find(k => k.id === newVal);
            if (!kit || !kit.cloakbrowser_profile_name) {
                profileTestState.value.result = true;
                return;
            }
            await testProfileForWizard();
        });

        // Step 4: Create site(s)
        async function wizardCreateSite() {
            const isBatch = wizardMode.value === 'batch';
            let domains = [];
            if (isBatch) {
                domains = createForm.domains.split('\n').map(d => d.trim()).filter(d => d).map(d => ({ domain: d }));
                if (!domains.length) { showToast('请至少输入一个域名', 'error'); return; }
            } else {
                const domain = createForm.site_name.trim();
                if (!domain) { showToast('请输入域名', 'error'); return; }
                domains = [{ domain }];
            }

            // Submit to backend (creates sites in DB + starts bg deploy threads)
            loading.value = true;
            try {
                const resp = await API.batchCreateStaticSite({
                    domains: domains,
                    brand_kit_id: wizardBrandKitId.value || null,
                    cf_account_id: cfSelectedAccountId.value || null,
                    admin_name: createForm.admin_name || 'admin',
                    admin_password: createForm.admin_password || '',
                    tag: "静态独立站",
                });
                if (resp.code !== 200) {
                    showToast(`创建失败: ${resp.message}`, 'error');
                    return;
                }
                const results = resp.data.results || [];

                // Refresh site list — new sites visible immediately
                await loadSites();

                // Close wizard
                wizardOpen.value = false;
                showToast(`${results.length} 个站点已创建，后台部署中...`);
            } catch (e) {
                showToast(`创建失败: ${e.message}`, 'error');
            } finally {
                loading.value = false;
            }
        }

        function closeDeployOverlay() {
            deployOverlay.show = false;
            deployOverlay.results = [];
            deployOverlay.domains = [];
            deployOverlay.done = false;
            deployOverlay.step = 'progress';
            postInstallSite.value = null;
        }
        // Auto-continue after deploy: apply brand kit if selected, then CF SSL, then finish
        async function autoContinue() {
            // Deploy done — close overlay, refresh sites, start silent install in background
            await loadSites();
            const installed = deployOverlay.domains.filter(d => d.status === 'installed');
            if (installed.length && wizardBrandKitId.value) {
                brandConfigSelectedKitId.value = wizardBrandKitId.value;
                const kit = brandKitsForWizard.value.find(k => k.id === wizardBrandKitId.value);
                if (kit && kit.brand_name) {
                    brandConfigBrandName.value = kit.brand_name;
                }
                // Auto-start silent brand config + SSL for each installed site
                for (const d of installed) {
                    const site = sites.value.find(s => s.site_name === d.domain || s.url?.includes(d.domain));
                    if (site) {
                        startSilentInstall(site.id);
                    }
                }
            }
            deployOverlay.show = false;
        }
        // === Demo Import Functions ===
        async function loadDemosForSite() {
            if (!postInstallSite.value) return;
            demosLoading.value = true;
            try {
                const resp = await API.getPrebuiltDemos(postInstallSite.value.site_id);
                if (resp.code === 200 && resp.data) {
                    demos.value = resp.data || [];
                    demoPluginReady.value = demos.value.length > 0;
                    if (demos.value.length > 0 && !selectedDemoId.value) {
                        selectedDemoId.value = demos.value[0].id;
                    }
                }
            } catch (e) {
                demoImportStatus.value = '加载失败: ' + e.message;
            }
            demosLoading.value = false;
        }
        async function startDemoImport() {
            if (!postInstallSite.value || demoImporting.value) return;
            demoImporting.value = true;
            demoImportStatus.value = '';
            try {
                const resp = await API.importPrebuiltDemo(postInstallSite.value.site_id, selectedDemoId.value);
                if (resp.code === 200) {
                    // Poll for completion
                    for (let i = 0; i < 60; i++) {
                        await new Promise(r => setTimeout(r, 5000));
                        const sresp = await API.getPrebuiltDemoStatus(postInstallSite.value.site_id, selectedDemoId.value);
                        if (sresp.code === 200 && sresp.data) {
                            if (sresp.data.status === 'success') {
                                demoImportStatus.value = 'completed';
                                showToast('演示导入完成');
                                break;
                            }
                            if (sresp.data.status === 'failed') {
                                demoImportStatus.value = sresp.data.message || '导入失败';
                                showToast('演示导入失败: ' + demoImportStatus.value, 'error');
                                break;
                            }
                        }
                    }
                    if (demoImportStatus.value !== 'completed' && !demoImportStatus.value) {
                        demoImportStatus.value = 'completed'; // Assume done after timeout
                        showToast('演示导入可能已完成（超时）');
                    }
                } else {
                    demoImportStatus.value = resp.message || '导入失败';
                    showToast('演示导入失败: ' + (resp.message || '未知错误'), 'error');
                }
            } catch (e) {
                demoImportStatus.value = e.message;
                showToast('演示导入错误: ' + e.message, 'error');
            }
            demoImporting.value = false;
        }
        function finishDemoImport() {
            // Close overlay, start silent install
            const siteId = postInstallSite.value?.site_id;
            const kitId = wizardBrandKitId.value;
            const brandName = brandConfigBrandName.value;
            const kitList = brandKitsForWizard.value;
            closeDeployOverlay();
            if (siteId) {
                // Restore wizard context for silent install
                wizardBrandKitId.value = kitId;
                brandConfigBrandName.value = brandName;
                brandKitsForWizard.value = kitList;
                startSilentInstall(siteId);
            }
        }
        // === Silent Install (AI config + SSL in background, no UI) ===
        function startSilentInstall(siteId) {
            if (!siteId) return;
            if (!pipelineStatuses[siteId]) pipelineStatuses[siteId] = {};
            (async () => {
                // Step 1: AI Brand Config (if brand kit was selected)
                if ((brandConfigSelectedKitId.value || wizardBrandKitId.value) && brandConfigBrandName.value.trim()) {
                    pipelineStatuses[siteId].silent_step = 'ai-config';
                    try {
                        const resp = await API.brandConfig(siteId, {
                            brand_kit_id: brandConfigSelectedKitId.value || wizardBrandKitId.value,
                            brand_name: brandConfigBrandName.value.trim(),
                        });
                        if (resp.code === 200) {
                            const key = resp.data.config_key;
                            for (let i = 0; i < 120; i++) {
                                await new Promise(r => setTimeout(r, 3000));
                                const sresp = await API.getBrandConfigStatus(siteId, key);
                                if (sresp.code === 200 && sresp.data) {
                                    if (sresp.data.status === 'success') break;
                                    else if (sresp.data.status === 'failed') break;
                                }
                            }
                        }
                    } catch (e) {}
                    refreshPipelineStatus(siteId);
                }
                // Step 2: CF SSL (last — HTTPS redirect invalidates HTTP sessions)
                pipelineStatuses[siteId].silent_step = 'cf-ssl';
                try {
                    await API.installCfSsl(siteId);
                } catch (e) {}
                pipelineStatuses[siteId].silent_step = null;
                refreshPipelineStatus(siteId);
            })();
        }
        // Brand kit for wizard step 1
        async function loadBrandKitsForWizard() {
            try {
                const resp = await API.getBrandKits();
                if (resp.code === 200) {
                    const all = resp.data || [];
                    brandKitsForWizard.value = all.filter(k => k.status === 'ready');
                }
            } catch (e) {}
        }
        // Brand kit for deploy overlay selection
        async function loadBrandKitsForSelect() {
            try {
                const resp = await API.getBrandKits();
                if (resp.code === 200) {
                    const all = resp.data || [];
                    brandKitsForSelect.value = all.filter(k => k.status === 'ready');
                }
            } catch (e) {}
        }
        function onBrandConfigKitChange() {
            const kit = brandKitsForSelect.value.find(k => k.id === brandConfigSelectedKitId.value);
            if (kit && kit.brand_name) {
                brandConfigBrandName.value = kit.brand_name;
            }
        }
        // Unified brand config (AI + 网站产品 + Logo + Footer)
        async function startBrandConfig() {
            if (!postInstallSite.value || !brandConfigBrandName.value.trim()) return;
            brandConfigRunning.value = true;
            brandConfigError.value = '';
            brandConfigSteps.value.forEach(s => { s.status = 'pending'; s.message = ''; });

            try {
                const resp = await API.brandConfig(postInstallSite.value.site_id, {
                    brand_kit_id: brandConfigSelectedKitId.value,
                    brand_name: brandConfigBrandName.value.trim(),
                });
                if (resp.code !== 200) {
                    brandConfigError.value = resp.message || '启动失败';
                    brandConfigRunning.value = false;
                    return;
                }
                brandConfigKey.value = resp.data.config_key;

                let attempts = 0;
                while (attempts < 120) {
                    await new Promise(r => setTimeout(r, 3000));
                    attempts++;
                    try {
                        const sresp = await API.getBrandConfigStatus(postInstallSite.value.site_id, brandConfigKey.value);
                        if (sresp.code === 200 && sresp.data) {
                            const st = sresp.data;
                            if (st.steps && Array.isArray(st.steps)) {
                                st.steps.forEach((s, i) => {
                                    if (brandConfigSteps.value[i]) {
                                        brandConfigSteps.value[i].status = s.status;
                                        brandConfigSteps.value[i].message = s.message || '';
                                    }
                                });
                            }
                            if (st.status === 'success') {
                                showToast('品牌配置完成');
                                brandConfigRunning.value = false;
                                installCfSslPlugin();
                                return;
                            } else if (st.status === 'failed') {
                                brandConfigError.value = st.message || '部分步骤失败，请查看详情';
                                brandConfigRunning.value = false;
                                return;
                            }
                        }
                    } catch (e) { /* retry */ }
                }
                brandConfigError.value = '品牌配置超时';
                brandConfigRunning.value = false;
            } catch (e) {
                brandConfigError.value = e.message || '网络错误';
                brandConfigRunning.value = false;
            }
        }
        function skipBrandConfig() {
            installCfSslPlugin();
        }
        async function installCfSslPlugin() {
            if (!postInstallSite.value) { finishPostInstall(); return; }
            deployOverlay.step = 'cf-ssl';
            brandKitApplyStatus.value = {
                running: true, message: '正在安装Cloudflare SSL插件...',
                steps: [{ label: '安装并激活 Flexible SSL for CloudFlare', status: 'running' }],
            };
            try {
                const resp = await API.installCfSsl(postInstallSite.value.site_id);
                if (resp.code === 200) {
                    brandKitApplyStatus.value.steps[0].status = 'done';
                    brandKitApplyStatus.value.message = 'Cloudflare SSL 插件已激活';
                } else {
                    brandKitApplyStatus.value.steps[0].status = 'failed';
                    brandKitApplyStatus.value.message = resp.message || 'CF SSL 安装失败';
                }
            } catch (e) {
                brandKitApplyStatus.value.steps[0].status = 'failed';
                brandKitApplyStatus.value.message = 'CF SSL 安装异常';
            }
            brandKitApplyStatus.value.running = false;
            // Auto-finish
            await new Promise(r => setTimeout(r, 2000));
            finishPostInstall();
        }
        async function finishPostInstall() {
            showToast('站点配置完成', 'success');
            closeDeployOverlay();
        }

        // ---- WP Polling ----
        function startWPPolling(siteId, domain) {
            if (wpPollingTimers[siteId]) return;
            wpInstallStatuses[siteId] = { status: 'installing', message: '1Panel正在创建数据库...', domain };
            let stopped = false;
            const timer = setInterval(async () => {
                if (stopped) return;
                try {
                    const resp = await API.getWPInstallStatus(siteId);
                    if (stopped) return;
                    if (resp.code === 200 && resp.data) {
                        wpInstallStatuses[siteId] = { ...resp.data, domain };
                        if (resp.data.status === 'installed') {
                            stopped = true;
                            clearInterval(timer);
                            delete wpPollingTimers[siteId];
                            await loadSites();
                            refreshPipelineStatus(siteId);
                        } else if (resp.data.status === 'failed') {
                            stopped = true;
                            clearInterval(timer);
                            delete wpPollingTimers[siteId];
                            await loadSites();
                        }
                    }
                } catch (e) {}
            }, 5000);
            wpPollingTimers[siteId] = timer;
        }
        function stopWPPolling(siteId) { if (wpPollingTimers[siteId]) { clearInterval(wpPollingTimers[siteId]); delete wpPollingTimers[siteId]; } }

        // ---- Edit ----
        function openEditModal(site) {
            editingSiteId.value = site.id;
            Object.assign(editForm, { site_name: site.site_name, url: site.url, admin_name: site.admin_name, admin_password: site.admin_password, tag: site.tag, security_id: site.security_id, http_username: site.http_username, http_password: site.http_password, verify_certificate: !!site.verify_certificate, ssl_version: site.ssl_version || 'auto' });
            showEditModal.value = true;
        }
        async function submitEdit() {
            loading.value = true;
            try { await API.updateSite(editingSiteId.value, editForm); showEditModal.value = false; showToast('站点已更新'); await loadSites(); } catch (e) { showToast('更新失败', 'error'); } finally { loading.value = false; }
        }

        // ---- Delete ----
        function confirmDelete(site) {
            modal.loading = false; modal.progress = '';
            showModal('删除站点', `确定要删除 "${site.site_name}" 吗？${site.panel_website_id ? '将同时从1Panel删除关联的网站、应用和数据库。' : ''}此操作不可撤销。`,
                async () => {
                    modal.loading = true;
                    try {
                        modal.progress = '正在删除站点及相关资源...';
                        await API.deleteSite(site.id);
                        modal.show = false;
                        showToast('站点已删除');
                        await loadSites(); await loadPanelData();
                    } catch (e) { showToast('删除失败', 'error'); modal.loading = false; modal.progress = ''; }
                }
            );
        }

        // ---- Fix 1Panel Website ----
        async function fixSiteWebsite(site) {
            loading.value = true;
            try {
                const resp = await API.fixWebsite(site.id);
                if (resp.code === 200) {
                    showToast(resp.message || '1Panel网站已修复');
                    await loadSites();
                } else {
                    showToast(resp.message || '修复失败', 'error');
                }
            } catch (e) { showToast('修复失败', 'error'); } finally { loading.value = false; }
        }

        async function openSiteBrowser(site) {
            if (!site.cloakbrowser_profile_name) {
                showToast('该站点未绑定指纹环境', 'error');
                return;
            }
            try {
                const resp = await API.openSiteBrowser(site.id);
                if (resp.code === 200) {
                    showToast(resp.message || '浏览器已启动');
                    if (resp.data && resp.data.vnc_url) {
                        window.open(resp.data.vnc_url, '_blank');
                    }
                } else {
                    showToast(resp.message || '启动失败', 'error');
                }
            } catch (e) {
                showToast('启动浏览器失败: ' + e.message, 'error');
            }
        }
        function exportCSV() { API.exportCSV(); showToast('CSV文件已导出'); }
        // Meta tag injection
        function openMetaModal(site) {
            metaModal.site = site;
            metaModal.siteId = site.id;
            metaModal.metaTag = '';
            metaModal.submitting = false;
            metaModal.show = true;
        }
        async function submitMetaTag() {
            if (!metaModal.metaTag.trim()) return;
            metaModal.submitting = true;
            try {
                const resp = await API.request('POST', `/api/sites/${metaModal.siteId}/inject-meta`, {
                    meta_tag: metaModal.metaTag.trim(),
                });
                if (resp.code === 200) {
                    showToast(resp.message || 'Meta标签已注入');
                    metaModal.show = false;
                } else {
                    showToast(resp.message || '注入失败', 'error');
                }
            } catch (e) { showToast('注入失败', 'error'); }
            metaModal.submitting = false;
        }
        async function saveGlobalConfig() {
            loading.value = true;
            try {
                const dsKeys = deepseekApiKeys.value.map(k => k.trim()).filter(Boolean);
                const cbKeys = crawlbaseApiKeys.value.map(k => k.trim()).filter(Boolean);
                const patch = {};
                if (dsKeys.length) patch.deepseek_api_key = JSON.stringify(dsKeys);
                if (cbKeys.length) patch.crawlbase_api_key = JSON.stringify(cbKeys);
                await API.saveConfig(Object.assign({}, globalConfig, patch));
                showToast('配置已保存');
            } catch (e) { showToast('保存配置失败', 'error'); } finally { loading.value = false; }
        }

        // ---- Google Merchant Center (任务 + 日志窗口) ----
        function _startLogPolling(taskId, siteId) {
            if (taskLogPollTimer.value) clearInterval(taskLogPollTimer.value);
            taskLogAfter.value = 0;
            taskLogLines.value = [];
            taskLogStatus.value = 'running';
            taskLogResult.value = null;
            taskLogVisible.value = true;

            const poll = async () => {
                try {
                    const resp = await API.getTaskLogs(taskId, taskLogAfter.value);
                    if (resp.code !== 200) return;
                    const data = resp.data;
                    const logs = data.logs || [];
                    if (logs.length > 0) {
                        // Deduplicate by log index in case of overlapping polls
                        const existing = new Set(taskLogLines.value.map(l => l.i));
                        const newLogs = logs.filter(l => !existing.has(l.i));
                        if (newLogs.length > 0) {
                            taskLogLines.value.push(...newLogs);
                            taskLogAfter.value = Math.max(taskLogAfter.value, ...logs.map(l => l.i)) + 1;
                        }
                        nextTick(() => {
                            const el = taskLogRef.value;
                            if (el) el.scrollTop = el.scrollHeight;
                        });
                    }
                    if (data.status !== 'running') {
                        taskLogStatus.value = data.status;
                        taskLogResult.value = data.result;
                        clearInterval(taskLogPollTimer.value);
                        taskLogPollTimer.value = null;
                        delete mcRegistering.value[siteId];
                    }
                } catch (e) { /* keep polling */ }
            };

            taskLogPollTimer.value = setInterval(poll, 500);
            poll();
        }

        async function registerMCForSite(site) {
            let profileDir = mcProfileDir.value;
            if (fingerprintEnabled.value && !profileDir && site.cloakbrowser_profile_name) {
                profileDir = site.cloakbrowser_profile_name;
            }
            if (!profileDir) { showToast('请选择 CloakBrowser Profile 目录', 'error'); return; }
            if (mcRegistering.value[site.id]) return;

            taskLogTitle.value = `注册 MC — ${site.site_name}`;
            try {
                const resp = await API.taskRegisterMC(site.id, profileDir, '');
                if (resp.code === 200 && resp.data?.task_id) {
                    mcRegistering.value[site.id] = 'register';
                    _startLogPolling(resp.data.task_id, site.id);
                    _watchTaskCompletion(resp.data.task_id, (success, result) => {
                        if (success && result) { site.google_mc_account_id = result.mc_account_id || '已注册'; }
                    });
                } else {
                    taskLogLines.value = [{ i: 0, t: new Date().toLocaleTimeString(), level: 'error', msg: resp.message || '启动任务失败', step: '' }];
                    taskLogStatus.value = 'failed';
                    taskLogVisible.value = true;
                    delete mcRegistering.value[site.id];
                }
            } catch (e) {
                taskLogLines.value = [{ i: 0, t: new Date().toLocaleTimeString(), level: 'error', msg: `启动任务失败: ${e.message || e}`, step: '' }];
                taskLogStatus.value = 'failed';
                taskLogVisible.value = true;
                delete mcRegistering.value[site.id];
            }
        }

        function _watchTaskCompletion(taskId, callback) {
            // Poll in background until task completes, then call callback
            const check = setInterval(async () => {
                try {
                    const resp = await API.getTaskLogs(taskId, 0);
                    if (resp.code === 200 && resp.data?.status !== 'running') {
                        clearInterval(check);
                        const result = resp.data.result;
                        callback(resp.data.status === 'success', result);
                    }
                } catch (e) { /* ignore */ }
            }, 1000);
        }
        function closeTaskLog() {
            if (taskLogPollTimer.value) {
                clearInterval(taskLogPollTimer.value);
                taskLogPollTimer.value = null;
            }
            taskLogVisible.value = false;
        }
        async function loadMCStatusForSite(site) {
            try {
                const resp = await API.getMCStatus(site.id);
                if (resp.code === 200) {
                    const d = resp.data;
                    if (d.google_feed_url) mcFeedUrls.value[site.id] = d.google_feed_url;
                    site.google_verification_done = d.google_verification_done;
                    site.google_mc_account_id = d.google_mc_account_id;
                }
            } catch (e) {}
        }
        // Profile management
        async function createNewProfile() {
            const name = mcNewProfileName.value.trim();
            if (!name) { showToast('请输入 Profile 名称', 'error'); return; }
            try {
                const resp = await API.createCloakbrowserProfile(name, mcNewGoogleEmail.value.trim(), mcNewProxy.value.trim(), mcNewCountry.value, mcNewPlatform.value || null);
                if (resp.code === 200) {
                    showToast('Profile 创建成功');
                    mcNewProfileName.value = ''; mcNewGoogleEmail.value = ''; mcNewProxy.value = '';
                    showCreateProfile.value = false;
                    await loadCloakbrowserProfiles();
                } else { showToast(resp.message || '创建失败', 'error'); }
            } catch (e) { showToast('创建失败', 'error'); }
        }
        async function deleteProfile(name) {
            if (!confirm(`确定删除 Profile "${name}"？将删除所有 cookies 和配置。`)) return;
            try {
                const resp = await API.deleteCloakbrowserProfile(name);
                if (resp.code === 200) { showToast(resp.message); await loadCloakbrowserProfiles(); }
                else { showToast(resp.message || '删除失败', 'error'); }
            } catch (e) { showToast('删除失败', 'error'); }
        }
        const mcNewProfileName = ref('');
        const mcNewGoogleEmail = ref('');
        const mcNewProxy = ref('');
        const mcNewCountry = ref('US');
        const mcNewPlatform = ref('');
        const showCreateProfile = ref(false);
        const showMcProfilePanel = ref(false);

        // ---- Brand Kits ----
        async function loadBrandKits() {
            brandKitsLoading.value = true;
            try { const resp = await API.getBrandKits(); if (resp.code === 200) brandKits.value = resp.data || []; } catch (e) {} finally { brandKitsLoading.value = false; }
        }
        async function loadProxies() {
            try { const resp = await API.getProxies(); if (resp.code === 200) proxies.value = resp.data || []; } catch (e) {}
            try { const resp = await API.getAvailableProxies(); if (resp.code === 200) availableProxies.value = resp.data || []; } catch (e) {}
        }
        async function handleImportProxies() {
            importingProxies.value = true;
            try {
                const resp = await API.importProxies();
                if (resp.code === 200) { showToast(resp.message); await loadProxies(); }
                else { showToast(resp.message || '导入失败', 'error'); }
            } catch (e) { showToast('导入失败', 'error'); }
            finally { importingProxies.value = false; }
        }
        async function handleImportProxyText() {
            const text = importingProxyText.value.trim();
            if (!text) { showToast('请粘贴代理列表', 'error'); return; }
            importingProxies.value = true;
            try {
                const resp = await API.importProxiesText(text, importingProxyType.value);
                if (resp.code === 200) {
                    showToast(resp.message);
                    importingProxyText.value = '';
                    await loadProxies();
                } else { showToast(resp.message || '导入失败', 'error'); }
            } catch (e) { showToast('导入失败', 'error'); }
            finally { importingProxies.value = false; }
        }

        // ---- Google Account Pool ----
        async function loadGoogleAccounts() {
            try {
                const resp = await API.getGoogleAccounts();
                console.log('[loadGoogleAccounts] getGoogleAccounts resp.code:', resp.code, 'data count:', resp.data?.length);
                if (resp.data && resp.data.length > 0) {
                    console.log('[loadGoogleAccounts] first account:', JSON.stringify(resp.data[0]));
                }
                if (resp.code === 200) googleAccounts.value = resp.data || [];
            } catch (e) { console.error('[loadGoogleAccounts] getGoogleAccounts error:', e); }
            try {
                const resp = await API.getAvailableGoogleAccounts();
                console.log('[loadGoogleAccounts] getAvailableGoogleAccounts resp.code:', resp.code, 'data count:', resp.data?.length);
                if (resp.data && resp.data.length > 0) {
                    console.log('[loadGoogleAccounts] available first account:', JSON.stringify(resp.data[0]));
                }
                if (resp.code === 200) availableGoogleAccounts.value = resp.data || [];
            } catch (e) { console.error('[loadGoogleAccounts] getAvailableGoogleAccounts error:', e); }
        }
        async function handleImportGoogleAccounts() {
            if (!googleAccountsText.value.trim()) { showToast('请粘贴 TXT 内容', 'error'); return; }
            importingGoogleAccounts.value = true;
            try {
                const resp = await API.importGoogleAccounts(googleAccountsText.value);
                if (resp.code === 200) { showToast(resp.message); googleAccountsText.value = ''; await loadGoogleAccounts(); }
                else { showToast(resp.message || '导入失败', 'error'); }
            } catch (e) { showToast('导入失败', 'error'); }
            finally { importingGoogleAccounts.value = false; }
        }
        async function handleDeleteGoogleAccount(id) {
            if (!confirm('确定删除此 Google 账户？')) return;
            try {
                const resp = await API.deleteGoogleAccount(id);
                if (resp.code === 200) { showToast(resp.message); await loadGoogleAccounts(); }
                else { showToast(resp.message || '删除失败', 'error'); }
            } catch (e) { showToast('删除失败', 'error'); }
        }

        const selectedProfileProxy = ref('');

        function onProfileChange() {
            const name = brandKitForm.cloakbrowser_profile_name;
            if (!name) {
                selectedProfileProxy.value = '';
                brandKitForm.proxy = '';
                brandKitForm.proxy_id = null;
                return;
            }
            const profile = cloakbrowserProfiles.value.find(p => p.name === name);
            if (profile && profile.proxy) {
                selectedProfileProxy.value = profile.proxy;
                brandKitForm.proxy = profile.proxy;
                // Also try to find matching proxy in pool by URL
                const match = availableProxies.value.find(p => p.proxy_url === profile.proxy);
                if (match) {
                    brandKitForm.proxy_id = match.id;
                } else {
                    brandKitForm.proxy_id = null;  // profile proxy not in pool, but still saved as proxy string
                }
            } else {
                selectedProfileProxy.value = '';
                brandKitForm.proxy = '';
                brandKitForm.proxy_id = null;
            }
        }

        function openBrandKitModal(kit) {
            loadProxies();
            loadGoogleAccounts();
            loadCloakbrowserProfiles();
            if (kit) {
                brandKitEditId.value = kit.id;
                Object.assign(brandKitForm, { name: kit.name, industry: kit.industry, proxy: kit.proxy || '', proxy_id: kit.proxy_id || null, google_account_id: kit.google_account_id || null, cloakbrowser_profile_name: kit.cloakbrowser_profile_name || '' });
                onProfileChange();  // restore proxy preview
            } else {
                brandKitEditId.value = null;
                Object.keys(brandKitForm).forEach(k => brandKitForm[k] = '');
                brandKitForm.proxy_id = null;
                brandKitForm.google_account_id = null;
                brandKitForm.cloakbrowser_profile_name = '';
                selectedProfileProxy.value = '';
            }
            showBrandKitModal.value = true;
        }
        function closeBrandKitModal() { showBrandKitModal.value = false; }
        async function handleSaveBrandKit() {
            if (!brandKitForm.name.trim()) { showToast('请输入套件名称', 'error'); return; }
            if (!brandKitEditId.value && !brandKitForm.proxy_id && !brandKitForm.cloakbrowser_profile_name) { showToast('请选择指纹环境（含代理）或手动指定代理', 'error'); return; }
            try {
                let resp;
                if (brandKitEditId.value) {
                    resp = await API.updateBrandKit(brandKitEditId.value, brandKitForm);
                } else {
                    resp = await API.createBrandKit(brandKitForm);
                }
                if (resp.code === 200) { showToast(brandKitEditId.value ? '套件已更新' : '套件已创建'); closeBrandKitModal(); await loadBrandKits(); await loadProxies(); await loadGoogleAccounts(); }
                else { showToast(resp.message || '操作失败', 'error'); }
            } catch (e) { showToast('操作失败', 'error'); }
        }
        async function handleDeleteBrandKit(kit) {
            if (!confirm(`确定删除品牌套件 "${kit.name}"？相关文件也将被删除。`)) return;
            try { await API.deleteBrandKit(kit.id); showToast('套件已删除'); await loadBrandKits(); loadCloakbrowserProfiles(); loadProxies(); loadGoogleAccounts(); } catch (e) { showToast('删除失败', 'error'); }
        }
        async function handleGenerateBrandKit(kit) {
            brandKitGenerating[kit.id] = { status: 'running', steps: ['AI 生成 SVG Logo', 'AI 生成商家信息', 'SVG 文字转路径', 'SVG 优化', '导出品牌套件', '创建指纹环境'], current: 0 };
            try {
                const resp = await API.generateBrandKit(kit.id);
                if (resp.code !== 200) { brandKitGenerating[kit.id] = { status: 'failed', error: resp.message }; showToast(resp.message || '生成失败', 'error'); return; }
                pollBrandKitStatus(kit.id);
            } catch (e) { brandKitGenerating[kit.id] = { status: 'failed', error: e.message }; showToast('生成失败', 'error'); }
        }
        function pollBrandKitStatus(kitId) {
            const timer = setInterval(async () => {
                try {
                    const resp = await API.getBrandKitStatus(kitId);
                    if (resp.code === 200 && resp.data) {
                        const st = resp.data;
                        if (st.steps && Array.isArray(st.steps)) {
                            let current = 0;
                            st.steps.forEach((s, i) => { if (s.status === 'done') current = i + 1; else if (s.status === 'running') current = i; });
                            brandKitGenerating[kitId] = { status: st.status, steps: st.steps, current };
                        }
                        if (st.status === 'ready') {
                            clearInterval(timer);
                            showToast('品牌套件生成完成');
                            await loadBrandKits();
                            // Refresh detail view if open
                            if (brandKitDetail.value && brandKitDetail.value.id === kitId) {
                                const d = await API.getBrandKit(kitId);
                                if (d.code === 200) brandKitDetail.value = d.data;
                            }
                        } else if (st.status === 'failed') {
                            clearInterval(timer);
                            brandKitGenerating[kitId] = { status: 'failed', error: st.message || '生成失败' };
                            showToast(st.message || '生成失败', 'error');
                            await loadBrandKits();
                        }
                    }
                } catch (e) { /* poll error, continue */ }
            }, 2000);
        }
        async function openBrandKitDetail(kit) {
            try {
                const resp = await API.getBrandKit(kit.id);
                if (resp.code === 200) {
                    brandKitDetail.value = resp.data;
                    brandKitDetailTab.value = 'info';
                    currentPage.value = 'brand-kits-detail';
                }
            } catch (e) { showToast('加载失败', 'error'); }
        }
        function handleDownloadBrandKitFile(filename) {
            if (!brandKitDetail.value) return;
            API.downloadBrandKitFile(brandKitDetail.value.id, filename);
        }

        function loadBrandKitConfigForms() {
            if (!brandKitDetail.value) return;
            const wc = brandKitDetail.value.woo_config || {};
            brandKitWooForm.address = wc.address || '';
            brandKitWooForm.city = wc.city || '';
            brandKitWooForm.country_state = wc.country_state || '';
            brandKitWooForm.postcode = wc.postcode || '';
            brandKitWooForm.allowed_countries = wc.allowed_countries || '';

            const fc = brandKitDetail.value.footer_config || {};
            brandKitFooterForm.address = fc.address || '';
            brandKitFooterForm.phone = fc.phone || '';
            brandKitFooterForm.email = fc.email || '';

            const tc = brandKitDetail.value.tax_config || {};
            brandKitTaxForm.tax_enabled = tc.tax_enabled !== false;
            brandKitTaxForm.prices_include_tax = tc.prices_include_tax || false;
            const rates = tc.tax_rates || [];
            if (rates.length > 0) {
                brandKitTaxForm.tax_rate_name = rates[0].name || '';
                brandKitTaxForm.tax_rate = rates[0].rate || '';
                brandKitTaxForm.tax_rate_country = rates[0].country || 'US';
                brandKitTaxForm.tax_rate_state = rates[0].state || '';
            }

            const sc = brandKitDetail.value.shipping_config || {};
            brandKitShippingForm.zone_name = sc.zone_name || 'Free Shipping';
            brandKitShippingForm.country = sc.country || 'US';
            brandKitShippingForm.min_amount = sc.min_amount || '';
        }

        async function saveBrandKitConfig(type) {
            if (!brandKitDetail.value) return;
            brandKitConfigSaving.value = true;
            try {
                const data = {};
                if (type === 'woo') {
                    data.woo_config = {
                        address: brandKitWooForm.address,
                        city: brandKitWooForm.city,
                        country_state: brandKitWooForm.country_state,
                        postcode: brandKitWooForm.postcode,
                        allowed_countries: brandKitWooForm.allowed_countries,
                    };
                } else if (type === 'footer') {
                    data.footer_config = {
                        address: brandKitFooterForm.address,
                        phone: brandKitFooterForm.phone,
                        email: brandKitFooterForm.email,
                    };
                } else if (type === 'tax') {
                    data.tax_config = {
                        tax_enabled: brandKitTaxForm.tax_enabled,
                        prices_include_tax: brandKitTaxForm.prices_include_tax,
                        tax_rates: brandKitTaxForm.tax_rate_name ? [{
                            name: brandKitTaxForm.tax_rate_name,
                            rate: brandKitTaxForm.tax_rate,
                            country: brandKitTaxForm.tax_rate_country,
                            state: brandKitTaxForm.tax_rate_state,
                            shipping: true,
                            priority: 1,
                        }] : [],
                    };
                } else if (type === 'shipping') {
                    data.shipping_config = {
                        zone_name: brandKitShippingForm.zone_name,
                        country: brandKitShippingForm.country,
                        min_amount: brandKitShippingForm.min_amount || '',
                    };
                }
                const resp = await API.saveBrandKitConfig(brandKitDetail.value.id, data);
                if (resp.code === 200) {
                    brandKitDetail.value = resp.data;
                    const labels = { woo: '商店配置', footer: '页脚配置', tax: '税费配置', shipping: '运费配置' };
                    showToast((labels[type] || '配置') + '已保存');
                } else {
                    showToast(resp.message || '保存失败', 'error');
                }
            } catch (e) { showToast('保存失败', 'error'); }
            brandKitConfigSaving.value = false;
        }

        // ---- User Management ----
        async function loadUsers() {
            try { const resp = await API.getUsers(); if (resp.code === 200) users.value = resp.data || []; } catch (e) {}
        }
        function openUserModal(user) {
            userFormError.value = '';
            loadPanelEnvironments();
            if (user) {
                userEditId.value = user.id;
                Object.assign(userForm, { username: user.username, password: '', role: user.role, panel_environment_id: user.panel_environment_id || null });
            } else {
                userEditId.value = null;
                Object.assign(userForm, { username: '', password: '', role: 'operator', panel_environment_id: null });
            }
            showUserModal.value = true;
        }
        function closeUserModal() { showUserModal.value = false; }
        async function handleSaveUser() {
            userFormError.value = '';
            if (!userForm.username) { userFormError.value = '请输入用户名'; return; }
            if (!userEditId.value && !userForm.password) { userFormError.value = '请输入密码'; return; }
            try {
                const data = { username: userForm.username, role: userForm.role, panel_environment_id: userForm.panel_environment_id };
                if (userForm.password) data.password = userForm.password;
                let resp;
                if (userEditId.value) {
                    resp = await API.updateUser(userEditId.value, data);
                } else {
                    resp = await API.createUser(data);
                }
                if (resp.code === 200) {
                    showToast(userEditId.value ? '用户已更新' : '用户已创建');
                    closeUserModal();
                    loadUsers();
                } else {
                    userFormError.value = resp.message || '操作失败';
                }
            } catch (e) { userFormError.value = '操作失败'; }
        }
        async function handleDeleteUser(user) {
            if (!confirm(`确定删除用户 "${user.username}" 吗？此操作不可撤销。`)) return;
            try {
                const resp = await API.deleteUser(user.id);
                if (resp.code === 200) { showToast('用户已删除'); loadUsers(); }
                else { showToast(resp.message || '删除失败', 'error'); }
            } catch (e) { showToast('删除失败', 'error'); }
        }

        // ---- Panel Environment (multi-1Panel) ----
        async function loadPanelEnvironments() {
            try { const resp = await API.listPanelEnvironments(); if (resp.code === 200) panelEnvironments.value = resp.data || []; } catch (e) {}
        }
        function openPanelEnvModal(env) {
            panelEnvFormError.value = '';
            if (env) {
                panelEnvEditId.value = env.id;
                Object.assign(panelEnvForm, { name: env.name, host: env.host, port: env.port, api_key: env.api_key, cf_account_id: env.cf_account_id ?? null });
            } else {
                panelEnvEditId.value = null;
                Object.assign(panelEnvForm, { name: '', host: '', port: 3500, api_key: '', cf_account_id: null });
            }
            showPanelEnvModal.value = true;
        }
        function closePanelEnvModal() { showPanelEnvModal.value = false; }
        async function handleSavePanelEnv() {
            panelEnvFormError.value = '';
            if (!panelEnvForm.name || !panelEnvForm.host || !panelEnvForm.api_key) { panelEnvFormError.value = '请填写所有必填字段'; return; }
            try {
                const data = { name: panelEnvForm.name, host: panelEnvForm.host, port: panelEnvForm.port, api_key: panelEnvForm.api_key, cf_account_id: panelEnvForm.cf_account_id || null };
                let resp;
                if (panelEnvEditId.value) {
                    resp = await API.updatePanelEnvironment(panelEnvEditId.value, data);
                } else {
                    resp = await API.createPanelEnvironment(data);
                }
                if (resp.code === 200) {
                    showToast(panelEnvEditId.value ? '环境已更新' : '环境已创建');
                    closePanelEnvModal();
                    loadPanelEnvironments();
                } else {
                    panelEnvFormError.value = resp.message || '操作失败';
                }
            } catch (e) { panelEnvFormError.value = '操作失败'; }
        }
        async function handleDeletePanelEnv(env) {
            if (!confirm(`确定删除环境 "${env.name}" 吗？关联该环境的用户将被重置。`)) return;
            try {
                const resp = await API.deletePanelEnvironment(env.id);
                if (resp.code === 200) { showToast('环境已删除'); loadPanelEnvironments(); }
                else { showToast(resp.message || '删除失败', 'error'); }
            } catch (e) { showToast('删除失败', 'error'); }
        }
        async function handleSetDefaultPanelEnv(env) {
            try {
                const resp = await API.setDefaultPanelEnvironment(env.id);
                if (resp.code === 200) { showToast('已设为默认'); loadPanelEnvironments(); }
                else { showToast(resp.message || '设置失败', 'error'); }
            } catch (e) { showToast('设置失败', 'error'); }
        }

        // ---- Fingerprint Categories & Profile Mapping ----
        async function loadFingerprintCategories() {
            try { const resp = await API.getFingerprintCategories(); if (resp.code === 200) fingerprintCategories.value = resp.data || []; } catch (e) {}
        }
        async function createFingerprintCategory() {
            const name = newCategoryName.value.trim();
            if (!name) { showToast('请输入分类名称', 'error'); return; }
            try {
                const resp = await API.createFingerprintCategory({ name });
                if (resp.code === 200) { showToast('分类已创建'); newCategoryName.value = ''; loadFingerprintCategories(); }
                else { showToast(resp.message || '创建失败', 'error'); }
            } catch (e) { showToast('创建失败', 'error'); }
        }
        async function deleteFingerprintCategory(catId) {
            if (!confirm('确定删除此分类？Profile 将变为未分类。')) return;
            try {
                const resp = await API.deleteFingerprintCategory(catId);
                if (resp.code === 200) { showToast('分类已删除'); loadFingerprintCategories(); loadProfileCategories(); }
                else { showToast(resp.message || '删除失败', 'error'); }
            } catch (e) { showToast('删除失败', 'error'); }
        }
        
        // Get profiles filtered by category
        function getProfilesByCategory(catId) {
            const profileNames = profileCategories.value
                .filter(pc => pc.category_id === catId)
                .map(pc => pc.profile_name);
            return cloakbrowserProfiles.value.filter(p => profileNames.includes(p.name));
        }

                // Export all system data
        async function exportSystemData() {
            try {
                showToast('正在导出...');
                const resp = await API.exportSystem();
                if (resp.code === 200) {
                    const blob = new Blob([JSON.stringify(resp.data, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'kairui-export-' + new Date().toISOString().slice(0, 10) + '.json';
                    a.click();
                    URL.revokeObjectURL(url);
                    showToast('导出成功');
                } else {
                    showToast(resp.message || '导出失败', 'error');
                }
            } catch (e) {
                showToast('导出失败: ' + e.message, 'error');
            }
        }

        // Trigger import file dialog
        function importSystemData() {
            importFileInput.value.click();
        }

        // Handle import file selection
        async function handleImportFile(event) {
            const file = event.target.files[0];
            if (!file) return;
            if (!confirm('导入将覆盖现有数据，确定继续？')) {
                event.target.value = '';
                return;
            }
            try {
                showToast('正在导入...');
                const text = await file.text();
                const data = JSON.parse(text);
                const resp = await API.importSystem(data);
                if (resp.code === 200) {
                    showToast(resp.message || '导入成功，即将刷新...');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast(resp.message || '导入失败', 'error');
                }
            } catch (e) {
                showToast('导入失败: ' + e.message, 'error');
            }
            event.target.value = '';
        }

// Import fingerprint profiles from text input (auto-parses proxy, syncs to SOCKS5 pool)
        async function importFingerprintProfiles() {
            const text = importingFingerprintText.value.trim();
            if (!text) { showToast('请输入 Profile 名称', 'error'); return; }
            if (!activeFingerprintCategory.value) { showToast('请先选择分类', 'error'); return; }

            const names = text.split('\n').map(n => n.trim()).filter(n => n.length > 0);
            if (!names.length) { showToast('未识别到有效的 Profile 名称', 'error'); return; }

            importingFingerprints.value = true;
            importFingerprintResult.value = '';
            let created = 0, skipped = 0;
            // Collect proxy lines in IP:PORT:USER:PASS format for batch import
            const proxyLines = [];

            for (const name of names) {
                try {
                    let proxy = '', country = 'US';
                    let host = '', port = '', user = '', pass = '';

                    // Format 1: user:pass@host:port (contains @)
                    const atIdx = name.indexOf('@');
                    if (atIdx >= 0) {
                        const userPass = name.substring(0, atIdx);
                        const hostPort = name.substring(atIdx + 1);
                        const upColon = userPass.indexOf(':');
                        const hpColon = hostPort.lastIndexOf(':');
                        if (upColon >= 0 && hpColon >= 0) {
                            user = userPass.substring(0, upColon);
                            pass = userPass.substring(upColon + 1);
                            host = hostPort.substring(0, hpColon);
                            port = hostPort.substring(hpColon + 1);
                        }
                    } else {
                        // Format 2: host:port:user:pass_profile-params
                        const parts = name.split(':');
                        if (parts.length >= 4) {
                            // Check if 2nd part looks like a port (numeric, 2-5 digits)
                            if (/^\d{2,5}$/.test(parts[1])) {
                                // host:port:user:pass_PROFILE_PARAMS
                                host = parts[0]; port = parts[1]; user = parts[2]; pass = parts[3];
                            } else {
                                // user:pass:host:port
                                user = parts[0]; pass = parts[1]; host = parts[2]; port = parts[3];
                            }
                        }
                    }

                    // Extract profile params from password (format: PASS_country-XX_session-XXX_lifetime-XXh...)
                    let profileName = name;  // fallback: sanitized full string
                    let purePass = pass;
                    if (pass && pass.includes('_country-')) {
                        const underscoreIdx = pass.indexOf('_country-');
                        purePass = pass.substring(0, underscoreIdx);
                        profileName = pass.substring(underscoreIdx + 1);  // country-us_session-xxx_lifetime-72h...
                        // Extract country from profile name
                        const countryMatch = profileName.match(/country-([a-z]{2})/i);
                        if (countryMatch) {
                            country = countryMatch[1].toUpperCase();
                        }
                    } else if (pass && pass.includes('_session-')) {
                        const underscoreIdx = pass.indexOf('_session-');
                        purePass = pass.substring(0, underscoreIdx);
                        profileName = pass.substring(underscoreIdx + 1);
                    }

                    if (host && port && user && purePass) {
                        proxy = 'socks5://' + encodeURIComponent(user) + ':' + encodeURIComponent(purePass) + '@' + host + ':' + port;
                        // Collect for batch proxy import: IP:PORT:USER:PASS (pure password)
                        proxyLines.push(host + ':' + port + ':' + user + ':' + purePass);
                    }

                    const resp = await API.createCloakbrowserProfile(profileName, '', proxy, country, null);
                    if (resp.code === 200 || resp.code === 409) {
                        if (resp.code === 200) created++;
                        else skipped++;
                        const sanitizedName = profileName.replace(/[^a-zA-Z0-9\-_]/g, '-');
                        await API.setProfileCategory(sanitizedName, activeFingerprintCategory.value);
                    }
                } catch (e) {
                    console.error('Import profile error:', name, e);
                }
            }

            // Auto-sync proxies to SOCKS5 pool (IP:PORT:USER:PASS format)
            if (proxyLines.length > 0) {
                try {
                    const proxyText = proxyLines.join('\n');
                    const proxyResp = await API.importProxiesText(proxyText, 'socks5');
                    if (proxyResp.code === 200) {
                        console.log('Proxy auto-sync: ' + proxyResp.message);
                    }
                } catch (e) {
                    console.error('Auto-sync proxy error:', e);
                }
            }

            importingFingerprints.value = false;
            importingFingerprintText.value = '';
            if (created === 0 && skipped > 0) {
                importFingerprintResult.value = '所有 ' + skipped + ' 个 Profile 均已存在，已关联到当前分类（代理已同步）';
            } else if (created > 0 && skipped > 0) {
                importFingerprintResult.value = '成功导入 ' + created + ' 个，' + skipped + ' 个已存在（已关联分类，代理已同步）';
            } else if (created > 0) {
                importFingerprintResult.value = '成功导入 ' + created + ' 个 Profile（代理已同步）';
            } else {
                importFingerprintResult.value = '未识别到有效的 Profile 名称';
            }
            await loadCloakbrowserProfiles();
            await loadProfileCategories();
            await loadProxies();
        }

        // Remove profile from current category (set to uncategorized)
        async function removeProfileFromCategory(profileName) {
            try {
                await API.setProfileCategory(profileName, null);
                showToast('已移除分类');
                await loadProfileCategories();
            } catch (e) {
                showToast('操作失败', 'error');
            }
        }
async function loadProfileCategories() {
            try { const resp = await API.getProfileCategories(); if (resp.code === 200) profileCategories.value = resp.data || []; } catch (e) {}
        }
        async function handleSetProfileCategory(profileName, categoryId) {
            try {
                const resp = await API.setProfileCategory(profileName, categoryId);
                if (resp.code === 200) { showToast('分类已更新'); loadProfileCategories(); }
                else { showToast(resp.message || '更新失败', 'error'); }
            } catch (e) { showToast('更新失败', 'error'); }
        }

        onMounted(async () => {
            if (API.token) { try { const resp = await API.checkAuth(); if (resp.code === 200) { isLoggedIn.value = true; currentUser.value = resp.data.username; currentUserRole.value = resp.data.role || ''; currentUserId.value = resp.data.user_id || null; currentPanelEnv.value = resp.data.panel_environment || null; await loadInitialData(); } } catch (e) { API.logout(); } }
        });

        return {
            isLoggedIn, currentUser, currentUserRole, currentUserId, currentPanelEnv, currentPage, loading, toast, modal,
            loginForm, loginError, sites, searchQuery, filteredSites,
            panelConnected, panelWebsites, panelInstalledApps, panelGroups,
            wizardStep, wizardOpen, wizardMode, wizardSiteId,
            createForm, createProgress, wpInstallStatuses,
            feedSiteId, feedProducts, showFeedProductModal, feedEditId, feedEditForm,
            feedMenuOpen, sourceTab, feedStats, feedStatsLoading,
            wooStats, wooStatsLoading, wooStatsPeriod, wooStatsDateMin, wooStatsDateMax,
            walmartCategories, walmartSelectedCategory, walmartProducts, walmartLoading, walmartError, walmartFetchLimit,
            walmartEnriching, walmartEnrichProgress, generatedFeed,
            walmartPage, walmartPerPage, walmartPagedProducts, walmartTotalPages,
            amazonSearchResults, amazonSearchLoading, amazonSearchError, amazonSelectedIndices,
            amazonSearchProgress, showAmazonImportModal, amazonImportModalText,
            showAmazonUrlModal, amazonUrlModalText,
            showConvertLogModal, convertLogLines, convertLogProgress, converting,
            feedSelectedIndices, showFeedDescModal, feedDescContent,
            loadPersistedAmazonResults, deleteSelectedAmazonProducts,
            openAmazonImportModal, closeAmazonImportModal, startAmazonSearchFromModal,
            openAmazonUrlModal, closeAmazonUrlModal, startAmazonUrlImport,
            handleAmazonFileUpload, toggleAmazonSelect, selectAllAmazon,
            closeConvertLogModal, toggleFeedSelect, selectAllFeed, deleteSelectedFeedItems, showFeedDescription, buildFeedDetailText,
            wooProducts, wooSelectedIndices, wooConverting, wooConvertProgress,
            feedSyncSiteId, wooSyncSiteId, syncingFeed, syncingWoo,
            convertToWooCommerce, loadWooProducts, toggleWooSelect, selectAllWoo, deleteSelectedWooProducts,
            createFeedForSite, cleanFeedFromSite, syncWooToSite, cleanWooFromSite, generateFeedFromWoo, wooGeneratingFeed, feedUrl,
            cfConnected, cfToken, cfAccounts, cfSelectedAccountId,
            deepseekApiKeys, deepseekVisibleKeys, deepseekKeyErrors, deepseekConnected, deepseekVerify,
            crawlbaseApiKeys, crawlbaseVisibleKeys, crawlbaseKeyErrors, crawlbaseConnected, crawlbaseVerify,
            cloakbrowserProfiles, loadCloakbrowserProfiles,
            mcRegistering, mcFeedUrls, mcProfileDir,
            fingerprintEnabled, envTestBlocking,
            agnesApiKey, agnesVisibleKey, agnesVerifying, agnesConnected, agnesVerify,
            taskLogVisible, taskLogTitle, taskLogLines, taskLogStatus, taskLogResult, taskLogRef,
            closeTaskLog,
            mcNewProfileName, mcNewGoogleEmail, mcNewProxy, mcNewCountry, mcNewPlatform,
            showCreateProfile, showMcProfilePanel, createNewProfile, deleteProfile,
            registerMCForSite, loadMCStatusForSite,

            showEditModal, editForm, editingSiteId, globalConfig, deployOverlay, closeDeployOverlay,
            postInstallSite, aiBrandName, aiConfigRunning, aiConfigError, aiConfigSteps, aiConfigKey,
            wooConfigForm, wooConfigSaving,
            brandConfigBrandName, brandConfigRunning, brandConfigError, brandConfigSteps, brandConfigKey,
            brandConfigSelectedKitId, wizardBrandKitId, brandKitsForWizard,
            currentProgressPct, currentProgressLabel,
            onBrandConfigKitChange, startBrandConfig, autoContinue, skipBrandConfig,
            loadBrandKitsForWizard,
            finishPostInstall, installCfSslPlugin,
            // Demo import
            demos, demosLoading, selectedDemoId, selectedCategory, demoCategories, filteredDemos,
            demoImporting, demoImportStatus, loadDemosForSite, startDemoImport, finishDemoImport,
            demoModal, demoModalCategories, demoModalFiltered, openDemoImportForSite, startDemoImportFromModal,
            // Environment selection (operator login)
            envSelectModal, submitEnvSelection,
            // Meta tag injection
            metaModal, openMetaModal, submitMetaTag,
            // Pipeline timeline
            pipelineStatuses, tooltipSiteId, loadPipelineStatus, refreshPipelineStatus, pipelineLineState, siteStatusText, stitchProgressTitle,
            // Silent install
            silentInstallSites, startSilentInstall,
            brandKitApplyStatus, brandKitApplying, applyBrandKitForm, brandKitsForSelect,
            loadBrandKitsForSelect,
            handleLogin, handleLogout, refreshSites, syncWithPanel,
            openWizard, closeWizard, onWizardGroupChange, wizardCreateSite, testProfileForWizard, profileTestState,

            openEditModal, submitEdit, confirmDelete, fixSiteWebsite, openSiteBrowser, saveGlobalConfig, exportCSV,
            loadFeedProducts, openFeedProductModal, closeFeedProductModal, handleSaveFeedProduct,
            handleDeleteFeedProduct, handleImportSampleProducts, handleExportFeed,
            toggleFeedMenu, setSourceTab, loadFeedStats,
            loadWooStats, setWooStatsPeriod, formatMoney, formatInt,
            loadWalmartCategories, fetchWalmartBestsellers, loadPersistedWalmartProducts, exportWalmartData,
            enrichWalmartProducts, loadGeneratedFeed, clearGeneratedFeed,
            walmartGoPage,
            cfVerify, loadCfAccounts, handleDeleteCfAccount, handleSetDefaultCfAccount,
            brandKits, brandKitsLoading, showBrandKitModal, brandKitEditId, brandKitForm,
            brandKitGenerating, showBrandKitDetail, brandKitDetail, brandKitDetailTab,
            proxies, availableProxies, importingProxies, importingProxyText, importingProxyType,
            loadProxies, handleImportProxies, handleImportProxyText,
            googleAccounts, availableGoogleAccounts, importingGoogleAccounts, googleAccountsText,
            loadGoogleAccounts, handleImportGoogleAccounts, handleDeleteGoogleAccount,
            brandKitWooForm, brandKitFooterForm, brandKitTaxForm, brandKitShippingForm, brandKitConfigSaving,
            loadBrandKits, openBrandKitModal, closeBrandKitModal, handleSaveBrandKit,
            selectedProfileProxy, onProfileChange,
            handleDeleteBrandKit, handleGenerateBrandKit, openBrandKitDetail,
            handleDownloadBrandKitFile, loadBrandKitConfigForms, saveBrandKitConfig,
            users, showUserModal, userEditId, userForm, userFormError,
            loadUsers, openUserModal, closeUserModal, handleSaveUser, handleDeleteUser,
            fingerprintCategories, profileCategories, newCategoryName,
            loadFingerprintCategories, createFingerprintCategory, deleteFingerprintCategory,
            loadProfileCategories, handleSetProfileCategory,

            activeFingerprintCategory, importingFingerprintText, importingFingerprints, importFingerprintResult,
            getProfilesByCategory, importFingerprintProfiles, removeProfileFromCategory,            settingsActiveTab, settingsTabs,

            exportSystemData, importSystemData, handleImportFile, importFileInput,            panelEnvironments, showPanelEnvModal, panelEnvEditId, panelEnvForm, panelEnvFormError,
            loadPanelEnvironments, openPanelEnvModal, closePanelEnvModal, handleSavePanelEnv,
            handleDeletePanelEnv, handleSetDefaultPanelEnv,
            showToast, showModal,
        };
    },

    template: `
    <!-- Login -->
    <div v-if="!isLoggedIn" class="min-h-screen bg-background flex items-center justify-center p-md">
        <div class="w-full max-w-5xl bg-surface-container-lowest rounded-xl elevation-3 overflow-hidden flex flex-col md:flex-row min-h-[600px]">
            <div class="hidden md:flex flex-col justify-between w-1/2 bg-primary-container text-on-primary p-xl relative overflow-hidden"
                 style="background-image: url('/images/login-bg.jpg'); background-size: cover; background-position: center;">
                <div class="absolute inset-0 bg-primary/80 z-0"></div>
                <div class="relative z-10">
                    <div class="flex items-center gap-sm mb-lg">
                        <span class="material-symbols-outlined" style="font-size:32px">dataset</span>
                        <span class="font-display-md">凯瑞投流</span>
                    </div>
                </div>
                <div class="relative z-10 mb-xl">
                    <h1 class="font-display-lg mb-md">欢迎回来</h1>
                    <p class="font-body-lg text-lg opacity-80 max-w-md">访问您的企业级电子商务指挥中心。管理库存、编排 Feed 并监控站点绩效。</p>
                </div>
                <div class="relative z-10">
                    <p class="font-label-sm opacity-70">&copy; 2024 凯瑞投流企业版</p>
                </div>
            </div>
            <div class="w-full md:w-1/2 p-xl lg:p-[48px] flex flex-col justify-center bg-surface-container-lowest">
                <div class="md:hidden flex items-center gap-sm mb-xl justify-center">
                    <span class="material-symbols-outlined" style="font-size:32px;color:var(--md-primary)">dataset</span>
                    <span class="font-display-md text-primary">凯瑞投流</span>
                </div>
                <div class="mb-xl text-center md:text-left">
                    <h2 class="font-display-md mb-xs" style="color:var(--md-on-surface)">登录</h2>
                    <p class="font-body-md text-on-surface-variant">请输入您的凭据以访问您的账户。</p>
                </div>
                <div class="flex p-xs bg-surface-container-high rounded-lg mb-lg">
                    <button @click="loginForm.tab = 'admin'"
                        :class="['flex-1 py-sm px-md text-center rounded font-label-md transition-all', loginForm.tab === 'admin' ? 'bg-surface-container-lowest text-on-surface elevation-1' : 'text-on-surface-variant hover:text-on-surface']">
                        管理员
                    </button>
                    <button @click="loginForm.tab = 'operator'"
                        :class="['flex-1 py-sm px-md text-center rounded font-label-md transition-all', loginForm.tab === 'operator' ? 'bg-surface-container-lowest text-on-surface elevation-1' : 'text-on-surface-variant hover:text-on-surface']">
                        操作员
                    </button>
                </div>
                <form @submit.prevent="handleLogin" class="space-y-md">
                    <div>
                        <label class="form-label" for="login-username">用户名</label>
                        <div class="relative">
                            <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2" style="font-size:18px;color:var(--md-outline-variant)">person</span>
                            <input v-model="loginForm.username" id="login-username" type="text" required
                                class="form-input pl-10" placeholder="请输入用户名" autocomplete="username">
                        </div>
                    </div>
                    <div>
                        <label class="form-label" for="login-password">密码</label>
                        <div class="relative">
                            <span class="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2" style="font-size:18px;color:var(--md-outline-variant)">lock</span>
                            <input v-model="loginForm.password" id="login-password" type="password" required
                                class="form-input pl-10" placeholder="请输入密码" autocomplete="current-password">
                        </div>
                    </div>
                    <p v-if="loginError" class="text-error font-label-md">{{ loginError }}</p>
                    <button type="submit" :disabled="loading"
                        class="w-full py-sm px-md bg-primary-container text-on-primary rounded-lg font-body-md font-medium hover:bg-primary transition-colors disabled:opacity-50 elevation-1">
                        <span v-if="!loading">登录</span>
                        <span v-else>登录中...</span>
                    </button>
                </form>
            </div>
        </div>
    </div>

<!-- Main App -->
    <div v-else class="min-h-screen bg-background">
        <!-- Sidebar -->
        <nav class="sidebar">
            <div class="sidebar-brand">
                <span class="material-symbols-outlined" style="font-variation-settings:'FILL' 1; font-size:28px;">dataset</span>
                <span class="brand-name">凯瑞投流</span>
            </div>
            <div class="sidebar-nav">
                <a @click="currentPage = 'dashboard'" :class="['sidebar-link', currentPage === 'dashboard' ? 'active' : '']">
                    <span class="material-symbols-outlined">dashboard</span> 仪表盘
                </a>
                <a @click="currentPage = 'woo-stats'; loadWooStats()" :class="['sidebar-link', currentPage === 'woo-stats' ? 'active' : '']">
                    <span class="material-symbols-outlined">trending_up</span> 销售统计
                </a>
                <a @click="currentPage = 'sites'" :class="['sidebar-link', currentPage === 'sites' ? 'active' : '']">
                    <span class="material-symbols-outlined">language</span> 站点列表
                    <span class="ml-auto bg-primary-container text-on-primary text-xs px-2 py-0.5 rounded-full font-label-sm">{{ sites.length }}</span>
                </a>
                <a @click="currentPage = 'shai-pin-source'" :class="['sidebar-link', currentPage === 'shai-pin-source' ? 'active' : '']">
                    <span class="material-symbols-outlined">inventory_2</span> 产品来源
                </a>
                <a @click="currentPage = 'woocommerce-products'" :class="['sidebar-link', currentPage === 'woocommerce-products' ? 'active' : '']">
                    <span class="material-symbols-outlined">shopping_cart</span> 网站产品
                </a>
                <a @click="currentPage = 'shai-pin-feed'" :class="['sidebar-link', currentPage === 'shai-pin-feed' ? 'active' : '']">
                    <span class="material-symbols-outlined">rss_feed</span> 数据源生成
                </a>
                <a @click="currentPage = 'mc-automation'" :class="['sidebar-link', currentPage === 'mc-automation' ? 'active' : '']">
                    <span class="material-symbols-outlined">hub</span> Google MC
                </a>
                <a @click="currentPage = 'brand-kits'; loadBrandKits()" :class="['sidebar-link', currentPage === 'brand-kits' || currentPage === 'brand-kits-detail' ? 'active' : '']">
                    <span class="material-symbols-outlined">branding_watermark</span> 品牌套件
                </a>
                <div class="sidebar-divider"></div>
                <a @click="currentPage = 'users'; loadUsers()" :class="['sidebar-link', currentPage === 'users' ? 'active' : '']">
                    <span class="material-symbols-outlined">group</span> 用户管理
                </a>
                <a @click="currentPage = 'settings'" :class="['sidebar-link', currentPage === 'settings' ? 'active' : '']">
                    <span class="material-symbols-outlined">settings</span> 系统设置
                </a>

            </div>
            <div class="sidebar-user">
                <div class="avatar">{{ currentUser ? currentUser.substring(0,2).toUpperCase() : 'AD' }}</div>
                <div>
                    <div class="name">{{ currentUser }}</div>
                    <div class="org">凯瑞电子商务</div>
                </div>
                <a @click="handleLogout" class="ml-auto text-on-surface-variant hover:text-error cursor-pointer" title="退出登录">
                    <span class="material-symbols-outlined">logout</span>
                </a>
            </div>
        </nav>

        <!-- Top Header -->
        <header class="top-header">
            <button @click="toggleMobileSidebar" class="md:hidden text-on-surface-variant p-sm">
                <span class="material-symbols-outlined">menu</span>
            </button>
            <div class="flex items-center gap-md flex-1">
                <div class="relative w-full max-w-md hidden md:block">
                    <span class="material-symbols-outlined absolute left-md top-1/2 -translate-y-1/2 text-outline" style="font-size:18px">search</span>
                    <input v-model="searchQuery" type="text" placeholder="在所有网站和产品中搜索..." class="search-input">
                </div>
            </div>
            <div class="flex items-center gap-lg">
                <div class="flex items-center gap-md">
                    <button class="header-icon-btn inline-flex items-center justify-center">
                        <span class="material-symbols-outlined">notifications</span>
                    </button>
                    <button class="header-icon-btn hidden md:block">
                        <span class="material-symbols-outlined">help_outline</span>
                    </button>
                </div>
                <div class="h-8 w-px bg-outline-variant hidden md:block"></div>
                <div class="flex items-center gap-sm">
                    <span :class="['inline-flex items-center gap-xs font-label-sm', panelConnected ? 'text-[#146c2e]' : 'text-error']">
                        <span class="material-symbols-outlined" style="font-size:14px">{{ panelConnected ? 'cloud_done' : 'cloud_off' }}</span>
                        {{ panelConnected ? '1Panel' : '离线' }}
                    </span>
                </div>
                <button @click="syncWithPanel" class="btn btn-secondary btn-sm">
                    <span class="material-symbols-outlined" style="font-size:16px">sync</span> 同步
                </button>
                <button @click="refreshSites" class="btn btn-primary btn-sm">
                    <span class="material-symbols-outlined" style="font-size:16px">refresh</span> 刷新
                </button>
            </div>
        </header>

<!-- Page Content -->
        <main class="main-content">
            <div class="main-content-inner">
                <div class="page-breadcrumb" v-if="currentPage !== 'dashboard'">
                    <span>凯瑞投流</span>
                    <span class="material-symbols-outlined" style="font-size:16px">chevron_right</span>
                    <span class="current">{{ currentPage === 'sites' ? '站点概览' : currentPage === 'brand-kits' ? '品牌套件' : currentPage === 'brand-kits-detail' ? '品牌套件详情' : currentPage === 'shai-pin-dashboard' ? '筛品' : currentPage === 'shai-pin-source' ? '产品来源' : currentPage === 'shai-pin-feed' ? '数据源生成' : currentPage === 'woocommerce-products' ? '网站产品' : currentPage === 'woo-stats' ? '销售统计' : currentPage === 'mc-automation' ? 'Google MC' : currentPage === 'users' ? '用户管理' : '系统设置' }}</span>
                </div>

<!-- Main Content -->
        <div class="mb-lg">
                <h1 class="page-title">{{ currentPage === 'dashboard' ? '概览' : currentPage === 'sites' ? '站点概览' : currentPage === 'brand-kits' ? '品牌套件' : currentPage === 'brand-kits-detail' ? '品牌套件详情' : currentPage === 'shai-pin-dashboard' ? '筛品' : currentPage === 'shai-pin-source' ? '产品来源' : currentPage === 'shai-pin-feed' ? '数据源生成' : currentPage === 'woocommerce-products' ? '网站产品' : currentPage === 'woo-stats' ? '销售统计' : currentPage === 'mc-automation' ? 'Google MC' : currentPage === 'users' ? '用户管理' : '系统设置' }}</h1>
                <p class="font-body-md text-on-surface-variant mt-xs"><span :class="panelConnected ? 'text-[#146c2e]' : 'text-error'"><span class="material-symbols-outlined text-[10px] mr-1">circle</span>{{ panelConnected ? '1Panel 已连接' : '1Panel 未连接' }}</span></p>
                <div class="flex gap-sm mt-md">
                    <button v-if="currentPage === 'settings'" @click="exportSystemData" class="flex items-center gap-sm px-md py-sm bg-primary-container text-on-primary rounded-lg hover:bg-primary transition-colors font-label-md text-label-md shadow-level-1" title="导出所有配置和数据"><span class="material-symbols-outlined text-[18px]">download</span>导出配置</button>
                    <button v-if="currentPage === 'settings'" @click="importSystemData" class="flex items-center gap-sm px-md py-sm bg-surface-container-low border border-outline-variant text-on-surface-variant rounded-lg hover:bg-surface-container-high transition-colors font-label-md text-label-md"><span class="material-symbols-outlined text-[18px]">upload</span>导入配置</button>
                    <input v-if="currentPage === 'settings'" type="file" ref="importFileInput" @change="handleImportFile" accept=".json" style="position:absolute;width:1px;height:1px;opacity:0;overflow:hidden">
                    <button v-if="currentPage !== 'settings'" @click="syncWithPanel" class="flex items-center gap-sm px-md py-sm bg-primary-container text-on-primary rounded-lg hover:bg-primary transition-colors font-label-md text-label-md shadow-level-1" title="从1Panel同步数据"><span class="material-symbols-outlined text-[18px]">sync</span>同步1Panel</button>
                    <button v-if="currentPage !== 'settings'" @click="refreshSites" class="flex items-center gap-sm px-md py-sm bg-surface-container-low border border-outline-variant text-on-surface-variant rounded-lg hover:bg-surface-container-high transition-colors font-label-md text-label-md"><span class="material-symbols-outlined text-[18px]" :class="loading ? 'animate-spin' : ''">refresh</span>刷新</button>
                </div>
            </div>

            <!-- Dashboard -->
            <div v-if="currentPage === 'dashboard'" class="space-y-lg fade-in">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-md mb-lg">
                    <div class="card"><div class="flex items-center justify-between"><div><p class="font-body-md text-on-surface-variant font-medium">站点总数</p><p class="font-display-md mt-xs text-on-surface">{{ sites.length }}</p></div><div class="p-xs bg-surface-container-low rounded-md"><span class="material-symbols-outlined">language</span></div></div></div>
                    <div class="card"><div class="flex items-center justify-between"><div><p class="font-body-md text-on-surface-variant font-medium">1Panel连接</p><p class="text-3xl font-bold mt-1" :class="panelConnected ? 'text-[#146c2e]' : 'text-error'">{{ panelConnected ? '正常' : '断开' }}</p></div><div class="w-12 h-12 rounded-lg flex items-center justify-center" :class="panelConnected ? 'bg-[#146c2e]/10' : 'bg-error-container'"><i class="fas fa-server text-xl" :class="panelConnected ? 'text-[#146c2e]' : 'text-error'"></i></div></div></div>
                </div>
                <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                    <h3 class="font-semibold text-on-surface mb-4"><span class="material-symbols-outlined">bolt</span>快速操作</h3>
                    <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
                        <button @click="openWizard('single')" class="flex flex-col items-center justify-center gap-sm p-4 border-2 border-dashed border-outline-variant rounded-xl hover:border-primary-container hover:bg-primary-container/5 transition-all text-center"><span class="material-symbols-outlined text-2xl">add_circle</span><p class="text-sm font-medium text-on-surface">创建单个站点</p></button>
                        <button @click="openWizard('batch')" class="flex flex-col items-center justify-center gap-sm p-4 border-2 border-dashed border-outline-variant rounded-xl hover:border-primary-container hover:bg-primary-container/5 transition-all text-center"><span class="material-symbols-outlined text-2xl">library_add</span><p class="text-sm font-medium text-on-surface">批量创建站点</p></button>
                        <button @click="exportCSV" class="flex flex-col items-center justify-center gap-sm p-4 border-2 border-dashed border-outline-variant rounded-xl hover:border-[#146c2e]/40 hover:bg-[#146c2e]/5 transition-all text-center"><span class="material-symbols-outlined text-2xl">description</span><p class="text-sm font-medium text-on-surface">导出CSV</p></button>
                    </div>
                </div>
            </div>

            <!-- 网站产品 Stats -->
            <div v-if="currentPage === 'woo-stats'" class="fade-in">
                <div v-if="wooStatsLoading" class="flex items-center justify-center py-20">
                    <span class="spinner w-4 h-4 inline-block"></span>
                    <span class="ml-3 text-on-surface-variant">加载销售数据中...</span>
                </div>
                <div v-else-if="wooStats">
                    <!-- Period filter -->
                    <div class="flex items-center gap-2 mb-6">
                        <span class="text-sm text-on-surface-variant mr-2">时间范围:</span>
                        <button v-for="p in [{k:'today',l:'今日'},{k:'7day',l:'7天'},{k:'30day',l:'30天'},{k:'month',l:'本月'}]" :key="p.k"
                                @click="setWooStatsPeriod(p.k)"
                                :class="['px-3 py-1.5 rounded-lg text-sm transition', wooStatsPeriod === p.k ? 'bg-primary-container text-on-primary' : 'bg-surface-container text-on-surface-variant hover:bg-surface-container-high']">
                            {{ p.l }}
                        </button>
                    </div>
                    <!-- Summary cards -->
                    <div class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-8">
                        <div class="bg-surface-container-lowest rounded-xl p-5 shadow-level-1">
                            <p class="font-body-md text-on-surface-variant font-medium">总销售额</p>
                            <p class="text-2xl font-bold text-on-surface mt-1">{{ '$' + formatMoney(wooStats.summary.total_sales) }}</p>
                        </div>
                        <div class="bg-surface-container-lowest rounded-xl p-5 shadow-level-1">
                            <p class="font-body-md text-on-surface-variant font-medium">净销售额</p>
                            <p class="text-2xl font-bold text-[#146c2e] mt-1">{{ '$' + formatMoney(wooStats.summary.net_sales) }}</p>
                        </div>
                        <div class="bg-surface-container-lowest rounded-xl p-5 shadow-level-1">
                            <p class="font-body-md text-on-surface-variant font-medium">总订单数</p>
                            <p class="text-2xl font-bold text-primary mt-1">{{ formatInt(wooStats.summary.total_orders) }}</p>
                        </div>
                        <div class="bg-surface-container-lowest rounded-xl p-5 shadow-level-1">
                            <p class="font-body-md text-on-surface-variant font-medium">平均客单价</p>
                            <p class="text-2xl font-bold text-primary mt-1">{{ '$' + formatMoney(wooStats.summary.average_sales) }}</p>
                        </div>
                        <div class="bg-surface-container-lowest rounded-xl p-5 shadow-level-1">
                            <p class="font-body-md text-on-surface-variant font-medium">活跃站点</p>
                            <p class="text-2xl font-bold text-primary mt-1">{{ wooStats.summary.active_sites }}<span class="text-sm text-on-surface-variant font-normal"> / {{ wooStats.summary.total_sites }}</span></p>
                        </div>
                    </div>
                    <!-- Per-site table -->
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                        <table class="w-full text-sm">
                            <thead class="bg-surface-container-low text-on-surface-variant">
                                <tr>
                                    <th class="text-left px-4 py-3 font-medium">站点</th>
                                    <th class="text-right px-4 py-3 font-medium">销售额</th>
                                    <th class="text-right px-4 py-3 font-medium">净销售额</th>
                                    <th class="text-right px-4 py-3 font-medium">订单数</th>
                                    <th class="text-right px-4 py-3 font-medium">平均客单价</th>
                                    <th class="text-center px-4 py-3 font-medium">状态</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-outline-variant">
                                <tr v-for="s in wooStats.sites" :key="s.id" class="hover:bg-surface-container-low">
                                    <td class="px-4 py-3">
                                        <div class="font-medium text-on-surface">{{ s.site_name }}</div>
                                        <div class="text-xs text-on-surface-variant">{{ s.url }}</div>
                                    </td>
                                    <td class="text-right px-4 py-3 text-on-surface">{{ '$' + formatMoney(s.total_sales) }}</td>
                                    <td class="text-right px-4 py-3 text-on-surface">{{ '$' + formatMoney(s.net_sales) }}</td>
                                    <td class="text-right px-4 py-3 text-on-surface">{{ formatInt(s.total_orders) }}</td>
                                    <td class="text-right px-4 py-3 text-on-surface">{{ '$' + formatMoney(s.average_sales) }}</td>
                                    <td class="text-center px-4 py-3">
                                        <span v-if="s.status === 'ok'" class="text-xs bg-[#146c2e]/10 text-[#146c2e] px-2 py-0.5 rounded-full">正常</span>
                                        <span v-else-if="s.status === 'no_woocommerce'" class="text-xs bg-surface-container text-on-surface-variant px-2 py-0.5 rounded-full">无网站产品</span>
                                        <span v-else-if="s.status === 'no_data'" class="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full">无数据</span>
                                        <span v-else class="text-xs bg-error-container text-error px-2 py-0.5 rounded-full">无法连接</span>
                                    </td>
                                </tr>
                            </tbody>
                            <tfoot v-if="wooStats.summary.active_sites > 0" class="bg-blue-50 font-medium">
                                <tr>
                                    <td class="px-4 py-3 text-primary">汇总</td>
                                    <td class="text-right px-4 py-3 text-primary">{{ '$' + formatMoney(wooStats.summary.total_sales) }}</td>
                                    <td class="text-right px-4 py-3 text-primary">{{ '$' + formatMoney(wooStats.summary.net_sales) }}</td>
                                    <td class="text-right px-4 py-3 text-primary">{{ formatInt(wooStats.summary.total_orders) }}</td>
                                    <td class="text-right px-4 py-3 text-primary">{{ '$' + formatMoney(wooStats.summary.average_sales) }}</td>
                                    <td class="text-center px-4 py-3 text-primary">{{ wooStats.summary.active_sites }} 个站点</td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                </div>
                <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center text-on-surface-variant">
                    <i class="fas fa-chart-bar text-5xl mb-4"></i>
                    <p class="text-lg font-medium text-on-surface-variant mb-2">暂无销售数据</p>
                    <p>请先创建站点并安装 网站产品</p>
                </div>
            </div>

            <!-- Sites List -->
            <div v-if="currentPage === 'sites'" class="fade-in">
                <div class="flex items-center justify-between mb-6">
                    <div class="relative"><i class="fas fa-search absolute left-3 top-3 text-on-surface-variant"></i><input v-model="searchQuery" type="text" placeholder="搜索站点..." class="pl-10 pr-4 py-2 border rounded-lg focus:border-primary w-64"></div>
                    <div class="flex gap-3"><button @click="openWizard('single')" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm"><span class="material-symbols-outlined text-[18px]">add_circle</span>创建站点</button><button @click="exportCSV" class="btn btn-secondary text-sm"><i class="fas fa-download mr-2"></i>导出CSV</button></div>
                </div>
                <div v-if="!filteredSites.length" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center text-on-surface-variant"><i class="fas fa-inbox text-4xl mb-4"></i><p>暂无站点，点击"创建站点"开始</p></div>
                <div v-else class="space-y-3">
                    <div v-for="site in filteredSites" :key="site.id" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-4">
                        <!-- Main row: site info + timeline + actions -->
                        <div class="flex items-center gap-3">
                            <!-- Site name + tooltip trigger -->
                            <div class="w-40 flex-shrink-0">
                                <div class="font-medium text-on-surface text-sm truncate">{{ site.site_name }}</div>
                                <div class="relative inline-block mt-0.5"
                                     @mouseenter="tooltipSiteId = site.id"
                                     @mouseleave="tooltipSiteId = null">
                                    <span class="text-xs text-on-surface-variant hover:text-primary cursor-help"><i class="fas fa-info-circle mr-0.5"></i>详情</span>
                                    <div v-if="tooltipSiteId === site.id" class="site-tooltip">
                                        <div class="grid grid-cols-2 gap-x-3 gap-y-1">
                                            <div class="col-span-2">
                                                <span class="text-on-surface-variant">域名:</span>
                                                <a :href="'https://' + site.url" target="_blank" class="text-blue-300 hover:text-blue-200">
                                                    {{ site.url }} <i class="fas fa-external-link-alt text-[10px] ml-1"></i>
                                                </a>
                                            </div>
                                            <div><span class="text-on-surface-variant">类型:</span> {{ site.site_type === 'static' ? '静态站点' : 'WordPress' }}</div>
                                            <div><span class="text-on-surface-variant">标签:</span> {{ site.tag || '-' }}</div>
                                            <template v-if="site.site_type === 'static'">
                                                <div class="col-span-2"><span class="text-on-surface-variant">1Panel:</span> <span :class="site.panel_website_id || site.static_dir ? 'text-green-300' : 'text-on-surface-variant'">{{ site.panel_website_id || site.static_dir ? '已关联' : '未关联' }}</span></div>
                                                <div class="col-span-2"><span class="text-on-surface-variant">DNS:</span> <span :class="site.cf_dns_record_id ? 'text-green-300' : 'text-on-surface-variant'">{{ site.cf_dns_record_id ? 'Cloudflare已配置' : '未配置' }}</span></div>
                                            </template>
                                            <template v-else>
                                                <div><span class="text-on-surface-variant">端口:</span> {{ site.port || '-' }}</div>
                                                <div><span class="text-on-surface-variant">管理员:</span> {{ site.admin_name || '-' }}</div>
                                                <div class="col-span-2"><span class="text-on-surface-variant">1Panel:</span> <span v-if="site.panel_website_id" :class="site.panel_status === 'Running' ? 'text-green-300' : 'text-red-300'">{{ site.panel_status === 'Running' ? '正常' : site.panel_status || '未知' }}</span><span v-else class="text-on-surface-variant">未关联</span></div>
                                                <div class="col-span-2"><span class="text-on-surface-variant">DNS:</span> {{ site.cf_dns_record_id ? 'Cloudflare已配置' : '未配置' }}</div>
                                            </template>
                                        </div>
                                        <div class="absolute top-full left-3 w-0 h-0 border-l-6 border-r-6 border-t-6 border-l-transparent border-r-transparent" style="border-top-color:#1f2937;"></div>
                                    </div>
                                </div>
                            </div>
                            <!-- Timeline -->
                            <div class="flex items-center gap-0 flex-1 ml-2">
                                <!-- ===== STATIC SITE PIPELINE ===== -->
                                <template v-if="site.site_type === 'static'">
                                    <!-- ① DNS -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.dns_resolved ? 'active' : (site.status === 'deploying' ? 'in-progress' : 'inactive')"
                                         :title="pipelineStatuses[site.id]?.dns_resolved ? 'DNS已解析' : 'DNS解析'">
                                        <i class="fas fa-globe"></i>
                                    </div>
                                    <div class="timeline-line" :class="(pipelineStatuses[site.id]?.dns_resolved || site.status === 'deploying') ? 'active' : ''"></div>
                                    <!-- ② 网站 -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.site_created ? 'active' : (site.status === 'deploying' ? 'in-progress' : 'inactive')"
                                         :title="pipelineStatuses[site.id]?.site_created ? '1Panel网站已创建' : '创建1Panel网站'">
                                        <i class="fas fa-server"></i>
                                    </div>
                                    <div class="timeline-line" :class="pipelineStatuses[site.id]?.design_started ? 'active' : (pipelineStatuses[site.id]?.site_created ? 'in-progress' : '')"></div>
                                    <!-- ③ 设计 -->
                                    <div class="timeline-icon"
                                         :class="pipelineStatuses[site.id]?.design_complete ? 'active' :
                                            pipelineStatuses[site.id]?.design_generating ? 'in-progress' :
                                            pipelineStatuses[site.id]?.design_started ? 'in-progress' : 'inactive'"
                                         :title="stitchProgressTitle(site)">
                                        <i class="fas fa-paint-brush"></i>
                                    </div>
                                    <div class="timeline-line" :class="pipelineStatuses[site.id]?.files_uploaded ? 'active' : (pipelineStatuses[site.id]?.design_complete ? 'in-progress' : '')"></div>
                                    <!-- ④ 上线 -->
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.files_uploaded ? 'active' : (site.status === 'deploying' && pipelineStatuses[site.id]?.design_complete ? 'in-progress' : 'inactive')"
                                         :title="pipelineStatuses[site.id]?.files_uploaded ? '站点已上线' : (pipelineStatuses[site.id]?.design_complete ? '上传文件中...' : '等待设计完成')">
                                        <i class="fas fa-check-circle"></i>
                                    </div>
                                </template>
                                <!-- WordPress site timeline (legacy) -->
                                <template v-else>
                                    <div class="timeline-icon" :class="pipelineStatuses[site.id]?.wp_deployed ? 'active' : (wpInstallStatuses[site.id] && wpInstallStatuses[site.id].status === 'installing' ? 'in-progress' : 'inactive')">
                                        <span class="material-symbols-outlined">language</span>
                                    </div>
                                    <div class="timeline-line" :class="pipelineLineState(site, 'demo')"></div>
                                    <div class="timeline-icon"
                                         :class="[
                                            pipelineStatuses[site.id]?.demo_imported ? 'active' :
                                            pipelineStatuses[site.id]?.demo_importing ? 'in-progress' :
                                            (pipelineStatuses[site.id]?.wp_deployed && !pipelineStatuses[site.id]?.demo_imported) ? 'in-progress clickable' : 'inactive',
                                            (pipelineStatuses[site.id]?.wp_deployed && !pipelineStatuses[site.id]?.demo_importing) ? 'clickable' : ''
                                         ]"
                                         :title="pipelineStatuses[site.id]?.demo_imported ? '演示已导入: ' + (pipelineStatuses[site.id]?.demo_name || '') : pipelineStatuses[site.id]?.demo_importing ? '演示导入中...' : '点击导入主题演示'"
                                         @click="pipelineStatuses[site.id]?.wp_deployed && !pipelineStatuses[site.id]?.demo_importing && openDemoImportForSite(site)">
                                        <i class="fas fa-paint-brush"></i>
                                    </div>
                                    <div class="timeline-line" :class="pipelineLineState(site, 'kit')"></div>
                                    <div class="timeline-icon"
                                         :class="pipelineStatuses[site.id]?.brand_configured ? 'active' :
                                            (pipelineStatuses[site.id]?.demo_imported && !pipelineStatuses[site.id]?.brand_configured) ? 'in-progress' : 'inactive'"
                                         :title="pipelineStatuses[site.id]?.brand_configured ? '品牌配置已完成' : '品牌配置'">
                                        <i class="fas fa-cube"></i>
                                    </div>
                                    <div class="timeline-line" :class="pipelineLineState(site, 'gmc')"></div>
                                    <div class="timeline-icon"
                                         :class="pipelineStatuses[site.id]?.gmc_registered ? 'active' : 'inactive'"
                                         :title="pipelineStatuses[site.id]?.gmc_registered ? 'GMC已注册: ' + (site.google_mc_account_id || '') : 'GMC注册'">
                                        <i class="fab fa-google"></i>
                                    </div>
                                </template>
                                <!-- Status text -->
                                <span class="text-xs text-on-surface-variant ml-3 whitespace-nowrap">{{ siteStatusText(site) }}</span>
                            </div>
                            <!-- Created time -->
                            <div class="flex-shrink-0 w-24 text-right text-xs text-on-surface-variant">
                                <span v-if="site.created_at" :title="site.created_at">{{ site.created_at.split('T')[0] }}</span>
                                <span v-else>-</span>
                            </div>
                            <!-- Actions -->
                            <div class="flex items-center gap-1.5 flex-shrink-0">
                                <button v-if="site.cloakbrowser_profile_name" @click="openSiteBrowser(site)" class="text-green-600 hover:text-green-700 p-1.5" title="打开指纹浏览器"><i class="fas fa-external-link-alt"></i></button>
                                <button @click="openEditModal(site)" class="text-blue-600 hover:text-blue-700 p-1.5" title="编辑"><i class="fas fa-edit"></i></button>
                                <button v-if="site.panel_app_install_id && (!site.panel_website_id || site.panel_status === 'deleted')" @click="fixSiteWebsite(site)" class="text-orange-500 hover:text-orange-600 p-1.5" title="修复1Panel网站"><i class="fas fa-wrench"></i></button>
                                <button @click="openMetaModal(site)" class="text-purple-500 hover:text-purple-600 p-1.5" title="注入Meta标签"><i class="fas fa-code"></i></button>
                                <button @click="confirmDelete(site)" class="text-red-500 hover:text-red-600 p-1.5" title="删除"><i class="fas fa-trash"></i></button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Meta Tag Injection Modal -->
            <div v-if="metaModal.show" class="modal-overlay modal-overlay" @click.self="metaModal.show = false">
                <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-lg mx-4 p-6 fade-in">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-lg font-bold"><i class="fas fa-code mr-2 text-primary"></i>注入 Meta 标签</h3>
                        <button @click="metaModal.show = false" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button>
                    </div>
                    <p class="text-sm text-on-surface-variant mb-4">{{ metaModal.site?.site_name || '' }} · 输入完整的 meta 标签代码，将注入到站点 header 中</p>
                    <div class="mb-4">
                        <textarea v-model="metaModal.metaTag" rows="3"
                            class="w-full border rounded-lg p-3 text-sm font-mono focus:border-primary focus:ring-1 focus:ring-blue-500"
                            placeholder='<meta name="xxx" content="yyy" />'></textarea>
                    </div>
                    <div class="flex gap-3">
                        <button @click="metaModal.show = false" class="flex-1 py-2.5 border rounded-lg text-on-surface-variant hover:bg-surface-container-low">取消</button>
                        <button @click="submitMetaTag" :disabled="!metaModal.metaTag.trim() || metaModal.submitting"
                            class="btn btn-primary flex-1 transition disabled:opacity-50">
                            <i v-if="metaModal.submitting" class="fas fa-spinner fa-spin mr-2"></i>
                            {{ metaModal.submitting ? '注入中...' : '确认注入' }}
                        </button>
                    </div>
                </div>
            </div>

            <!-- Environment Selection Modal (operator login) -->
            <div v-if="envSelectModal.show" class="modal-overlay modal-overlay">
                <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-lg mx-4 p-6 fade-in">
                    <h3 class="text-lg font-bold mb-2"><span class="material-symbols-outlined">dns</span>选择 1Panel 环境</h3>
                    <p class="text-sm text-on-surface-variant mb-4">请选择你管理的 1Panel 服务器环境</p>

                    <div v-if="envSelectModal.loading" class="text-center py-12 text-on-surface-variant">
                        <span class="spinner w-4 h-4 inline-block"></span><p>加载环境列表...</p>
                    </div>
                    <div v-else-if="envSelectModal.environments.length === 0" class="text-center py-12 text-on-surface-variant">
                        <i class="fas fa-exclamation-triangle text-3xl mb-3"></i><p>暂无可用的 1Panel 环境，请联系管理员在系统设置中配置</p>
                    </div>
                    <div v-else>
                        <div class="space-y-2 max-h-80 overflow-y-auto mb-4">
                            <div v-for="env in envSelectModal.environments" :key="env.id"
                                 @click="envSelectModal.selectedEnvId = env.id"
                                 :class="['p-4 rounded-xl border-2 cursor-pointer transition',
                                     envSelectModal.selectedEnvId === env.id
                                         ? 'border-primary-container bg-blue-50'
                                         : 'border-outline-variant hover:border-outline']">
                                <div class="flex items-center justify-between">
                                    <div>
                                        <div class="font-medium text-on-surface">{{ env.name }}</div>
                                        <div class="text-sm text-on-surface-variant mt-1">{{ env.host }}:{{ env.port }}</div>
                                    </div>
                                    <div v-if="envSelectModal.selectedEnvId === env.id" class="text-primary">
                                        <i class="fas fa-check-circle text-xl"></i>
                                    </div>
                                </div>
                                <div v-if="env.is_default" class="mt-2"><span class="text-xs bg-[#146c2e]/10 text-[#146c2e] px-2 py-0.5 rounded-full">默认</span></div>
                            </div>
                        </div>
                        <button @click="submitEnvSelection"
                            :disabled="!envSelectModal.selectedEnvId || envSelectModal.submitting"
                            class="btn btn-primary w-full transition disabled:opacity-50">
                            <i v-if="envSelectModal.submitting" class="fas fa-spinner fa-spin mr-2"></i>
                            {{ envSelectModal.submitting ? '设置中...' : '确认选择' }}
                        </button>
                    </div>
                </div>
            </div>

            <!-- Demo Import Modal (triggered from timeline icon) -->
            <div v-if="demoModal.show" class="modal-overlay modal-overlay" @click.self="demoModal.show = false">
                <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-3xl mx-4 p-6 fade-in max-h-[85vh] overflow-y-auto">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-lg font-bold"><i class="fas fa-paint-brush mr-2 text-pink-500"></i>导入主题演示</h3>
                        <button @click="demoModal.show = false" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button>
                    </div>
                    <p class="text-sm text-on-surface-variant mb-3">{{ demoModal.site?.site_name || '' }} · 选择一个 WoodMart 演示模板导入</p>

                    <div v-if="demoModal.loading" class="text-center py-12 text-on-surface-variant">
                        <span class="spinner w-4 h-4 inline-block"></span><p>加载演示列表...</p>
                    </div>
                    <div v-else-if="demoModal.demos.length === 0" class="text-center py-12 text-on-surface-variant">
                        <p>暂无可用的演示模板，请确认 WoodMart 主题已激活</p>
                    </div>
                    <div v-else>
                        <!-- Category filter -->
                        <div class="flex flex-wrap gap-1.5 mb-3">
                            <button @click="demoModal.category = 'all'"
                                :class="['px-2.5 py-1 rounded-full text-xs font-medium transition',
                                    demoModal.category === 'all' ? 'bg-primary text-on-primary' : 'bg-surface-container text-on-surface-variant hover:bg-surface-container-high']">
                                全部
                            </button>
                            <button v-for="cat in demoModalCategories" :key="cat"
                                @click="demoModal.category = cat"
                                :class="['px-2.5 py-1 rounded-full text-xs font-medium transition',
                                    demoModal.category === cat ? 'bg-primary text-on-primary' : 'bg-surface-container text-on-surface-variant hover:bg-surface-container-high']">
                                {{ cat }}
                            </button>
                        </div>
                        <!-- Demo cards -->
                        <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 max-h-64 overflow-y-auto mb-4">
                            <div v-for="demo in demoModalFiltered" :key="demo.id"
                                @click="demoModal.selectedDemoId = demo.id"
                                :class="['cursor-pointer border-2 rounded-xl p-3 text-center transition',
                                    demoModal.selectedDemoId === demo.id ? 'border-primary-container bg-blue-50' : 'border-outline-variant hover:border-outline']">
                                <img v-if="demo.thumbnail" :src="demo.thumbnail" :alt="demo.name" class="w-full h-20 object-cover rounded-lg mb-2" />
                                <p class="text-xs font-medium text-on-surface truncate">{{ demo.name }}</p>
                            </div>
                        </div>
                        <!-- Import button -->
                        <div class="flex items-center gap-3">
                            <button @click="startDemoImportFromModal" :disabled="!demoModal.selectedDemoId || demoModal.importing"
                                class="btn-primary text-on-primary px-6 py-2.5 rounded-lg disabled:opacity-50">
                                <i v-if="demoModal.importing" class="fas fa-spinner fa-spin mr-2"></i>
                                {{ demoModal.importing ? '导入中...' : '导入演示' }}
                            </button>
                            <button @click="demoModal.show = false" class="px-4 py-2.5 border rounded-lg text-sm hover:bg-surface-container-low">
                                取消
                            </button>
                        </div>
                        <p v-if="demoModal.status" class="text-xs mt-2" :class="demoModal.status.includes('失败') ? 'text-error' : 'text-on-surface-variant'">
                            {{ demoModal.status }}
                        </p>
                    </div>
                </div>
            </div>

            <!-- 筛品 Dashboard -->
            <div v-if="currentPage === 'shai-pin-dashboard'" class="fade-in">
                <div v-if="feedStatsLoading" class="flex items-center justify-center py-20">
                    <span class="spinner w-4 h-4 inline-block"></span>
                    <span class="ml-3 text-on-surface-variant">加载数据统计中...</span>
                </div>

                <div v-else-if="feedStats">
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-6 mb-8">
                        <div class="card">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="font-body-md text-on-surface-variant font-medium">商品总数</p>
                                    <p class="font-display-md mt-xs text-on-surface">{{ feedStats.total_products }}</p>
                                </div>
                                <div class="w-12 h-12 bg-[#146c2e]/10 rounded-lg flex items-center justify-center">
                                    <i class="fas fa-boxes text-[#146c2e] text-xl"></i>
                                </div>
                            </div>
                        </div>
                        <div class="card">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="font-body-md text-on-surface-variant font-medium">涉及站点</p>
                                    <p class="font-display-md mt-xs text-on-surface">{{ feedStats.sites_with_products }}</p>
                                </div>
                                <div class="p-xs bg-surface-container-low rounded-md">
                                    <span class="material-symbols-outlined">language</span>
                                </div>
                            </div>
                        </div>
                        <div class="card">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="font-body-md text-on-surface-variant font-medium">有货</p>
                                    <p class="text-3xl font-bold text-[#146c2e] mt-1">{{ feedStats.in_stock }}</p>
                                </div>
                                <div class="w-12 h-12 bg-[#146c2e]/10 rounded-lg flex items-center justify-center">
                                    <i class="fas fa-check-circle text-[#146c2e] text-xl"></i>
                                </div>
                            </div>
                        </div>
                        <div class="card">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="font-body-md text-on-surface-variant font-medium">缺货</p>
                                    <p class="text-3xl font-bold text-error mt-1">{{ feedStats.out_of_stock }}</p>
                                </div>
                                <div class="w-12 h-12 bg-error-container rounded-lg flex items-center justify-center">
                                    <span class="material-symbols-outlined">close</span>
                                </div>
                            </div>
                        </div>
                        <div class="card">
                            <div class="flex items-center justify-between">
                                <div>
                                    <p class="font-body-md text-on-surface-variant font-medium">预定</p>
                                    <p class="text-3xl font-bold text-yellow-600 mt-1">{{ feedStats.preorder }}</p>
                                </div>
                                <div class="w-12 h-12 bg-yellow-100 rounded-lg flex items-center justify-center">
                                    <i class="fas fa-clock text-yellow-600 text-xl"></i>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-4">
                            <i class="fas fa-coins mr-2 text-primary"></i>币种分布
                        </h3>
                        <div v-if="feedStats.currencies && feedStats.currencies.length" class="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div v-for="c in feedStats.currencies" :key="c.currency"
                                 class="bg-surface-container-low rounded-lg p-4 text-center">
                                <p class="text-2xl font-bold text-on-surface">{{ c.count }}</p>
                                <p class="text-sm text-on-surface-variant mt-1">{{ c.currency }}</p>
                            </div>
                        </div>
                        <div v-else class="text-center text-on-surface-variant py-8">
                            <i class="fas fa-inbox text-3xl mb-2"></i>
                            <p>暂无币种数据</p>
                        </div>
                    </div>
                </div>

                <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center text-on-surface-variant">
                    <i class="fas fa-chart-bar text-5xl mb-4"></i>
                    <p class="text-lg font-medium text-on-surface-variant mb-2">暂无筛品数据</p>
                    <p>请先在站点中添加 Feed 商品数据</p>
                </div>
            </div>

            <!-- 筛品 - 商品来源 -->
            <div v-if="currentPage === 'shai-pin-source'" class="fade-in">
                <div class="flex gap-2 mb-6">
                    <button @click="sourceTab = 'walmart'"
                            :class="['px-6 py-3 rounded-lg text-sm font-medium transition',
                                     sourceTab === 'walmart' ? 'bg-primary-container text-on-primary shadow' : 'bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container shadow-level-1']">
                        <i class="fas fa-shopping-cart mr-2"></i>沃尔玛商超
                    </button>
                    <button @click="sourceTab = 'amazon'"
                            :class="['px-6 py-3 rounded-lg text-sm font-medium transition',
                                     sourceTab === 'amazon' ? 'bg-tertiary-container text-on-primary shadow' : 'bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container shadow-level-1']">
                        <i class="fab fa-amazon mr-2"></i>亚马逊网品
                    </button>
                    <button @click="sourceTab = 'tiktok'"
                            :class="['px-6 py-3 rounded-lg text-sm font-medium transition',
                                     sourceTab === 'tiktok' ? 'bg-gray-800 text-on-primary shadow' : 'bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container shadow-level-1']">
                        <i class="fab fa-tiktok mr-2"></i>tiktok爆款
                    </button>
                    <button @click="sourceTab = 'hot-import'; loadPersistedAmazonResults()"
                            :class="['px-6 py-3 rounded-lg text-sm font-medium transition',
                                     sourceTab === 'hot-import' ? 'bg-error text-on-primary shadow' : 'bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container shadow-level-1']">
                        <i class="fas fa-rocket mr-2"></i>爆品导入
                    </button>
                </div>

                <!-- Walmart Tab Content -->
                <div v-if="sourceTab === 'walmart'" class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                    <div class="p-6 border-b">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="font-semibold text-on-surface">
                                <i class="fas fa-shopping-cart mr-2 text-primary"></i>沃尔玛商超 · 热销商品
                            </h3>
                            <div class="flex items-center gap-3">
                                <button v-if="walmartProducts && walmartProducts.length" @click="exportWalmartData('excel')"
                                    class="px-3 py-2 bg-[#146c2e] text-on-primary rounded-lg text-sm hover:bg-[#146c2e]/80 transition">
                                    <i class="fas fa-file-excel mr-1"></i>导出Excel
                                </button>
                                <button v-if="walmartProducts && walmartProducts.length" @click="exportWalmartData('json')"
                                    class="px-3 py-2 bg-surface-container-highest text-on-primary rounded-lg text-sm hover:bg-surface-container-high transition">
                                    <i class="fas fa-code mr-1"></i>导出JSON
                                </button>
                            </div>
                        </div>
                        <div class="flex items-center gap-4 flex-wrap">
                            <select v-model="walmartSelectedCategory" @change="loadPersistedWalmartProducts()" class="px-4 py-2 border rounded-lg text-sm focus:border-primary min-w-[240px]">
                                <option value="">-- 选择大类 --</option>
                                <optgroup v-for="g in walmartCategories" :key="g.group" :label="g.group">
                                    <option v-for="c in g.items" :key="c.key" :value="c.key">
                                        {{ c.label }}{{ c.cached_count ? ' (' + c.cached_count + ' 件)' : '' }}
                                    </option>
                                </optgroup>
                            </select>
                            <input v-model.number="walmartFetchLimit" type="number" min="0"
                                placeholder="全部" title="留空或0=获取全部，填数字=限制条数"
                                class="px-3 py-2 border rounded-lg text-sm w-20 focus:border-primary">
                            <button @click="fetchWalmartBestsellers" :disabled="walmartLoading || !walmartSelectedCategory"
                                class="px-6 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50 transition">
                                <i :class="['fas mr-1', walmartLoading ? 'fa-spinner fa-spin' : 'fa-download']"></i>
                                {{ walmartLoading ? '抓取中...' : '抓取热销榜' }}
                            </button>
                            <button v-if="walmartProducts && walmartProducts.length" @click="enrichWalmartProducts"
                                :disabled="walmartEnriching"
                                class="px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50 transition">
                                <i :class="['fas mr-1', walmartEnriching ? 'fa-spinner fa-spin' : 'fa-database']"></i>
                                {{ walmartEnriching ? '处理中...' : '数据异步' }}
                            </button>
                        </div>
                        <div v-if="walmartEnrichProgress" class="mt-2 text-xs text-primary font-medium">
                            <span class="material-symbols-outlined text-[10px]">circle</span>{{ walmartEnrichProgress }}
                        </div>
                        <p class="text-xs text-on-surface-variant mt-3">
                            通过 Crawlbase 实时抓取 Walmart.com 美国站五大商超品类的 Best Sellers 榜单数据。
                            初次加载请等待 10-30 秒。
                        </p>
                    </div>

                    <!-- Error -->
                    <div v-if="walmartError" class="p-8 text-center">
                        <div class="bg-error-container border border-error/20 rounded-lg p-4 inline-block">
                            <i class="fas fa-exclamation-triangle text-red-400 mr-2"></i>
                            <span class="text-error">{{ walmartError }}</span>
                        </div>
                    </div>

                    <!-- Loading skeletons -->
                    <div v-if="walmartLoading" class="p-8">
                        <div class="space-y-3 animate-pulse">
                            <div v-for="i in 5" :key="i" class="flex items-center gap-4 p-3 bg-surface-container-low rounded-lg">
                                <div class="w-8 h-8 bg-surface-container-high rounded-full"></div>
                                <div class="flex-1 h-4 bg-surface-container-high rounded w-3/4"></div>
                                <div class="w-20 h-4 bg-surface-container-high rounded"></div>
                                <div class="w-16 h-4 bg-surface-container-high rounded"></div>
                            </div>
                        </div>
                    </div>

                    <!-- Empty state -->
                    <div v-if="!walmartLoading && !walmartError && !(walmartProducts && walmartProducts.length)" class="p-12 text-center text-on-surface-variant">
                        <div class="w-16 h-16 bg-blue-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                            <i class="fas fa-shopping-cart text-primary text-2xl"></i>
                        </div>
                        <p>选择大类后点击「抓取热销榜」开始获取数据</p>
                    </div>

                    <!-- Results table -->
                    <div v-if="walmartProducts && walmartProducts.length" class="overflow-x-auto">
                        <div class="px-6 py-3 bg-surface-container-low border-b flex items-center justify-between text-xs text-on-surface-variant">
                            <span>共 {{ (walmartProducts && walmartProducts.length) || 0 }} 件</span>
                            <span>第 {{ walmartPage || 1 }} / {{ walmartTotalPages || 1 }} 页</span>
                            <div class="flex items-center gap-1">
                                <button @click="walmartGoPage((walmartPage || 1) - 1)" :disabled="(walmartPage || 1) <= 1"
                                    class="px-3 py-1 rounded hover:bg-surface-container-high disabled:opacity-30 transition">上一页</button>
                                <button @click="walmartGoPage((walmartPage || 1) + 1)" :disabled="(walmartPage || 1) >= (walmartTotalPages || 1)"
                                    class="px-3 py-1 rounded hover:bg-surface-container-high disabled:opacity-30 transition">下一页</button>
                            </div>
                        </div>
                        <table class="w-full text-sm">
                            <thead class="bg-surface-container-low text-left text-xs text-on-surface-variant uppercase">
                                <tr>
                                    <th class="px-4 py-3 w-12">排名</th>
                                    <th class="px-4 py-3">产品名称</th>
                                    <th class="px-4 py-3 w-24">价格</th>
                                    <th class="px-4 py-3 w-20">评论数</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y">
                                <tr v-for="(p, idx) in walmartPagedProducts" :key="idx" class="hover:bg-surface-container-low transition">
                                    <td class="px-4 py-3">
                                        <span :class="['inline-flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold',
                                            (p && p.rank <= 3) ? 'bg-yellow-100 text-yellow-800' : 'bg-surface-container text-on-surface-variant']">
                                            {{ p && p.rank }}
                                        </span>
                                    </td>
                                    <td class="px-4 py-3">
                                        <div class="max-w-md">
                                            <a v-if="p && p.source_url" :href="p.source_url" target="_blank"
                                                class="font-medium text-primary hover:text-primary hover:underline line-clamp-2">
                                                {{ p && p.product_name }}
                                            </a>
                                            <span v-else class="font-medium text-on-surface line-clamp-2">{{ p && p.product_name }}</span>
                                        </div>
                                    </td>
                                    <td class="px-4 py-3 font-medium text-on-surface">{{ p && p.price != null ? '$' + p.price.toFixed(2) : '-' }}</td>
                                    <td class="px-4 py-3 text-on-surface-variant">{{ p && p.review_count != null ? p.review_count.toLocaleString() : '-' }}</td>
                                </tr>
                            </tbody>
                        </table>
                        <div class="px-6 py-3 bg-surface-container-low border-t flex items-center justify-between text-xs text-on-surface-variant">
                            <span>第 {{ walmartPage || 1 }} 页，每页 {{ walmartPerPage || 20 }} 件，共 {{ (walmartProducts && walmartProducts.length) || 0 }} 件</span>
                            <div class="flex items-center gap-1">
                                <button @click="walmartGoPage((walmartPage || 1) - 1)" :disabled="(walmartPage || 1) <= 1"
                                    class="px-3 py-1 rounded hover:bg-surface-container-high disabled:opacity-30 transition">上一页</button>
                                <button @click="walmartGoPage((walmartPage || 1) + 1)" :disabled="(walmartPage || 1) >= (walmartTotalPages || 1)"
                                    class="px-3 py-1 rounded hover:bg-surface-container-high disabled:opacity-30 transition">下一页</button>
                            </div>
                        </div>
                    </div>
                </div>

                <div v-if="sourceTab === 'amazon'" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center">
                    <div class="w-20 h-20 bg-orange-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <i class="fab fa-amazon text-tertiary text-3xl"></i>
                    </div>
                    <h3 class="text-lg font-semibold text-on-surface mb-2">亚马逊网品商品来源</h3>
                    <p class="text-on-surface-variant">此功能正在开发中，敬请期待...</p>
                </div>

                <div v-if="sourceTab === 'tiktok'" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center">
                    <div class="w-20 h-20 bg-gray-800 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <i class="fab fa-tiktok text-on-primary text-3xl"></i>
                    </div>
                    <h3 class="text-lg font-semibold text-on-surface mb-2">tiktok爆款商品来源</h3>
                    <p class="text-on-surface-variant">此功能正在开发中，敬请期待...</p>
                </div>

                <!-- 爆品导入 Tab Content -->
                <div v-if="sourceTab === 'hot-import'" class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                    <div class="p-6 border-b">
                        <h3 class="font-semibold text-on-surface mb-4">
                            <i class="fas fa-rocket mr-2 text-error"></i>爆品导入 · 亚马逊产品搜索
                        </h3>
                        <div class="flex items-center gap-3">
                            <button @click="openAmazonImportModal" :disabled="amazonSearchLoading"
                                class="px-5 py-2.5 bg-error text-on-primary rounded-lg text-sm font-medium hover:bg-error disabled:opacity-50 transition">
                                <i class="fas fa-keyboard mr-1"></i>输入搜索
                            </button>
                            <label class="px-5 py-2.5 bg-primary-container text-on-primary rounded-lg text-sm font-medium hover:bg-primary transition cursor-pointer">
                                <i class="fas fa-file-upload mr-1"></i>导入文件
                                <input type="file" accept=".txt,.csv" class="hidden" @change="handleAmazonFileUpload" :disabled="amazonSearchLoading">
                            </label>
                            <button @click="openAmazonUrlModal" :disabled="amazonSearchLoading"
                                class="px-5 py-2.5 bg-[#146c2e] text-on-primary rounded-lg text-sm font-medium hover:bg-[#146c2e]/80 disabled:opacity-50 transition">
                                <i class="fas fa-link mr-1"></i>粘贴链接
                            </button>
                        </div>
                        <p class="text-xs text-on-surface-variant mt-3">
                            搜索关键词 或 粘贴 Amazon 产品链接直接导入。支持 .txt（每行一个产品名）或 .csv 文件导入。
                        </p>
                    </div>

                    <!-- Progress / Searching -->
                    <div v-if="amazonSearchProgress" class="px-6 py-2 bg-error-container border-b text-sm">
                        <span v-if="amazonSearchLoading" class="text-error"><span class="material-symbols-outlined text-[10px]">circle</span></span>
                        <span v-else class="text-[#146c2e]"><i class="fas fa-check-circle mr-1"></i></span>
                        <span :class="amazonSearchLoading ? 'text-error' : 'text-[#146c2e]'">{{ amazonSearchProgress }}</span>
                    </div>

                    <!-- Error -->
                    <div v-if="amazonSearchError && !amazonSearchResults.length" class="p-8 text-center">
                        <div class="bg-error-container border border-error/20 rounded-lg p-4 inline-block">
                            <i class="fas fa-exclamation-triangle text-red-400 mr-2"></i>
                            <span class="text-error">{{ amazonSearchError }}</span>
                        </div>
                    </div>

                    <!-- Loading -->
                    <div v-if="amazonSearchLoading && !amazonSearchResults.length" class="p-8">
                        <div class="space-y-3 animate-pulse">
                            <div v-for="i in 5" :key="i" class="flex items-center gap-4 p-3 bg-surface-container-low rounded-lg">
                                <div class="w-8 h-8 bg-surface-container-high rounded-full"></div>
                                <div class="flex-1 h-4 bg-surface-container-high rounded w-3/4"></div>
                                <div class="w-20 h-4 bg-surface-container-high rounded"></div>
                            </div>
                        </div>
                    </div>

                    <!-- Empty -->
                    <div v-if="!amazonSearchLoading && !amazonSearchError && !amazonSearchResults.length && !amazonSearchProgress" class="p-12 text-center text-on-surface-variant">
                        <div class="w-16 h-16 bg-error-container rounded-2xl flex items-center justify-center mx-auto mb-4">
                            <i class="fas fa-rocket text-error text-2xl"></i>
                        </div>
                        <p>点击「输入搜索」或「导入文件」开始</p>
                    </div>

                    <!-- Import Modal -->
                    <div v-if="showAmazonImportModal" class="modal-overlay modal-overlay" @click.self="closeAmazonImportModal">
                        <div class="bg-surface-container-lowest rounded-xl shadow-level-1 w-full max-w-lg p-6 fade-in">
                            <h3 class="text-lg font-semibold text-on-surface mb-4">
                                <i class="fas fa-keyboard mr-2 text-error"></i>输入产品名称
                            </h3>
                            <textarea v-model="amazonImportModalText" placeholder="每行一个产品名称&#10;例如：&#10;running shoes&#10;wireless earbuds&#10;yoga mat"
                                class="w-full px-4 py-3 border rounded-lg text-sm focus:border-red-500 min-h-[200px] resize-y"></textarea>
                            <div class="flex justify-end gap-3 mt-4">
                                <button @click="closeAmazonImportModal" class="px-4 py-2 border rounded-lg text-sm hover:bg-surface-container-low">取消</button>
                                <button @click="startAmazonSearchFromModal" class="px-6 py-2 bg-error text-on-primary rounded-lg text-sm font-medium hover:bg-error transition">
                                    <i class="fas fa-search mr-1"></i>开始搜索
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- URL Import Modal -->
                    <div v-if="showAmazonUrlModal" class="modal-overlay modal-overlay" @click.self="closeAmazonUrlModal">
                        <div class="bg-surface-container-lowest rounded-xl shadow-level-1 w-full max-w-lg p-6 fade-in">
                            <h3 class="text-lg font-semibold text-on-surface mb-4">
                                <i class="fas fa-link mr-2 text-[#146c2e]"></i>粘贴 Amazon 产品链接
                            </h3>
                            <textarea v-model="amazonUrlModalText" placeholder="每行一个 Amazon 产品链接&#10;例如：&#10;https://www.amazon.com/dp/B0BDMTZYXZ&#10;https://www.amazon.com/dp/B0C3VJDKM8"
                                class="w-full px-4 py-3 border rounded-lg text-sm focus:border-green-500 min-h-[200px] resize-y"></textarea>
                            <div class="flex justify-end gap-3 mt-4">
                                <button @click="closeAmazonUrlModal" class="px-4 py-2 border rounded-lg text-sm hover:bg-surface-container-low">取消</button>
                                <button @click="startAmazonUrlImport" class="px-6 py-2 bg-[#146c2e] text-on-primary rounded-lg text-sm font-medium hover:bg-[#146c2e]/80 transition">
                                    <i class="fas fa-download mr-1"></i>直接导入
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Results -->
                    <div v-if="amazonSearchResults.length">
                        <div class="px-6 py-3 bg-surface-container-low border-b flex items-center justify-between text-xs text-on-surface-variant">
                            <span>共 {{ amazonSearchResults.length }} 件产品</span>
                            <div class="flex items-center gap-3">
                                <label class="flex items-center gap-1 cursor-pointer hover:text-on-surface">
                                    <input type="checkbox" :checked="amazonSelectedIndices.size === amazonSearchResults.length" @change="selectAllAmazon" class="accent-red-500">
                                    全选
                                </label>
                                <button @click="convertToWooCommerce" :disabled="!amazonSelectedIndices.size || amazonSearchLoading || wooConverting"
                                    class="px-4 py-1.5 bg-primary-container text-on-primary rounded text-xs font-medium hover:bg-primary disabled:opacity-50 transition">
                                    <i class="fas fa-shopping-cart mr-1"></i>生成 网站产品 ({{ amazonSelectedIndices.size }})
                                </button>
                                <button @click="deleteSelectedAmazonProducts" :disabled="!amazonSelectedIndices.size || amazonSearchLoading"
                                    class="px-4 py-1.5 bg-error text-on-primary rounded text-xs font-medium hover:bg-error disabled:opacity-50 transition">
                                    <i class="fas fa-trash mr-1"></i>删除 ({{ amazonSelectedIndices.size }})
                                </button>
                            </div>
                        </div>
                        <div class="overflow-x-auto">
                            <table class="w-full text-sm">
                                <thead class="bg-surface-container-low text-left text-xs text-on-surface-variant uppercase">
                                    <tr>
                                        <th class="px-3 py-3 w-10"></th>
                                        <th class="px-3 py-3 w-14">图片</th>
                                        <th class="px-3 py-3">产品信息</th>
                                        <th class="px-3 py-3 w-20">价格</th>
                                        <th class="px-3 py-3 w-16">Prime</th>
                                        <th class="px-3 py-3 w-16">评分</th>
                                        <th class="px-3 py-3 w-20">评论数</th>
                                    </tr>
                                </thead>
                                <tbody class="divide-y">
                                    <tr v-for="(p, idx) in amazonSearchResults" :key="idx"
                                        :class="['hover:bg-surface-container-low transition cursor-pointer', amazonSelectedIndices.has(idx) ? 'bg-error-container' : '']"
                                        @click="toggleAmazonSelect(idx)">
                                        <td class="px-3 py-3">
                                            <input type="checkbox" :checked="amazonSelectedIndices.has(idx)" class="accent-red-500 pointer-events-none">
                                        </td>
                                        <td class="px-3 py-3">
                                            <img v-if="p.thumbnail" :src="p.thumbnail" class="w-12 h-12 rounded border object-cover" :alt="p.product_name" loading="lazy">
                                            <div v-else class="w-12 h-12 bg-surface-container rounded border flex items-center justify-center"><i class="fas fa-image text-on-surface-variant text-xs"></i></div>
                                        </td>
                                        <td class="px-3 py-2.5">
                                            <a v-if="p.source_url" :href="p.source_url" target="_blank" @click.stop
                                                class="font-medium text-primary hover:text-primary hover:underline block max-w-[280px] truncate">
                                                {{ p.product_name }}
                                            </a>
                                            <span v-else class="font-medium text-on-surface block max-w-[280px] truncate">{{ p.product_name }}</span>
                                            <div class="flex items-center gap-1.5 mt-1 flex-wrap">
                                                <span v-if="p.brand" class="inline-flex items-center px-1.5 py-0.5 bg-amber-50 text-amber-700 rounded text-xs font-medium border border-amber-200">
                                                    <i class="fas fa-tag mr-0.5 text-xs"></i>{{ p.brand }}
                                                </span>
                                                <span v-if="p.asin" class="inline-flex items-center text-xs text-on-surface-variant font-mono">{{ p.asin }}</span>
                                                <span v-if="p.breadcrumbs" class="text-xs text-on-surface-variant truncate max-w-[200px]" :title="p.breadcrumbs">{{ p.breadcrumbs }}</span>
                                            </div>
                                        </td>
                                        <td class="px-3 py-3">
                                            <div v-if="p.original_price && p.original_price !== p.price" class="flex flex-col">
                                                <span class="text-xs text-on-surface-variant line-through leading-tight">{{ p.original_price }}</span>
                                                <span class="font-semibold text-on-surface">{{ p.price || '-' }}</span>
                                            </div>
                                            <span v-else class="font-semibold text-on-surface">{{ p.price || '-' }}</span>
                                        </td>
                                        <td class="px-3 py-3">
                                            <span v-if="p.is_prime" class="inline-flex items-center px-1.5 py-0.5 bg-blue-50 text-primary rounded text-xs font-medium">
                                                <i class="fas fa-check-circle mr-0.5"></i>Prime
                                            </span>
                                            <span v-else class="text-xs text-on-surface-variant">-</span>
                                        </td>
                                        <td class="px-3 py-3 text-on-surface">
                                            <span v-if="p.rating_score" class="inline-flex items-center gap-1">
                                                <i class="fas fa-star text-amber-400 text-xs"></i>
                                                {{ p.rating_score.toFixed(1) }}
                                            </span>
                                            <span v-else class="text-xs text-on-surface-variant">-</span>
                                        </td>
                                        <td class="px-3 py-3 text-on-surface-variant">{{ p.review_count ? p.review_count.toLocaleString() : '-' }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 转换日志弹窗 -->
            <div v-if="showConvertLogModal" class="modal-overlay modal-overlay" @click.self="closeConvertLogModal">
                <div class="bg-surface-container-lowest rounded-xl shadow-level-1 w-full max-w-2xl max-h-[80vh] flex flex-col fade-in">
                    <div class="flex items-center justify-between p-5 border-b">
                        <h3 class="text-lg font-semibold text-on-surface"><span class="material-symbols-outlined">sync</span>转换日志</h3>
                        <button @click="closeConvertLogModal" :disabled="converting || wooConverting" class="text-on-surface-variant hover:text-on-surface-variant disabled:opacity-30"><span class="material-symbols-outlined">close</span></button>
                    </div>
                    <div class="p-5 overflow-y-auto flex-1">
                        <div class="mb-4">
                            <div class="flex items-center justify-between mb-2">
                                <span class="text-sm text-on-surface-variant">{{ convertLogProgress }}</span>
                                <span v-if="converting" class="text-xs text-[#146c2e]"><span class="spinner w-4 h-4 inline-block"></span>运行中</span>
                            </div>
                            <div v-if="converting" class="w-full bg-surface-container-high rounded-full h-2">
                                <div class="bg-[#146c2e] h-2 rounded-full transition-all animate-pulse" style="width:100%"></div>
                            </div>
                        </div>
                        <div class="space-y-2">
                            <div v-for="(line, i) in convertLogLines" :key="i"
                                :class="['flex items-start gap-2 text-sm p-2 rounded', line.ok ? 'bg-[#146c2e]/5 text-[#146c2e]' : 'bg-error-container text-error']">
                                <i :class="['fas mt-0.5', line.ok ? 'fa-check-circle text-[#146c2e]' : 'fa-times-circle text-error']"></i>
                                <div class="flex-1 min-w-0">
                                    <span class="font-medium">#{{ line.idx }}</span> {{ line.title }}
                                    <span v-if="line.error" class="block text-error text-xs mt-0.5">{{ line.error }}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="p-4 border-t flex justify-end">
                        <button @click="closeConvertLogModal" :disabled="converting || wooConverting"
                            class="px-6 py-2 bg-surface-container hover:bg-surface-container-high rounded-lg text-sm font-medium transition disabled:opacity-30">
                            {{ converting ? '转换中...' : '关闭' }}
                        </button>
                    </div>
                </div>
            </div>

            <!-- 筛品 - Feed生成 -->
            <div v-if="currentPage === 'shai-pin-feed'" class="fade-in">
                <div class="flex items-center justify-between mb-6 flex-wrap gap-3">
                    <h3 class="font-semibold text-on-surface">
                        <i class="fas fa-file-export mr-2 text-[#146c2e]"></i>Feed 生成
                        <span v-if="generatedFeed.length" class="text-sm text-on-surface-variant ml-2">({{ generatedFeed.length }} 件商品)</span>
                        <a v-if="feedUrl[feedSyncSiteId]" :href="feedUrl[feedSyncSiteId]" target="_blank"
                            class="ml-3 text-xs text-primary hover:text-primary underline inline-flex items-center gap-1">
                            <i class="fas fa-external-link-alt"></i>{{ feedUrl[feedSyncSiteId].split('/').pop() }}
                        </a>
                    </h3>
                    <div class="flex items-center gap-3">
                        <!-- Site selector -->
                        <select v-model="feedSyncSiteId" class="border rounded-lg px-3 py-2 text-sm bg-surface-container-lowest focus:ring-2 focus:ring-green-300">
                            <option :value="null">-- 选择站点 --</option>
                            <option v-for="site in sites" :key="site.id" :value="site.id">{{ site.site_name }} ({{ site.url }})</option>
                        </select>
                        <button @click="createFeedForSite" :disabled="!feedSyncSiteId || syncingFeed || !generatedFeed.length"
                            class="px-4 py-2 bg-[#146c2e] text-on-primary rounded-lg text-sm hover:bg-[#146c2e]/80 disabled:opacity-50 transition whitespace-nowrap">
                            <span class="material-symbols-outlined text-[18px]">add_circle</span>{{ syncingFeed ? '创建中...' : '创建' }}
                        </button>
                        <button @click="cleanFeedFromSite" :disabled="!feedSyncSiteId || syncingFeed"
                            class="px-4 py-2 bg-tertiary-container text-on-primary rounded-lg text-sm hover:bg-tertiary disabled:opacity-50 transition whitespace-nowrap">
                            <i class="fas fa-broom mr-1"></i>清理
                        </button>
                        <button @click="loadGeneratedFeed" class="px-4 py-2 border rounded-lg text-sm hover:bg-surface-container-low transition">
                            <span class="material-symbols-outlined">sync</span>刷新
                        </button>
                        <button v-if="generatedFeed.length" @click="clearGeneratedFeed"
                            class="px-4 py-2 bg-error text-on-primary rounded-lg text-sm hover:bg-error transition">
                            <i class="fas fa-trash mr-1"></i>清除
                        </button>
                    </div>
                </div>

                <!-- Empty state -->
                <div v-if="!generatedFeed.length" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center">
                    <div class="w-20 h-20 bg-[#146c2e]/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <i class="fas fa-file-export text-[#146c2e] text-3xl"></i>
                    </div>
                    <h3 class="text-lg font-semibold text-on-surface mb-2">Feed 生成</h3>
                    <p class="text-on-surface-variant mb-1">暂无生成数据</p>
                    <p class="text-xs text-on-surface-variant">从 网站产品页面点击「生成 Feed」或在商品来源导入产品后在此创建</p>
                </div>

                <!-- Feed product table -->
                <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                    <!-- Toolbar -->
                    <div class="px-6 py-3 bg-surface-container-low border-b flex items-center justify-between text-xs text-on-surface-variant">
                        <span>共 {{ generatedFeed.length }} 件产品</span>
                        <div class="flex items-center gap-3">
                            <label class="flex items-center gap-1 cursor-pointer hover:text-on-surface">
                                <input type="checkbox" :checked="feedSelectedIndices.size === generatedFeed.length" @change="selectAllFeed" class="accent-green-500">
                                全选
                            </label>
                            <button @click="deleteSelectedFeedItems" :disabled="!feedSelectedIndices.size"
                                class="px-4 py-1.5 bg-error text-on-primary rounded text-xs font-medium hover:bg-error disabled:opacity-50 transition">
                                <i class="fas fa-trash mr-1"></i>删除 ({{ feedSelectedIndices.size }})
                            </button>
                        </div>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm">
                            <thead class="bg-surface-container-low text-left text-xs text-on-surface-variant uppercase border-b">
                                <tr>
                                    <th class="px-4 py-3 w-10"></th>
                                    <th class="px-4 py-3 w-14">图片</th>
                                    <th class="px-4 py-3 min-w-[220px] max-w-[300px]">产品信息</th>
                                    <th class="px-4 py-3 w-32">品牌/价格</th>
                                    <th class="px-4 py-3 w-28">评分/排名</th>
                                    <th class="px-4 py-3 w-12">详情</th>
                                    <th class="px-4 py-3 w-36">更多图片</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y">
                                <tr v-for="(p, idx) in generatedFeed" :key="p.id"
                                    :class="['hover:bg-surface-container-low transition align-top cursor-pointer', feedSelectedIndices.has(idx) ? 'bg-[#146c2e]/5' : '']"
                                    @click="toggleFeedSelect(idx)">
                                    <td class="px-4 py-3">
                                        <input type="checkbox" :checked="feedSelectedIndices.has(idx)" class="accent-green-500 pointer-events-none">
                                    </td>
                                    <td class="px-4 py-3">
                                        <img v-if="p.thumbnail" :src="p.thumbnail" class="w-12 h-12 rounded border object-cover" :alt="p.title">
                                        <img v-else-if="p.images && p.images.length" :src="p.images[0]" class="w-12 h-12 rounded border object-cover" :alt="p.title">
                                        <div v-else class="w-12 h-12 bg-surface-container rounded border flex items-center justify-center"><i class="fas fa-image text-on-surface-variant text-xs"></i></div>
                                    </td>
                                    <td class="px-4 py-3 max-w-[300px]">
                                        <a v-if="p.source_url" :href="p.source_url" target="_blank" @click.stop
                                            class="font-semibold text-on-surface hover:text-primary transition line-clamp-1 block" :title="p.title">
                                            {{ p.title }}
                                        </a>
                                        <span v-else class="font-semibold text-on-surface line-clamp-1 block" :title="p.title">{{ p.title }}</span>
                                        <div class="flex items-center gap-2 mt-1 text-xs text-on-surface-variant flex-wrap">
                                            <span v-if="p.item_id">ASIN: {{ p.item_id }}</span>
                                            <span v-if="p.extra_data && p.extra_data.sellerName" class="text-primary" :title="p.extra_data.sellerUrl || ''">
                                                <i class="fas fa-store-alt mr-0.5"></i>{{ p.extra_data.sellerName }}
                                            </span>
                                            <span v-if="p.extra_data && p.extra_data.isPrime" class="bg-blue-100 text-primary px-1.5 py-0.5 rounded text-[10px] font-semibold">Prime</span>
                                            <span v-if="p.extra_data && p.extra_data.condition && p.extra_data.condition !== 'New'" class="bg-yellow-100 text-yellow-700 px-1.5 py-0.5 rounded text-[10px]">{{ p.extra_data.condition }}</span>
                                            <span v-if="p.extra_data && p.extra_data.couponText" class="bg-[#146c2e]/10 text-[#146c2e] px-1.5 py-0.5 rounded text-[10px] font-semibold">{{ p.extra_data.couponText }}</span>
                                        </div>
                                    </td>
                                    <td class="px-4 py-3">
                                        <p class="text-xs text-on-surface-variant" v-if="p.brand">{{ p.brand }}</p>
                                        <p v-if="p.extra_data && p.extra_data.originalPrice && p.extra_data.originalPrice !== p.price" class="text-xs text-on-surface-variant line-through">{{ p.currency || 'USD' }} {{ p.extra_data.originalPrice }}</p>
                                        <p class="font-bold text-[#146c2e]" v-if="p.price">{{ p.currency || '' }} {{ p.price }}</p>
                                        <p v-else class="text-on-surface-variant text-xs">-</p>
                                        <p v-if="p.extra_data && p.extra_data.discount" class="text-xs text-error mt-0.5 font-medium">{{ p.extra_data.discount }}</p>
                                    </td>
                                    <td class="px-4 py-3">
                                        <div class="flex items-center gap-1">
                                            <div v-if="p.ratings" class="flex items-center gap-1 text-yellow-600">
                                                <i class="fas fa-star text-[10px]"></i><span class="font-medium text-xs">{{ p.ratings }}</span>
                                            </div>
                                            <span v-else class="text-on-surface-variant text-xs">-</span>
                                        </div>
                                        <p v-if="p.reviews_count" class="text-xs text-on-surface-variant mt-0.5">{{ p.reviews_count.toLocaleString() }} 评</p>
                                        <p v-if="p.extra_data && p.extra_data.bestSellerRank" class="text-xs text-tertiary mt-0.5 font-medium" :title="'Best Sellers Rank'">BSR #{{ p.extra_data.bestSellerRank }}</p>
                                        <p v-if="p.extra_data && p.extra_data.estimatedSales" class="text-xs text-on-surface-variant mt-0.5">{{ p.extra_data.estimatedSales }}</p>
                                    </td>
                                    <td class="px-4 py-3 text-center">
                                        <button v-if="p.description || (p.features && p.features.length) || (p.extra_data && Object.keys(p.extra_data).length)"
                                            @click.stop="showFeedDescription(buildFeedDetailText(p))"
                                            class="text-primary hover:text-primary transition" title="查看详情">
                                            <i class="fas fa-info-circle text-lg"></i>
                                        </button>
                                        <span v-else class="text-on-surface-variant">-</span>
                                    </td>
                                    <td class="px-4 py-3">
                                        <div v-if="p.images && p.images.length > 1" class="flex flex-wrap gap-1">
                                            <img v-for="(img, i) in p.images.slice(0, 4)" :key="i" :src="img"
                                                class="w-10 h-10 rounded border object-cover" :alt="p.title + ' ' + (i+1)">
                                            <span v-if="p.images.length > 4" class="text-xs text-on-surface-variant self-center">+{{ p.images.length - 4 }}</span>
                                        </div>
                                        <span v-else class="text-on-surface-variant text-xs">-</span>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Description Modal -->
                <div v-if="showFeedDescModal" class="modal-overlay modal-overlay" @click.self="showFeedDescModal = false">
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 w-full max-w-3xl max-h-[85vh] p-6 fade-in flex flex-col">
                        <div class="flex items-center justify-between mb-4">
                            <h3 class="text-lg font-semibold text-on-surface"><i class="fas fa-info-circle mr-2 text-primary"></i>产品详情</h3>
                            <button @click="showFeedDescModal = false" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button>
                        </div>
                        <div class="overflow-y-auto flex-1 text-sm text-on-surface whitespace-pre-wrap">{{ feedDescContent }}</div>
                    </div>
                </div>
            </div>

            <!-- 网站产品 产品 -->
            <div v-if="currentPage === 'woocommerce-products'" class="fade-in">
                <div class="flex items-center justify-between mb-6 flex-wrap gap-3">
                    <h3 class="font-semibold text-on-surface">
                        <i class="fas fa-shopping-cart mr-2 text-primary"></i>网站产品 产品
                        <span v-if="wooProducts.length" class="text-sm text-on-surface-variant ml-2">({{ wooProducts.length }} 件商品)</span>
                    </h3>
                    <div class="flex items-center gap-3">
                        <!-- Site selector -->
                        <select v-model="wooSyncSiteId" class="border rounded-lg px-3 py-2 text-sm bg-surface-container-lowest focus:ring-2 focus:ring-blue-300">
                            <option :value="null">-- 选择站点 --</option>
                            <option v-for="site in sites" :key="site.id" :value="site.id">{{ site.site_name }} ({{ site.url }})</option>
                        </select>
                        <button @click="syncWooToSite" :disabled="!wooSyncSiteId || syncingWoo || !wooProducts.length"
                            class="px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50 transition whitespace-nowrap">
                            <i class="fas fa-cloud-upload-alt mr-1"></i>{{ syncingWoo ? '同步中...' : '同步' }}
                        </button>
                        <button @click="cleanWooFromSite" :disabled="!wooSyncSiteId || syncingWoo"
                            class="px-4 py-2 bg-tertiary-container text-on-primary rounded-lg text-sm hover:bg-tertiary disabled:opacity-50 transition whitespace-nowrap">
                            <i class="fas fa-broom mr-1"></i>清理
                        </button>
                        <button @click="generateFeedFromWoo" :disabled="wooGeneratingFeed || !wooProducts.length"
                            class="px-4 py-2 bg-[#146c2e] text-on-primary rounded-lg text-sm hover:bg-[#146c2e]/80 disabled:opacity-50 transition whitespace-nowrap">
                            <i class="fas fa-file-export mr-1"></i>{{ wooGeneratingFeed ? '生成中...' : '生成 Feed' }}
                        </button>
                        <span v-if="wooConvertProgress" class="text-xs text-on-surface-variant">{{ wooConvertProgress }}</span>
                    </div>
                </div>

                <!-- Empty state -->
                <div v-if="!wooProducts.length" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center">
                    <div class="w-20 h-20 bg-blue-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <i class="fas fa-shopping-cart text-primary text-3xl"></i>
                    </div>
                    <h3 class="text-lg font-semibold text-on-surface mb-2">网站产品 产品</h3>
                    <p class="text-on-surface-variant mb-1">暂无 网站产品 产品</p>
                    <p class="text-xs text-on-surface-variant">在商品来源页面使用「爆品导入」搜索产品后点击「生成 网站产品」</p>
                </div>

                <!-- 网站产品 product table -->
                <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                    <div class="px-6 py-3 bg-surface-container-low border-b flex items-center justify-between text-xs text-on-surface-variant">
                        <span>共 {{ wooProducts.length }} 件产品</span>
                        <div class="flex items-center gap-3">
                            <label class="flex items-center gap-1 cursor-pointer hover:text-on-surface">
                                <input type="checkbox" :checked="wooSelectedIndices.size === wooProducts.length" @change="selectAllWoo" class="accent-blue-500">
                                全选
                            </label>
                            <button @click="deleteSelectedWooProducts" :disabled="!wooSelectedIndices.size"
                                class="px-4 py-1.5 bg-error text-on-primary rounded text-xs font-medium hover:bg-error disabled:opacity-50 transition">
                                <i class="fas fa-trash mr-1"></i>删除 ({{ wooSelectedIndices.size }})
                            </button>
                        </div>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm">
                            <thead class="bg-surface-container-low text-left text-xs text-on-surface-variant uppercase border-b">
                                <tr>
                                    <th class="px-3 py-2 w-8"></th>
                                    <th class="px-3 py-2 w-[140px] max-w-[180px]">产品名称</th>
                                    <th class="px-3 py-2 w-20">价格</th>
                                    <th class="px-3 py-2 w-16">库存</th>
                                    <th class="px-3 py-2 w-20">分类</th>
                                    <th class="px-3 py-2 w-12">图</th>
                                </tr>
                            </thead>
                            <tbody class="divide-y">
                                <tr v-for="(p, idx) in wooProducts" :key="p.id"
                                    :class="['hover:bg-surface-container-low transition cursor-pointer', wooSelectedIndices.has(idx) ? 'bg-blue-50' : '']"
                                    @click="toggleWooSelect(idx)">
                                    <td class="px-3 py-2">
                                        <input type="checkbox" :checked="wooSelectedIndices.has(idx)" class="accent-blue-500 pointer-events-none">
                                    </td>
                                    <td class="px-3 py-2 max-w-[180px]">
                                        <a v-if="p.source_url" :href="p.source_url" target="_blank" @click.stop
                                            class="font-medium text-on-surface hover:text-primary transition line-clamp-1 block text-xs" :title="p.name">
                                            {{ (p.name || '').slice(0, 18) }}{{ (p.name || '').length > 18 ? '..' : '' }}
                                        </a>
                                        <span v-else class="font-medium text-on-surface line-clamp-1 block text-xs" :title="p.name">{{ (p.name || '').slice(0, 18) }}{{ (p.name || '').length > 18 ? '..' : '' }}</span>
                                    </td>
                                    <td class="px-3 py-2">
                                        <p class="font-bold text-[#146c2e] text-xs" v-if="p.regular_price">{{ p.regular_price }}</p>
                                        <p v-else class="text-on-surface-variant text-xs">-</p>
                                    </td>
                                    <td class="px-3 py-2">
                                        <span :class="['px-1.5 py-0.5 rounded-full text-[10px] font-medium',
                                            p.stock_status === 'instock' ? 'bg-[#146c2e]/10 text-[#146c2e]' :
                                            p.stock_status === 'outofstock' ? 'bg-error-container text-error' :
                                            'bg-yellow-100 text-yellow-700']">
                                            {{ p.stock_status === 'instock' ? '有货' : p.stock_status === 'outofstock' ? '缺货' : '预售' }}
                                        </span>
                                    </td>
                                    <td class="px-3 py-2 text-[10px] text-on-surface-variant">
                                        <span v-if="p.categories" class="line-clamp-1" :title="p.categories">{{ (p.categories || '').slice(0, 12) }}{{ (p.categories || '').length > 12 ? '..' : '' }}</span>
                                        <span v-else>-</span>
                                    </td>
                                    <td class="px-3 py-2">
                                        <div v-if="p.images" class="w-6 h-6 rounded border object-cover overflow-hidden">
                                            <img :src="p.images.split('|')[0]" class="w-full h-full object-cover" :alt="p.name">
                                        </div>
                                        <span v-else class="text-on-surface-variant text-[10px]">-</span>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Settings (Tabbed) -->
            <div v-if="currentPage === 'settings'" class="fade-in">
                <div>
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1">
                        <!-- Tab Navigation -->
                        <div class="flex border-b border-outline-variant overflow-x-auto">
                            <button v-for="tab in settingsTabs" :key="tab.key"
                                @click="settingsActiveTab = tab.key"
                                :class="['px-4 py-3 text-sm font-medium whitespace-nowrap transition border-b-2',
                                         settingsActiveTab === tab.key ? 'text-primary border-blue-700' : 'text-on-surface-variant border-transparent hover:text-on-surface hover:border-outline']">
                                {{ tab.label }}
                            </button>
                        </div>
                        <div class="p-6 space-y-4">
                            <!-- Tab: WordPress -->
                            <div v-if="settingsActiveTab === 'wordpress'">
                                <div><label class="block text-sm font-medium text-on-surface mb-1">默认管理员用户名</label><input v-model="globalConfig.default_admin_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div>
                                <div class="mt-4"><label class="block text-sm font-medium text-on-surface mb-1">默认管理员密码</label><input v-model="globalConfig.default_admin_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"><p class="text-xs text-on-surface-variant mt-1">应用于所有新创建的WordPress站点</p></div>
                                <div class="mt-4"><label class="block text-sm font-medium text-on-surface mb-1">默认数据库服务</label><select v-model="globalConfig.db_service" class="w-full px-4 py-2 border rounded-lg focus:border-primary"><option value="mariadb">MariaDB</option><option value="mysql">MySQL</option></select></div>
                            </div>
                            <!-- Tab: 1Panel 环境 -->
                            <div v-else-if="settingsActiveTab === 'panel'" @vue:mounted="loadPanelEnvironments()">
                                <div class="flex items-center justify-between mb-4">
                                    <h4 class="text-sm font-semibold text-on-surface"><span class="material-symbols-outlined">dns</span>已保存的 1Panel 环境</h4>
                                    <button @click="openPanelEnvModal(null)" class="btn-primary text-on-primary px-3 py-1.5 rounded-lg text-sm"><i class="fas fa-plus mr-1"></i>添加环境</button>
                                </div>
                                <div v-if="!panelEnvironments.length" class="text-center py-8 text-sm text-on-surface-variant">
                                    <i class="fas fa-inbox text-2xl mb-2 block"></i>暂未配置 1Panel 环境
                                </div>
                                <div v-else class="space-y-3">
                                    <div v-for="env in panelEnvironments" :key="env.id" class="bg-surface-container-low rounded-lg p-4 flex items-center justify-between">
                                        <div class="flex-1">
                                            <div class="flex items-center gap-2">
                                                <span class="font-medium text-on-surface">{{ env.name }}</span>
                                                <span v-if="env.is_default" class="text-xs bg-blue-100 text-primary px-2 py-0.5 rounded-full">默认</span>
                                            </div>
                                            <p class="text-xs text-on-surface-variant mt-1">{{ env.host }}:{{ env.port }}</p>
                                            <p v-if="env.cf_account_id" class="text-xs text-primary mt-0.5">CF: {{ (cfAccounts.find(a => a.id === env.cf_account_id) || {}).name || env.cf_account_id }}</p>
                                        </div>
                                        <div class="flex gap-1">
                                            <button v-if="!env.is_default" @click="handleSetDefaultPanelEnv(env)" class="text-xs text-on-surface-variant hover:text-primary px-2 py-1" title="设为默认"><i class="fas fa-star"></i></button>
                                            <button @click="openPanelEnvModal(env)" class="text-xs text-on-surface-variant hover:text-primary px-2 py-1" title="编辑"><span class="material-symbols-outlined">edit</span></button>
                                            <button @click="handleDeletePanelEnv(env)" class="text-xs text-on-surface-variant hover:text-error px-2 py-1" title="删除"><span class="material-symbols-outlined">delete</span></button>
                                        </div>
                                    </div>
                                </div>
                                <!-- Panel Env Modal -->
                                <div v-if="showPanelEnvModal" class="modal-overlay modal-overlay" @click.self="closePanelEnvModal">
                                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 w-full max-w-md p-6 fade-in">
                                        <h3 class="text-lg font-semibold text-on-surface mb-4">{{ panelEnvEditId ? '编辑环境' : '添加 1Panel 环境' }}</h3>
                                        <div class="space-y-4">
                                            <div><label class="block text-sm font-medium text-on-surface mb-1">环境名称</label><input v-model="panelEnvForm.name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary" placeholder="如：美国1Panel"></div>
                                            <div><label class="block text-sm font-medium text-on-surface mb-1">主机地址</label><input v-model="panelEnvForm.host" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary" placeholder="如：192.168.1.1"></div>
                                            <div><label class="block text-sm font-medium text-on-surface mb-1">端口</label><input v-model.number="panelEnvForm.port" type="number" class="w-full px-4 py-2 border rounded-lg focus:border-primary" placeholder="3500"></div>
                                            <div><label class="block text-sm font-medium text-on-surface mb-1">API Key</label><input v-model="panelEnvForm.api_key" type="password" class="w-full px-4 py-2 border rounded-lg focus:border-primary" placeholder="1Panel API Key"></div>
                                            <div v-if="cfAccounts.length"><label class="block text-sm font-medium text-on-surface mb-1">Cloudflare 账户</label>
                                                <select v-model="panelEnvForm.cf_account_id" class="w-full px-4 py-2 border rounded-lg focus:border-primary">
                                                    <option :value="null">使用默认账户</option>
                                                    <option v-for="acct in cfAccounts" :key="acct.id" :value="acct.id">{{ acct.name }}<span v-if="acct.is_default" class="text-on-surface-variant"> (默认)</span></option>
                                                </select>
                                            </div>
                                            <p v-if="panelEnvFormError" class="text-error text-sm">{{ panelEnvFormError }}</p>
                                        </div>
                                        <div class="flex justify-end gap-2 mt-6">
                                            <button @click="closePanelEnvModal" class="px-4 py-2 border rounded-lg text-sm hover:bg-surface-container-low">取消</button>
                                            <button @click="handleSavePanelEnv" class="btn-primary text-on-primary px-6 py-2 rounded-lg text-sm">{{ panelEnvEditId ? '保存' : '创建' }}</button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <!-- Tab: Agnes AI -->
                            <div v-else-if="settingsActiveTab === 'agnes'">
                                <div class="flex items-center justify-between mb-3">
                                    <span :class="agnesConnected ? 'text-[#146c2e]' : 'text-error'"><span class="material-symbols-outlined text-[10px]">circle</span>{{ agnesConnected ? '已连接' : '未连接' }}</span>
                                    <span class="text-xs text-on-surface-variant">agnes-2.0-flash</span>
                                </div>
                                <label class="block text-sm font-medium text-on-surface mb-2">API Key</label>
                                <div class="flex gap-2 mb-2 items-center">
                                    <div class="flex-1 relative">
                                        <input :type="agnesVisibleKey ? 'text' : 'password'" v-model="agnesApiKey" placeholder="输入 Agnes API Key" class="w-full px-4 py-3 border rounded-lg text-sm focus:border-primary pr-10">
                                    </div>
                                </div>
                                <button @click="agnesVerify" :disabled="agnesVerifying" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm">
                                    <i v-if="agnesVerifying" class="fas fa-spinner fa-spin mr-1"></i>
                                    {{ agnesVerifying ? '验证中...' : '验证并保存' }}
                                </button>
                                <p class="text-xs text-on-surface-variant mt-2">Agnes AI 用于快速生成商城页面（约3-5秒/页），替代Stitch</p>
                            </div>

                            <!-- Tab: DeepSeek -->
                            <div v-else-if="settingsActiveTab === 'deepseek'">
                                <div class="flex items-center justify-between mb-3">
                                    <span :class="deepseekConnected ? 'text-[#146c2e]' : 'text-error'"><span class="material-symbols-outlined text-[10px]">circle</span>{{ deepseekConnected ? '已连接' : '未连接' }}</span>
                                    <a href="https://platform.deepseek.com/api_keys" target="_blank" class="text-xs text-primary hover:underline"><i class="fas fa-external-link-alt mr-1"></i>来源</a>
                                </div>
                                <label class="block text-sm font-medium text-on-surface mb-2">API Keys（支持多个，自动轮询）</label>
                                <div v-for="(key, idx) in deepseekApiKeys" :key="'ds'+idx" class="flex gap-2 mb-2 items-center">
                                    <div class="flex-1 relative">
                                        <input v-model="deepseekApiKeys[idx]" :type="deepseekVisibleKeys[idx] ? 'text' : 'password'" placeholder="sk-..." class="w-full px-4 py-2 pr-10 border rounded-lg focus:border-primary" @input="delete deepseekKeyErrors[idx]">
                                        <button @click="deepseekVisibleKeys[idx] = !deepseekVisibleKeys[idx]" class="absolute right-2 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface-variant" tabindex="-1"><i :class="deepseekVisibleKeys[idx] ? 'fas fa-eye-slash' : 'fas fa-eye'"></i></button>
                                    </div>
                                    <span v-if="deepseekKeyErrors[idx]" class="text-error text-xs whitespace-nowrap" :title="deepseekKeyErrors[idx]"><i class="fas fa-exclamation-circle mr-1"></i>{{ deepseekKeyErrors[idx] }}</span>
                                    <span v-else-if="deepseekConnected && deepseekApiKeys[idx].trim()" class="text-[#146c2e] text-xs"><span class="material-symbols-outlined">check_circle</span></span>
                                    <button v-if="deepseekApiKeys.length > 1" @click="deepseekApiKeys.splice(idx, 1)" class="text-red-400 hover:text-error px-2" title="删除"><span class="material-symbols-outlined">close</span></button>
                                </div>
                                <div class="flex gap-2 mt-3">
                                    <button @click="deepseekApiKeys.push('')" class="text-sm text-primary hover:text-primary border border-dashed border-blue-300 rounded-lg px-4 py-2 hover:bg-surface-container-low"><i class="fas fa-plus mr-1"></i>添加密钥</button>
                                    <button @click="deepseekVerify" :disabled="loading" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm"><i class="fas fa-check mr-2"></i>验证并保存</button>
                                </div>
                            </div>
                            <!-- Tab: Crawlbase -->
                            <div v-else-if="settingsActiveTab === 'crawlbase'">
                                <div class="flex items-center justify-between mb-3">
                                    <span :class="crawlbaseConnected ? 'text-[#146c2e]' : 'text-error'"><span class="material-symbols-outlined text-[10px]">circle</span>{{ crawlbaseConnected ? '已连接' : '未连接' }}</span>
                                    <a href="https://crawlbase.com/dashboard/account" target="_blank" class="text-xs text-primary hover:underline"><i class="fas fa-external-link-alt mr-1"></i>来源</a>
                                </div>
                                <label class="block text-sm font-medium text-on-surface mb-2">API Tokens（支持多个，自动轮询）</label>
                                <div v-for="(key, idx) in crawlbaseApiKeys" :key="'cb'+idx" class="flex gap-2 mb-2 items-center">
                                    <div class="flex-1 relative">
                                        <input v-model="crawlbaseApiKeys[idx]" :type="crawlbaseVisibleKeys[idx] ? 'text' : 'password'" placeholder="输入 Crawlbase API Token" class="w-full px-4 py-2 pr-10 border rounded-lg focus:border-primary" @input="delete crawlbaseKeyErrors[idx]">
                                        <button @click="crawlbaseVisibleKeys[idx] = !crawlbaseVisibleKeys[idx]" class="absolute right-2 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface-variant" tabindex="-1"><i :class="crawlbaseVisibleKeys[idx] ? 'fas fa-eye-slash' : 'fas fa-eye'"></i></button>
                                    </div>
                                    <span v-if="crawlbaseKeyErrors[idx]" class="text-error text-xs whitespace-nowrap" :title="crawlbaseKeyErrors[idx]"><i class="fas fa-exclamation-circle mr-1"></i>{{ crawlbaseKeyErrors[idx] }}</span>
                                    <span v-else-if="crawlbaseConnected && crawlbaseApiKeys[idx].trim()" class="text-[#146c2e] text-xs"><span class="material-symbols-outlined">check_circle</span></span>
                                    <button v-if="crawlbaseApiKeys.length > 1" @click="crawlbaseApiKeys.splice(idx, 1)" class="text-red-400 hover:text-error px-2" title="删除"><span class="material-symbols-outlined">close</span></button>
                                </div>
                                <div class="flex gap-2 mt-3">
                                    <button @click="crawlbaseApiKeys.push('')" class="text-sm text-primary hover:text-primary border border-dashed border-blue-300 rounded-lg px-4 py-2 hover:bg-surface-container-low"><i class="fas fa-plus mr-1"></i>添加密钥</button>
                                    <button @click="crawlbaseVerify" :disabled="loading" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm"><i class="fas fa-check mr-2"></i>验证并保存</button>
                                </div>
                            </div>
                            <!-- Tab: Cloudflare -->
                            <div v-else-if="settingsActiveTab === 'cloudflare'">
                                <div class="flex items-center justify-between mb-3">
                                    <span :class="cfConnected ? 'text-[#146c2e]' : 'text-error'"><span class="material-symbols-outlined text-[10px]">circle</span>{{ cfConnected ? '已连接' : '未连接' }}</span>
                                    <a href="https://dash.cloudflare.com/profile/api-tokens" target="_blank" class="text-xs text-primary hover:underline"><i class="fas fa-external-link-alt mr-1"></i>来源</a>
                                </div>
                                <div v-if="cfAccounts.length" class="bg-surface-container-low rounded-lg p-3 mb-4">
                                    <h4 class="text-sm font-semibold text-on-surface mb-2">已保存的账号</h4>
                                    <div v-for="acc in cfAccounts" :key="acc.id" class="flex items-center justify-between py-2 border-b border-outline-variant last:border-b-0">
                                        <div class="flex items-center gap-2">
                                            <i class="fas fa-cloud text-tertiary"></i>
                                            <span class="text-sm font-medium">{{ acc.name }}</span>
                                            <span v-if="acc.is_default" class="text-xs bg-orange-100 text-tertiary px-2 py-0.5 rounded-full">默认</span>
                                        </div>
                                        <div class="flex gap-1">
                                            <button v-if="!acc.is_default" @click="handleSetDefaultCfAccount(acc.id)" class="text-xs text-on-surface-variant hover:text-tertiary px-2 py-1" title="设为默认"><i class="fas fa-star"></i></button>
                                            <button @click="handleDeleteCfAccount(acc.id)" class="text-xs text-on-surface-variant hover:text-error px-2 py-1" title="删除"><span class="material-symbols-outlined">delete</span></button>
                                        </div>
                                    </div>
                                </div>
                                <label class="block text-sm font-medium text-on-surface mb-1">API Token</label>
                                <div class="flex gap-2"><input v-model="cfToken" type="password" placeholder="输入Cloudflare API Token" class="flex-1 px-4 py-2 border rounded-lg focus:border-primary"><button @click="cfVerify" :disabled="loading" class="btn-primary text-on-primary px-4 py-2 rounded-lg"><i class="fas fa-check mr-2"></i>验证并保存</button></div>
                            </div>
                            <!-- Tab: 谷歌账户 -->
                            <div v-else-if="settingsActiveTab === 'google_account'" @vue:mounted="loadGoogleAccounts()">
                                <div class="flex items-center justify-between mb-3">
                                    <h3 class="font-semibold text-on-surface"><i class="fab fa-google mr-2 text-primary"></i>谷歌账户池</h3>
                                    <button @click="loadGoogleAccounts" class="text-xs text-primary hover:text-primary"><span class="material-symbols-outlined">sync</span>刷新</button>
                                </div>
                                <p class="text-xs text-on-surface-variant mb-2"><i class="fas fa-info-circle mr-1"></i>用于 GMC 自动化时自动登录 Google（支持 TOTP 2FA）。格式: email|password|recovery_email|base32_secret|year|country</p>
                                <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-4 mb-4">
                                    <label class="block text-xs font-medium text-on-surface-variant mb-2">从 TXT 导入账户</label>
                                    <textarea v-model="googleAccountsText" rows="6" class="w-full px-3 py-2 border rounded-lg text-sm font-mono focus:border-primary" placeholder="粘贴 TXT 内容..."></textarea>
                                    <div class="mt-2">
                                        <button @click="handleImportGoogleAccounts" :disabled="importingGoogleAccounts" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm disabled:opacity-50">
                                            <i v-if="importingGoogleAccounts" class="fas fa-spinner fa-spin mr-1"></i>
                                            {{ importingGoogleAccounts ? '导入中...' : '导入' }}
                                        </button>
                                    </div>
                                </div>
                                <div v-if="!googleAccounts.length" class="text-center py-6 text-sm text-on-surface-variant">
                                    <i class="fas fa-inbox text-2xl mb-2 block"></i>暂无 Google 账户
                                </div>
                                <div v-else class="overflow-x-auto max-h-96 overflow-y-auto bg-surface-container-lowest rounded-xl shadow-level-1">
                                    <table class="w-full text-sm">
                                        <thead class="bg-surface-container-low text-xs text-on-surface-variant uppercase sticky top-0">
                                            <tr>
                                                <th class="px-3 py-2 text-left">#</th>
                                                <th class="px-3 py-2 text-left">Email</th>
                                                <th class="px-3 py-2 text-left">密码</th>
                                                <th class="px-3 py-2 text-left">恢复邮箱</th>
                                                <th class="px-3 py-2 text-left">国家</th>
                                                <th class="px-3 py-2 text-left">注册年</th>
                                                <th class="px-3 py-2 text-left">状态</th>
                                                <th class="px-3 py-2 text-left">操作</th>
                                            </tr>
                                        </thead>
                                        <tbody class="divide-y divide-outline-variant">
                                            <tr v-for="ga in googleAccounts" :key="ga.id" class="hover:bg-surface-container-low">
                                                <td class="px-3 py-2 text-xs font-mono">#{{ ga.id }}</td>
                                                <td class="px-3 py-2 text-xs font-mono">{{ ga.email }}</td>
                                                <td class="px-3 py-2 text-xs font-mono">{{ ga.password || '***' }}</td>
                                                <td class="px-3 py-2 text-xs">{{ ga.recovery_email || '—' }}</td>
                                                <td class="px-3 py-2 text-xs">{{ ga.country || '—' }}</td>
                                                <td class="px-3 py-2 text-xs">{{ ga.registration_year || '—' }}</td>
                                                <td class="px-3 py-2">
                                                    <span v-if="ga.occupied_kit_name" class="badge bg-error-container text-error font-semibold text-xs">被【{{ ga.occupied_kit_name }}】占用</span>
                                                    <span v-else class="badge bg-[#146c2e]/10 text-[#146c2e] font-semibold text-xs">可用</span>
                                                </td>
                                                <td class="px-3 py-2">
                                                    <button @click="handleDeleteGoogleAccount(ga.id)" class="text-xs text-on-surface-variant hover:text-error" title="删除"><span class="material-symbols-outlined">delete</span></button>
                                                </td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                                <div class="mt-2 text-xs text-on-surface-variant">
                                    共 {{ googleAccounts.length }} 个账户 | 可用 {{ googleAccounts.filter(a => !a.occupied_kit_name).length }} | 已占用 {{ googleAccounts.filter(a => a.occupied_kit_name).length }}
                                </div>
                            </div>
                            <!-- Tab: 指纹环境 -->
                            <div v-else-if="settingsActiveTab === 'fingerprint'" @vue:mounted="loadFingerprintCategories(); loadCloakbrowserProfiles(); loadProfileCategories()">
                                <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-4 mb-4 flex items-center justify-between">
                                    <div>
                                        <p class="font-medium text-on-surface"><i class="fas fa-power-off mr-2 text-primary"></i>启用指纹环境</p>
                                        <p class="text-xs text-on-surface-variant mt-1">开启后，建站和GMC注册将自动使用品牌套件关联的 CloakBrowser Profile</p>
                                    </div>
                                    <button type="button" @click="fingerprintEnabled = !fingerprintEnabled"
                                        :class="['relative inline-flex items-center rounded-full transition duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-blue-300 focus:ring-offset-2',
                                                 fingerprintEnabled ? 'bg-primary' : 'bg-surface-container-high']"
                                        style="width: 44px; height: 24px;">
                                        <span :class="['inline-block w-5 h-5 bg-surface-container-lowest rounded-full shadow transition duration-200 ease-in-out',
                                                       fingerprintEnabled ? 'translate-x-5' : 'translate-x-0.5']"></span>
                                    </button>
                                </div>

                                <!-- Category Tabs -->
                                <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-4 mb-4">
                                    <div class="flex items-end gap-2 mb-4">
                                        <div class="flex-1">
                                            <label class="block text-xs text-on-surface-variant mb-1">新建分类</label>
                                            <input v-model="newCategoryName" type="text" placeholder="输入分类名称" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" @keyup.enter="createFingerprintCategory">
                                        </div>
                                        <button @click="createFingerprintCategory" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm whitespace-nowrap">创建分类</button>
                                    </div>

                                    <!-- Tab Cards -->
                                    <div v-if="fingerprintCategories.length" class="flex gap-2 flex-wrap">
                                        <button v-for="cat in fingerprintCategories" :key="cat.id"
                                            @click="activeFingerprintCategory = cat.id"
                                            :class="['px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap',
                                                activeFingerprintCategory === cat.id
                                                    ? 'bg-primary-container text-on-primary shadow-level-1'
                                                    : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-low']">
                                            {{ cat.name }}
                                            <span class="ml-1.5 px-1.5 py-0.5 rounded-full text-xs opacity-80" :class="activeFingerprintCategory === cat.id ? 'bg-white/20' : 'bg-outline-variant/30'">
                                                {{ getProfilesByCategory(cat.id).length }}
                                            </span>
                                            <span @click.stop="deleteFingerprintCategory(cat.id)" class="ml-1.5 text-on-surface-variant hover:text-error cursor-pointer text-lg leading-none" title="删除分类">&times;</span>
                                        </button>
                                    </div>
                                    <div v-else class="text-center py-6 text-sm text-on-surface-variant">
                                        <i class="fas fa-inbox text-2xl mb-2 block"></i>暂无分类，请先创建
                                    </div>
                                </div>

                                <!-- Active Category Content -->
                                <div v-if="activeFingerprintCategory && fingerprintCategories.length">
                                    <!-- Import Profiles -->
                                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-4 mb-4">
                                        <label class="block text-sm font-medium text-on-surface mb-1">导入指纹环境</label>
                                        <p class="text-xs text-on-surface-variant mb-2">每行一个 Profile 名称</p>
                                        <textarea v-model="importingFingerprintText" rows="4"
                                            class="w-full px-3 py-2 border rounded-lg text-xs font-mono focus:border-primary"
                                            placeholder="profile_alpha&#10;profile_beta&#10;profile_gamma"></textarea>
                                        <button @click="importFingerprintProfiles" :disabled="importingFingerprints"
                                            class="mt-2 btn-primary text-on-primary px-4 py-2 rounded-lg text-sm">
                                            <i v-if="importingFingerprints" class="fas fa-spinner fa-spin mr-1"></i>
                                            导入到 {{ fingerprintCategories.find(c => c.id === activeFingerprintCategory)?.name || '当前分类' }}
                                        </button>
                                        <p v-if="importFingerprintResult" :class="['text-xs mt-1', (importFingerprintResult.startsWith('成功') || importFingerprintResult.startsWith('所有')) ? 'text-[#146c2e]' : 'text-error']">
                                            {{ importFingerprintResult }}
                                        </p>
                                    </div>

                                    <!-- Profile List -->
                                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                                        <div v-if="getProfilesByCategory(activeFingerprintCategory).length">
                                            <table class="w-full text-sm">
                                                <thead class="bg-surface-container-low text-xs text-on-surface-variant uppercase">
                                                    <tr>
                                                        <th class="px-3 py-2 text-left">名称</th>
                                                        <th class="px-3 py-2 text-left">平台</th>
                                                        <th class="px-3 py-2 text-left">国家</th>
                                                        <th class="px-3 py-2 text-left">代理</th>
                                                        <th class="px-3 py-2 text-left">状态</th>
                                                        <th class="px-3 py-2 text-right">操作</th>
                                                    </tr>
                                                </thead>
                                                <tbody class="divide-y divide-outline-variant">
                                                    <tr v-for="p in getProfilesByCategory(activeFingerprintCategory)" :key="p.name" class="hover:bg-surface-container-low">
                                                        <td class="px-3 py-2 font-mono text-xs">{{ p.name }}</td>
                                                        <td class="px-3 py-2 text-xs">{{ p.platform || p.config?.platform || '-' }}</td>
                                                        <td class="px-3 py-2 text-xs">{{ p.country || p.config?.country || '-' }}</td>
                                                        <td class="px-3 py-2 text-xs max-w-[120px] truncate" :title="p.proxy">{{ p.proxy || '-' }}</td>
                                                        <td class="px-3 py-2">
                                                            <span v-if="brandKits.some(bk => bk.cloakbrowser_profile_name === p.name)" class="badge bg-[#146c2e]/10 text-[#146c2e] text-xs">使用中</span>
                                                            <span v-else class="badge bg-surface-container-high text-on-surface-variant text-xs">可用</span>
                                                        </td>
                                                        <td class="px-3 py-2 text-right">
                                                            <button @click="removeProfileFromCategory(p.name)" class="text-xs text-error hover:text-error px-1 py-0.5" title="从分类移除">
                                                                <span class="material-symbols-outlined text-sm">link_off</span>
                                                            </button>
                                                        </td>
                                                    </tr>
                                                </tbody>
                                            </table>
                                        </div>
                                        <div v-else class="text-center py-8 text-sm text-on-surface-variant">
                                            <i class="fas fa-inbox text-2xl mb-2 block"></i>
                                            请在上方输入区导入 Profile 名称
                                        </div>
                                    </div>
                                </div>

                                <!-- No category selected -->
                                <div v-if="!activeFingerprintCategory && fingerprintCategories.length" class="text-center py-8 text-sm text-on-surface-variant bg-surface-container-lowest rounded-xl shadow-level-1">
                                    <i class="fas fa-hand-pointer text-2xl mb-2 block"></i>
                                    请选择上方的分类标签卡
                                </div>
                            </div>
                        </div>
                    </div>
                    <button @click="saveGlobalConfig" :disabled="loading" class="w-full btn-primary text-on-primary py-3 rounded-lg font-semibold mt-4"><i class="fas fa-save mr-2"></i>保存设置</button>
                </div>
            </div>

            <!-- User Management -->
            <div v-if="currentPage === 'users'" class="fade-in">
                <div class="flex items-center justify-between mb-6">
                    <h3 class="font-semibold text-on-surface"><i class="fas fa-users mr-2 text-primary"></i>用户管理</h3>
                    <button @click="openUserModal(null)" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm"><i class="fas fa-plus mr-2"></i>创建用户</button>
                </div>
                <div class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-surface-container-low text-left text-xs font-medium text-on-surface-variant uppercase tracking-wider">
                            <tr>
                                <th class="px-4 py-3">ID</th>
                                <th class="px-4 py-3">用户名</th>
                                <th class="px-4 py-3">角色</th>
                                <th class="px-4 py-3">1Panel 环境</th>
                                <th class="px-4 py-3">创建时间</th>
                                <th class="px-4 py-3 w-32">操作</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-outline-variant">
                            <tr v-for="user in users" :key="user.id" class="hover:bg-surface-container-low">
                                <td class="px-4 py-3 text-sm text-on-surface-variant">#{{ user.id }}</td>
                                <td class="px-4 py-3 font-medium">{{ user.username }}</td>
                                <td class="px-4 py-3"><span :class="user.role === 'admin' ? 'bg-blue-100 text-primary' : 'bg-blue-100 text-primary'" class="badge">{{ user.role === 'admin' ? '管理员' : '运营' }}</span></td>
                                <td class="px-4 py-3 text-xs text-on-surface-variant">{{ user.panel_env_name || '—' }}</td>
                                <td class="px-4 py-3 text-sm text-on-surface-variant">{{ user.created_at }}</td>
                                <td class="px-4 py-3">
                                    <div class="flex gap-1">
                                        <button @click="openUserModal(user)" class="text-xs text-primary hover:text-primary px-2 py-1" title="编辑"><span class="material-symbols-outlined">edit</span></button>
                                        <button @click="handleDeleteUser(user)" class="text-xs text-on-surface-variant hover:text-error px-2 py-1" title="删除"><span class="material-symbols-outlined">delete</span></button>
                                    </div>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <!-- User Modal -->
                <div v-if="showUserModal" class="modal-overlay modal-overlay" @click.self="closeUserModal">
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 w-full max-w-md p-6 fade-in">
                        <h3 class="text-lg font-semibold text-on-surface mb-4">{{ userEditId ? '编辑用户' : '创建用户' }}</h3>
                        <div class="space-y-4">
                            <div><label class="block text-sm font-medium text-on-surface mb-1">用户名</label><input v-model="userForm.username" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary" placeholder="请输入用户名"></div>
                            <div><label class="block text-sm font-medium text-on-surface mb-1">{{ userEditId ? '新密码（留空不修改）' : '密码' }}</label><input v-model="userForm.password" type="password" class="w-full px-4 py-2 border rounded-lg focus:border-primary" placeholder="请输入密码"></div>
                            <div><label class="block text-sm font-medium text-on-surface mb-1">角色</label><select v-model="userForm.role" class="w-full px-4 py-2 border rounded-lg focus:border-primary"><option value="operator">运营</option><option value="admin">管理员</option></select></div>
                            <div v-if="userForm.role === 'operator'"><label class="block text-sm font-medium text-on-surface mb-1">指定 1Panel 环境</label><select v-model="userForm.panel_environment_id" class="w-full px-4 py-2 border rounded-lg focus:border-primary"><option :value="null">使用默认环境</option><option v-for="env in panelEnvironments" :key="env.id" :value="env.id">{{ env.name }} ({{ env.host }})</option></select><p class="text-xs text-on-surface-variant mt-1">运营人员登录后将使用指定的 1Panel 环境</p></div>
                            <p v-if="userFormError" class="text-error text-sm">{{ userFormError }}</p>
                        </div>
                        <div class="flex justify-end gap-2 mt-6">
                            <button @click="closeUserModal" class="px-4 py-2 border rounded-lg text-sm hover:bg-surface-container-low">取消</button>
                            <button @click="handleSaveUser" class="btn-primary text-on-primary px-6 py-2 rounded-lg text-sm">{{ userEditId ? '保存' : '创建' }}</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Brand Kits List -->
            <div v-if="currentPage === 'brand-kits'" class="fade-in">
                <div class="flex items-center justify-between mb-6">
                    <h3 class="font-semibold text-on-surface"><i class="fas fa-paint-brush mr-2 text-primary"></i>品牌套件</h3>
                    <button @click="openBrandKitModal(null)" class="btn-primary text-on-primary px-4 py-2 rounded-lg text-sm"><i class="fas fa-plus mr-2"></i>创建套件</button>
                </div>

                <div v-if="brandKitsLoading" class="text-center py-20"><span class="spinner w-4 h-4 inline-block"></span></div>

                <div v-else-if="!brandKits.length" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-12 text-center">
                    <div class="w-20 h-20 bg-blue-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                        <i class="fas fa-paint-brush text-primary text-3xl"></i>
                    </div>
                    <h3 class="text-lg font-semibold text-on-surface mb-2">暂无品牌套件</h3>
                    <p class="text-on-surface-variant mb-4">创建品牌套件，使用AI生成专属Logo和品牌资源</p>
                    <button @click="openBrandKitModal(null)" class="btn-primary text-on-primary px-6 py-2 rounded-lg"><i class="fas fa-plus mr-2"></i>创建第一个套件</button>
                </div>

                <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                    <table class="w-full">
                        <thead class="bg-surface-container-low text-left text-xs font-medium text-on-surface-variant uppercase tracking-wider">
                            <tr>
                                <th class="px-4 py-3">logo</th>
                                <th class="px-4 py-3">套件名称</th>
                                <th class="px-4 py-3">品牌</th>
                                <th class="px-4 py-3">行业</th>
                                <th class="px-4 py-3">状态</th>
                                <th class="px-4 py-3">色彩</th>
                                <th class="px-4 py-3 w-56">操作</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-outline-variant">
                            <tr v-for="kit in brandKits" :key="kit.id" class="hover:bg-surface-container-low transition cursor-pointer" @click="openBrandKitDetail(kit)">
                                <td class="px-4 py-3">
                                    <div v-if="kit.processed_svg || kit.raw_svg" v-html="kit.processed_svg || kit.raw_svg" class="w-8 h-8 svg-preview"></div>
                                    <i v-else class="fas fa-paint-brush text-on-surface-variant text-lg"></i>
                                </td>
                                <td class="px-4 py-3">
                                    <p class="font-medium text-on-surface text-sm">{{ kit.name }}</p>
                                </td>
                                <td class="px-4 py-3">
                                    <span class="text-sm text-on-surface-variant">{{ kit.brand_name || '—' }}</span>
                                </td>
                                <td class="px-4 py-3">
                                    <span class="font-body-md text-on-surface-variant font-medium">{{ kit.industry || '—' }}</span>
                                </td>
                                <td class="px-4 py-3">
                                    <span :class="['text-xs px-2 py-0.5 rounded-full',
                                        kit.status === 'ready' ? 'bg-[#146c2e]/10 text-[#146c2e]' :
                                        kit.status === 'generating' ? 'bg-blue-100 text-primary' :
                                        kit.status === 'failed' ? 'bg-error-container text-error' :
                                        'bg-surface-container text-on-surface-variant']">
                                        {{ kit.status === 'ready' ? '已就绪' : kit.status === 'generating' ? '生成中' : kit.status === 'failed' ? '失败' : '草稿' }}
                                    </span>
                                </td>
                                <td class="px-4 py-3">
                                    <div v-if="kit.colors && kit.colors.length" class="flex gap-1">
                                        <span v-for="(c, i) in kit.colors" :key="i" class="w-4 h-4 rounded-full border border-outline" :style="{ backgroundColor: c }" :title="c"></span>
                                    </div>
                                    <span v-else class="text-xs text-on-surface-variant">—</span>
                                </td>
                                <td class="px-4 py-3" @click.stop>
                                    <div class="flex gap-1.5">
                                        <button @click="handleGenerateBrandKit(kit)" :disabled="brandKitGenerating[kit.id] && brandKitGenerating[kit.id].status === 'running'" class="px-2.5 py-1.5 bg-primary-container text-on-primary rounded text-xs hover:bg-primary disabled:opacity-50 transition">
                                            <i :class="['fas mr-0.5', (brandKitGenerating[kit.id] && brandKitGenerating[kit.id].status === 'running') ? 'fa-spinner fa-spin' : 'fa-magic']"></i>
                                            {{ (brandKitGenerating[kit.id] && brandKitGenerating[kit.id].status === 'running') ? '生成中' : '生成' }}
                                        </button>
                                        <button @click="openBrandKitModal(kit)" class="px-2.5 py-1.5 border rounded text-xs hover:bg-surface-container-low transition"><span class="material-symbols-outlined">edit</span></button>
                                        <button @click="handleDeleteBrandKit(kit)" class="px-2.5 py-1.5 border rounded text-xs text-red-400 hover:bg-error-container transition"><span class="material-symbols-outlined">delete</span></button>
                                    </div>
                                    <div v-if="brandKitGenerating[kit.id] && brandKitGenerating[kit.id].status === 'running'" class="mt-2">
                                        <div class="w-full bg-surface-container-high rounded-full h-1">
                                            <div class="bg-primary-container h-1 rounded-full transition-all" :style="{ width: ((brandKitGenerating[kit.id].current / 4) * 100) + '%' }"></div>
                                        </div>
                                        <p class="text-xs text-on-surface-variant mt-0.5">{{ brandKitGenerating[kit.id].steps[brandKitGenerating[kit.id].current] || '' }}</p>
                                    </div>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Google Merchant Center Automation -->
            <div v-if="currentPage === 'mc-automation'" class="fade-in">
                <div class="space-y-6">
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-2"><i class="fab fa-google mr-2 text-primary"></i>Google Merchant Center 自动化</h3>
                        <p class="text-sm text-on-surface-variant mb-4">注入站点验证标签（Google 将自动抓取验证）、通过指纹浏览器自动注册 MC 账号。Feed 生成由 <a href="#" @click.prevent="currentPage='shai-pin-feed'" class="text-primary underline">筛品</a> 接管。</p>
                        <div v-if="sites.length === 0" class="text-center py-12 text-on-surface-variant"><i class="fas fa-inbox text-4xl mb-4"></i><p>暂无站点</p></div>
                        <div v-else class="overflow-x-auto">
                            <table class="w-full text-sm">
                                <thead class="bg-surface-container-low"><tr><th class="px-4 py-3 text-left font-medium text-on-surface-variant">站点</th><th class="px-4 py-3 text-left font-medium text-on-surface-variant">Feed</th><th class="px-4 py-3 text-left font-medium text-on-surface-variant">域名验证</th><th class="px-4 py-3 text-left font-medium text-on-surface-variant">MC 账号</th><th class="px-4 py-3 text-right font-medium text-on-surface-variant">操作</th></tr></thead>
                                <tbody class="divide-y">
                                    <tr v-for="site in sites" :key="site.id" class="hover:bg-surface-container-low">
                                        <td class="px-4 py-3"><div class="font-medium text-on-surface">{{ site.site_name }}</div><div class="text-xs text-on-surface-variant">{{ site.url }}</div><div v-if="fingerprintEnabled && site.cloakbrowser_profile_name" class="mt-1"><span class="badge bg-blue-100 text-primary"><i class="fas fa-fingerprint mr-0.5"></i>{{ site.cloakbrowser_profile_name }}</span></div></td>
                                        <td class="px-4 py-3">
                                            <span v-if="mcFeedUrls[site.id]" class="text-[#146c2e] text-xs"><i class="fas fa-check-circle mr-1"></i>已生成</span>
                                            <span v-else class="text-on-surface-variant text-xs">未生成</span>
                                        </td>
                                        <td class="px-4 py-3">
                                            <span v-if="site.google_verification_done" class="text-[#146c2e] text-xs" title="验证标签已注入站点，Google 将自动抓取验证"><i class="fas fa-check-circle mr-1"></i>已注入</span>
                                            <span v-else class="text-on-surface-variant text-xs">未注入</span>
                                        </td>
                                        <td class="px-4 py-3">
                                            <span v-if="site.google_mc_account_id" class="text-[#146c2e] text-xs"><i class="fas fa-check-circle mr-1"></i>{{ site.google_mc_account_id }}</span>
                                            <span v-else class="text-on-surface-variant text-xs">未注册</span>
                                        </td>
                                        <td class="px-4 py-3 text-right">
                                            <div class="flex items-center justify-end gap-1">
                                                <button v-if="!site.google_mc_account_id" @click="registerMCForSite(site)" :disabled="mcRegistering[site.id]" class="px-2 py-1 text-xs bg-primary-container text-on-primary rounded hover:bg-primary">{{ mcRegistering[site.id] === 'register' ? '注册中...' : '注册MC' }}</button>
                                            </div>
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Brand Kit Detail -->
            <div v-if="currentPage === 'brand-kits-detail' && brandKitDetail" class="fade-in">
                <div class="mb-6">
                    <button @click="currentPage = 'brand-kits'; loadBrandKits()" class="text-primary hover:text-primary text-sm mb-3 inline-block">
                        <i class="fas fa-arrow-left mr-1"></i>返回列表
                    </button>
                    <div class="flex items-center justify-between">
                        <div>
                            <h2 class="page-title">{{ brandKitDetail.name }}</h2>
                            <p v-if="brandKitDetail.brand_name" class="text-on-surface-variant">{{ brandKitDetail.brand_name }}<span v-if="brandKitDetail.industry"> · {{ brandKitDetail.industry }}</span></p>
                        </div>
                        <div class="flex gap-2">
                            <button @click="handleGenerateBrandKit(brandKitDetail)" :disabled="brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running'" class="px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50 transition">
                                <i :class="['fas mr-1', (brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running') ? 'fa-spinner fa-spin' : 'fa-magic']"></i>
                                {{ (brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running') ? '生成中...' : '重新生成' }}
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Generation Progress -->
                <div v-if="brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running'" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6 mb-6">
                    <h3 class="font-semibold text-on-surface mb-4"><span class="spinner w-4 h-4 inline-block"></span>生成进度</h3>
                    <div class="space-y-3">
                        <div v-for="(step, i) in (brandKitGenerating[brandKitDetail.id].steps || [])" :key="i" class="flex items-center gap-3 px-4 py-3 rounded-lg"
                            :class="i < (brandKitGenerating[brandKitDetail.id].current || 0) ? 'bg-[#146c2e]/5' : i === (brandKitGenerating[brandKitDetail.id].current || 0) ? 'bg-blue-50' : 'bg-surface-container-low'">
                            <i v-if="i < (brandKitGenerating[brandKitDetail.id].current || 0)" class="fas fa-check-circle text-[#146c2e]"></i>
                            <i v-else-if="i === (brandKitGenerating[brandKitDetail.id].current || 0)" class="fas fa-spinner fa-spin text-primary"></i>
                            <i v-else class="fas fa-circle text-on-surface-variant text-xs"></i>
                            <span class="text-sm">{{ step.label || step }}</span>
                        </div>
                    </div>
                </div>

                <!-- Error -->
                <div v-if="brandKitDetail.status === 'failed' && brandKitDetail.error_message" class="bg-error-container border border-error/20 rounded-xl p-4 mb-6">
                    <p class="text-error text-sm"><i class="fas fa-exclamation-circle mr-2"></i>{{ brandKitDetail.error_message }}</p>
                </div>

                <!-- Tab Navigation -->
                <div class="flex border-b mb-6 gap-0">
                    <button @click="brandKitDetailTab = 'info'" :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition',
                        brandKitDetailTab === 'info' ? 'border-primary-container text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface']">
                        <i class="fas fa-info-circle mr-1"></i>品牌信息
                    </button>
                    <button @click="brandKitDetailTab = 'store'; loadBrandKitConfigForms()" :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition',
                        brandKitDetailTab === 'store' ? 'border-primary-container text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface']">
                        <i class="fas fa-store mr-1"></i>商店品牌
                    </button>
                    <button @click="brandKitDetailTab = 'footer'; loadBrandKitConfigForms()" :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition',
                        brandKitDetailTab === 'footer' ? 'border-primary-container text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface']">
                        <i class="fas fa-shoe-prints mr-1"></i>页脚配置
                    </button>
                    <button @click="brandKitDetailTab = 'taxshipping'; loadBrandKitConfigForms()" :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition',
                        brandKitDetailTab === 'taxshipping' ? 'border-primary-container text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface']">
                        <i class="fas fa-truck mr-1"></i>税费/运费
                    </button>
                    <button @click="brandKitDetailTab = 'fingerprint'" :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition',
                        brandKitDetailTab === 'fingerprint' ? 'border-primary-container text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface']">
                        <i class="fas fa-fingerprint mr-1"></i>指纹环境
                    </button>
                    <button @click="brandKitDetailTab = 'google'" :class="['px-4 py-2.5 text-sm font-medium border-b-2 transition',
                        brandKitDetailTab === 'google' ? 'border-primary-container text-primary' : 'border-transparent text-on-surface-variant hover:text-on-surface']">
                        <i class="fab fa-google mr-1"></i>谷歌账户
                    </button>
                </div>

                <!-- Tab 1: Brand Info -->
                <div v-if="brandKitDetailTab === 'info'">
                    <!-- Logo Preview -->
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6 mb-6">
                        <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-image mr-2 text-primary"></i>Logo 预览</h3>
                        <div v-if="brandKitDetail.processed_svg || brandKitDetail.raw_svg" class="bg-surface-container-low rounded-lg p-8 flex items-center justify-center" style="min-height: 200px;">
                            <div v-html="brandKitDetail.processed_svg || brandKitDetail.raw_svg" class="w-48 h-48 svg-preview"></div>
                        </div>
                        <div v-else class="text-center py-12 text-on-surface-variant">
                            <i class="fas fa-paint-brush text-4xl mb-3"></i>
                            <p>尚未生成Logo，请点击"生成"按钮</p>
                        </div>
                    </div>

                    <!-- Colors & Typography -->
                    <div v-if="(brandKitDetail.colors && brandKitDetail.colors.length) || (brandKitDetail.typography && Object.keys(brandKitDetail.typography).length)" class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                        <div v-if="brandKitDetail.colors && brandKitDetail.colors.length" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                            <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-palette mr-2 text-primary"></i>品牌色彩</h3>
                            <div class="flex flex-wrap gap-3">
                                <div v-for="(c, i) in brandKitDetail.colors" :key="i" class="flex items-center gap-2">
                                    <span class="w-10 h-10 rounded-lg border border-outline shadow-sm" :style="{ backgroundColor: c }"></span>
                                    <span class="text-sm font-mono text-on-surface-variant">{{ c }}</span>
                                </div>
                            </div>
                        </div>
                        <div v-if="brandKitDetail.typography && Object.keys(brandKitDetail.typography).length" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                            <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-font mr-2 text-primary"></i>字体排版</h3>
                            <div class="space-y-3">
                                <div v-if="brandKitDetail.typography.heading">
                                    <p class="text-xs text-on-surface-variant mb-1">标题字体</p>
                                    <p class="font-semibold text-on-surface">{{ brandKitDetail.typography.heading }}</p>
                                </div>
                                <div v-if="brandKitDetail.typography.body">
                                    <p class="text-xs text-on-surface-variant mb-1">正文字体</p>
                                    <p class="text-on-surface">{{ brandKitDetail.typography.body }}</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- CloakBrowser Profile badge -->
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6 mb-6">
                        <h3 class="font-semibold text-on-surface mb-3"><i class="fas fa-fingerprint mr-2 text-primary"></i>指纹环境</h3>
                        <div v-if="brandKitDetail.cloakbrowser_profile_name" class="flex items-center gap-3">
                            <span class="text-sm text-on-surface-variant font-mono">{{ brandKitDetail.cloakbrowser_profile_name }}</span>
                            <span class="badge bg-[#146c2e]/10 text-[#146c2e]">已绑定</span>
                            <button @click="brandKitDetailTab = 'fingerprint'" class="text-xs text-primary hover:text-primary">查看详情 →</button>
                        </div>
                        <p v-else class="text-sm text-on-surface-variant">生成品牌套件时自动创建</p>
                    </div>

                    <!-- Google Account badge -->
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6 mb-6">
                        <h3 class="font-semibold text-on-surface mb-3"><i class="fab fa-google mr-2 text-primary"></i>Google 账户</h3>
                        <div v-if="brandKitDetail.google_account_email" class="flex items-center gap-3">
                            <span class="text-sm text-on-surface-variant font-mono">{{ brandKitDetail.google_account_email }}</span>
                            <span class="badge bg-[#146c2e]/10 text-[#146c2e]">已绑定</span>
                            <button @click="brandKitDetailTab = 'google'" class="text-xs text-primary hover:text-primary">查看详情 →</button>
                        </div>
                        <div v-else-if="brandKitDetail.google_account_id" class="flex items-center gap-3">
                            <span class="text-sm text-on-surface-variant">账户 #{{ brandKitDetail.google_account_id }}</span>
                            <span class="badge bg-[#146c2e]/10 text-[#146c2e]">已绑定</span>
                            <button @click="brandKitDetailTab = 'google'" class="text-xs text-primary hover:text-primary">查看详情 →</button>
                        </div>
                        <p v-else class="text-sm text-on-surface-variant">未绑定 Google 账户 — 可在编辑套件时关联</p>
                    </div>

                    <!-- Download Files -->
                    <div v-if="brandKitDetail.status === 'ready'" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-download mr-2 text-[#146c2e]"></i>下载文件</h3>
                        <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                            <button v-if="brandKitDetail.png_256" @click="handleDownloadBrandKitFile(brandKitDetail.png_256)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-file-image text-tertiary"></i><span>PNG 256</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                            <button v-if="brandKitDetail.png_512" @click="handleDownloadBrandKitFile(brandKitDetail.png_512)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-file-image text-tertiary"></i><span>PNG 512</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                            <button v-if="brandKitDetail.png_1024" @click="handleDownloadBrandKitFile(brandKitDetail.png_1024)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-file-image text-tertiary"></i><span>PNG 1024</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                            <button v-if="brandKitDetail.ico" @click="handleDownloadBrandKitFile(brandKitDetail.ico)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-star text-yellow-500"></i><span>ICO 图标</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                            <button v-if="brandKitDetail.webp" @click="handleDownloadBrandKitFile(brandKitDetail.webp)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-file-image text-primary"></i><span>WebP</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                            <button v-if="brandKitDetail.og_image" @click="handleDownloadBrandKitFile(brandKitDetail.og_image)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-share-alt text-primary"></i><span>OG 图片</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                            <button v-if="brandKitDetail.brand_md" @click="handleDownloadBrandKitFile(brandKitDetail.brand_md)" class="flex items-center gap-2 px-4 py-3 bg-surface-container-low rounded-lg hover:bg-surface-container transition text-sm">
                                <i class="fas fa-file-alt text-on-surface-variant"></i><span>BRAND.md</span><i class="fas fa-download text-on-surface-variant ml-auto"></i>
                            </button>
                        </div>
                        <div v-if="brandKitDetail.directory" class="mt-2 text-xs text-on-surface-variant">路径: {{ brandKitDetail.directory }}</div>
                    </div>
                </div>

                <!-- Tab 2: Store Brand (网站产品) -->
                <div v-if="brandKitDetailTab === 'store'" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                    <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-store mr-2 text-primary"></i>网站产品 商店配置</h3>
                    <p class="text-xs text-on-surface-variant mb-4">此配置将在应用品牌套件时自动保存到网站产品商店</p>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs font-medium text-on-surface-variant mb-1">地址</label>
                            <input v-model="brandKitWooForm.address" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="街道地址">
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-medium text-on-surface-variant mb-1">城市</label>
                                <input v-model="brandKitWooForm.city" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="城市名称">
                            </div>
                            <div>
                                <label class="block text-xs font-medium text-on-surface-variant mb-1">邮编</label>
                                <input v-model="brandKitWooForm.postcode" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="邮政编码">
                            </div>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-on-surface-variant mb-1">国家/地区</label>
                            <input v-model="brandKitWooForm.country_state" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="例如：US:IL">
                            <p class="text-xs text-on-surface-variant mt-0.5">格式：国家代码:州代码</p>
                        </div>
                        <div>
                            <label class="block text-xs font-medium text-on-surface-variant mb-1">销售地点</label>
                            <input v-model="brandKitWooForm.allowed_countries" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="例如：US,CA">
                            <p class="text-xs text-on-surface-variant mt-0.5">默认所有国家，填写后仅限指定国家</p>
                        </div>
                    </div>
                    <button @click="saveBrandKitConfig('woo')" :disabled="brandKitConfigSaving" class="mt-4 px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50">
                        <i :class="['fas mr-1', brandKitConfigSaving ? 'fa-spinner fa-spin' : 'fa-save']"></i>
                        {{ brandKitConfigSaving ? '保存中...' : '保存商店配置' }}
                    </button>
                </div>

                <!-- Tab 3: Footer Config -->
                <div v-if="brandKitDetailTab === 'footer'" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                    <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-shoe-prints mr-2 text-tertiary"></i>页脚配置</h3>
                    <p class="text-xs text-on-surface-variant mb-4">此配置将在应用品牌套件时自动保存到网站页脚</p>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs font-medium text-on-surface-variant mb-1">地址</label>
                            <input v-model="brandKitFooterForm.address" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-orange-500" placeholder="公司地址">
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-medium text-on-surface-variant mb-1">电话</label>
                                <input v-model="brandKitFooterForm.phone" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-orange-500" placeholder="联系电话">
                            </div>
                            <div>
                                <label class="block text-xs font-medium text-on-surface-variant mb-1">邮箱</label>
                                <input v-model="brandKitFooterForm.email" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-orange-500" placeholder="联系邮箱">
                            </div>
                        </div>
                    </div>
                    <button @click="saveBrandKitConfig('footer')" :disabled="brandKitConfigSaving" class="mt-4 px-4 py-2 bg-tertiary-container text-on-primary rounded-lg text-sm hover:bg-tertiary disabled:opacity-50">
                        <i :class="['fas mr-1', brandKitConfigSaving ? 'fa-spinner fa-spin' : 'fa-save']"></i>
                        {{ brandKitConfigSaving ? '保存中...' : '保存页脚配置' }}
                    </button>
                </div>

                <!-- Tab 4: Tax & Shipping -->
                <div v-if="brandKitDetailTab === 'taxshipping'" class="space-y-6">
                    <!-- Tax config -->
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-percent mr-2 text-[#146c2e]"></i>税费配置</h3>
                        <p class="text-xs text-on-surface-variant mb-4">此配置将在应用品牌套件时自动配置到网站产品</p>
                        <div class="space-y-3">
                            <div class="flex items-center gap-3">
                                <label class="flex items-center gap-2 cursor-pointer">
                                    <input type="checkbox" v-model="brandKitTaxForm.tax_enabled" class="rounded text-[#146c2e]">
                                    <span class="text-sm text-on-surface">启用税率</span>
                                </label>
                                <label class="flex items-center gap-2 cursor-pointer">
                                    <input type="checkbox" v-model="brandKitTaxForm.prices_include_tax" class="rounded text-[#146c2e]">
                                    <span class="text-sm text-on-surface">价格含税</span>
                                </label>
                            </div>
                            <div class="border-t pt-3">
                                <p class="text-xs font-medium text-on-surface-variant mb-2">标准税率</p>
                                <div class="grid grid-cols-2 gap-3">
                                    <div>
                                        <label class="block text-xs font-medium text-on-surface-variant mb-1">税率名称</label>
                                        <input v-model="brandKitTaxForm.tax_rate_name" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-green-500" placeholder="如：US State Tax">
                                    </div>
                                    <div>
                                        <label class="block text-xs font-medium text-on-surface-variant mb-1">税率 (%)</label>
                                        <input v-model="brandKitTaxForm.tax_rate" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-green-500" placeholder="如：8.25">
                                    </div>
                                </div>
                                <div class="grid grid-cols-2 gap-3 mt-2">
                                    <div>
                                        <label class="block text-xs font-medium text-on-surface-variant mb-1">国家代码</label>
                                        <input v-model="brandKitTaxForm.tax_rate_country" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-green-500" placeholder="US">
                                    </div>
                                    <div>
                                        <label class="block text-xs font-medium text-on-surface-variant mb-1">州代码（可选）</label>
                                        <input v-model="brandKitTaxForm.tax_rate_state" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-green-500" placeholder="如：NY">
                                    </div>
                                </div>
                            </div>
                        </div>
                        <button @click="saveBrandKitConfig('tax')" :disabled="brandKitConfigSaving" class="mt-4 px-4 py-2 bg-[#146c2e] text-on-primary rounded-lg text-sm hover:bg-[#146c2e]/80 disabled:opacity-50">
                            <i :class="['fas mr-1', brandKitConfigSaving ? 'fa-spinner fa-spin' : 'fa-save']"></i>
                            {{ brandKitConfigSaving ? '保存中...' : '保存税费配置' }}
                        </button>
                    </div>

                    <!-- Shipping config -->
                    <div class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-truck mr-2 text-primary"></i>运费配置</h3>
                        <p class="text-xs text-on-surface-variant mb-4">此配置将在应用品牌套件时自动创建免费配送区域</p>
                        <div class="space-y-3">
                            <div>
                                <label class="block text-xs font-medium text-on-surface-variant mb-1">配送区域名称</label>
                                <input v-model="brandKitShippingForm.zone_name" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="Free Shipping">
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-xs font-medium text-on-surface-variant mb-1">适用国家</label>
                                    <input v-model="brandKitShippingForm.country" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="US">
                                    <p class="text-xs text-on-surface-variant mt-0.5">多个国家用逗号分隔</p>
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-on-surface-variant mb-1">最低订单金额（可选）</label>
                                    <input v-model="brandKitShippingForm.min_amount" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-primary" placeholder="留空即无条件免邮">
                                </div>
                            </div>
                        </div>
                        <button @click="saveBrandKitConfig('shipping')" :disabled="brandKitConfigSaving" class="mt-4 px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50">
                            <i :class="['fas mr-1', brandKitConfigSaving ? 'fa-spinner fa-spin' : 'fa-save']"></i>
                            {{ brandKitConfigSaving ? '保存中...' : '保存运费配置' }}
                        </button>
                    </div>
                </div>

                <!-- Tab 5: 指纹环境 -->
                <div v-if="brandKitDetailTab === 'fingerprint'" class="space-y-6">
                    <div v-if="brandKitDetail.cloakbrowser_profile_name" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-4"><i class="fas fa-fingerprint mr-2 text-primary"></i>指纹环境</h3>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">Profile 名称</p>
                                <p class="font-mono text-sm font-medium text-on-surface">{{ brandKitDetail.cloakbrowser_profile_name }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.platform">
                                <p class="text-xs text-on-surface-variant mb-1">平台</p>
                                <p class="text-sm text-on-surface">{{ {win:'Windows',mac:'macOS',linux:'Linux'}[brandKitDetail.profile_info.platform] || brandKitDetail.profile_info.platform }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.country">
                                <p class="text-xs text-on-surface-variant mb-1">国家</p>
                                <p class="text-sm text-on-surface">{{ brandKitDetail.profile_info.country }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.gpu">
                                <p class="text-xs text-on-surface-variant mb-1">GPU</p>
                                <p class="text-sm text-on-surface truncate" :title="brandKitDetail.profile_info.gpu">{{ brandKitDetail.profile_info.gpu.substring(0, 40) }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.screen">
                                <p class="text-xs text-on-surface-variant mb-1">屏幕分辨率</p>
                                <p class="text-sm text-on-surface">{{ brandKitDetail.profile_info.screen }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.timezone">
                                <p class="text-xs text-on-surface-variant mb-1">时区</p>
                                <p class="text-sm text-on-surface">{{ brandKitDetail.profile_info.timezone }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.proxy">
                                <p class="text-xs text-on-surface-variant mb-1">代理</p>
                                <p class="text-sm text-on-surface truncate" :title="brandKitDetail.profile_info.proxy">{{ brandKitDetail.profile_info.proxy.substring(0, 30) }}</p>
                            </div>
                            <div v-if="brandKitDetail.profile_info?.dir">
                                <p class="text-xs text-on-surface-variant mb-1">目录</p>
                                <p class="font-mono text-xs text-on-surface-variant truncate" :title="brandKitDetail.profile_info.dir">{{ brandKitDetail.profile_info.dir }}</p>
                            </div>
                        </div>
                        <div v-if="globalConfig.fingerprint_enabled === 'true'" class="mt-4 bg-[#146c2e]/5 border border-[#146c2e]/20 rounded-lg p-3">
                            <p class="text-[#146c2e] text-sm"><i class="fas fa-check-circle mr-2"></i>指纹环境已启用 — 建站和GMC注册将自动使用此Profile</p>
                        </div>
                        <div v-else class="mt-4 bg-yellow-50 border border-yellow-200 rounded-lg p-3">
                            <p class="text-yellow-700 text-sm"><i class="fas fa-info-circle mr-2"></i>指纹环境未启用 — 请在系统设置中开启以自动注入</p>
                        </div>
                    </div>
                    <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6 text-center py-12">
                        <i class="fas fa-fingerprint text-4xl text-on-surface-variant mb-4 block"></i>
                        <p class="text-on-surface-variant mb-2">尚未创建指纹环境</p>
                        <p class="text-sm text-on-surface-variant">请先生成品牌套件，系统将在第6步自动创建 CloakBrowser Profile</p>
                        <p v-if="brandKitDetail.proxy" class="text-xs text-on-surface-variant mt-2">代理: {{ brandKitDetail.proxy }}</p>
                        <button v-if="brandKitDetail.status !== 'ready'" @click="handleGenerateBrandKit(brandKitDetail)" :disabled="brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running'" class="mt-4 px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary disabled:opacity-50 transition">
                            <i :class="['fas mr-1', (brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running') ? 'fa-spinner fa-spin' : 'fa-magic']"></i>
                            {{ (brandKitGenerating[brandKitDetail.id] && brandKitGenerating[brandKitDetail.id].status === 'running') ? '生成中...' : '立即生成' }}
                        </button>
                    </div>
                </div>

                <!-- Tab: Google Account -->
                <div v-if="brandKitDetailTab === 'google'" class="space-y-6">
                    <div v-if="brandKitDetail.google_account_email" class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6">
                        <h3 class="font-semibold text-on-surface mb-4"><i class="fab fa-google mr-2 text-primary"></i>Google 账户</h3>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">邮箱</p>
                                <p class="text-sm font-medium text-on-surface">{{ brandKitDetail.google_account_email }}</p>
                            </div>
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">密码</p>
                                <p class="text-sm font-mono text-on-surface">{{ brandKitDetail.google_account_password || '—' }}</p>
                            </div>
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">恢复邮箱</p>
                                <p class="text-sm text-on-surface">{{ brandKitDetail.google_account_recovery || '—' }}</p>
                            </div>
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">TOTP 安全凭证 (Base32)</p>
                                <p class="text-sm font-mono text-on-surface break-all">{{ brandKitDetail.google_account_totp || '—' }}</p>
                            </div>
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">注册年份</p>
                                <p class="text-sm text-on-surface">{{ brandKitDetail.google_account_year || '—' }}</p>
                            </div>
                            <div>
                                <p class="text-xs text-on-surface-variant mb-1">国家</p>
                                <p class="text-sm text-on-surface">{{ brandKitDetail.google_account_country || '—' }}</p>
                            </div>
                        </div>
                        <div class="mt-4 bg-blue-50 border border-primary-container/20 rounded-lg p-3">
                            <p class="text-primary text-sm"><i class="fas fa-check-circle mr-2"></i>Google 账户已绑定 — GMC 自动化将使用此账户登录</p>
                        </div>
                    </div>
                    <div v-else class="bg-surface-container-lowest rounded-xl shadow-level-1 p-6 text-center py-12">
                        <i class="fab fa-google text-4xl text-on-surface-variant mb-4 block"></i>
                        <p class="text-on-surface-variant mb-2">未绑定 Google 账户</p>
                        <p class="text-sm text-on-surface-variant">可在编辑套件时关联 Google 账户，用于 GMC 自动化登录</p>
                        <button @click="openBrandKitModal(brandKitDetail)" class="mt-4 px-4 py-2 bg-primary-container text-on-primary rounded-lg text-sm hover:bg-primary transition">
                            <i class="fas fa-edit mr-1"></i>编辑套件
                        </button>
                    </div>
                </div>
            </div>
            </div>

        </main>

        <!-- Create Site Wizard Modal -->
        <div v-if="wizardOpen" class="modal-overlay modal-overlay">
            <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-3xl mx-4 max-h-[90vh] overflow-y-auto fade-in">
                <div class="p-6 border-b flex items-center justify-between">
                    <h2 class="text-lg font-bold">创建静态站点</h2>
                    <button @click="closeWizard" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button>
                </div>

                <div class="p-6 space-y-4">
                    <!-- Cloudflare DNS -->
                    <div class="bg-blue-50 border border-primary-container/20 rounded-lg p-4 mb-2"><p class="text-primary text-sm"><i class="fas fa-info-circle mr-2"></i>站点创建时将<strong>自动创建DNS解析</strong>（指向1Panel服务器，开启Cloudflare代理）。</p></div>
                    <div v-if="!cfConnected" class="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-2">
                        <p class="text-yellow-700 text-sm mb-3"><i class="fas fa-exclamation-triangle mr-2"></i>Cloudflare未授权。请在下方输入API Token或前往系统设置中配置。</p>
                        <div class="flex gap-2"><input v-model="cfToken" type="password" placeholder="输入Cloudflare API Token" class="flex-1 px-3 py-2 border rounded-lg text-sm focus:border-primary"><button @click="cfVerify" :disabled="loading" class="bg-tertiary-container text-on-primary px-4 py-2 rounded-lg text-sm hover:bg-tertiary"><i class="fas fa-check mr-1"></i>验证并保存</button></div>
                        <p class="text-xs text-on-surface-variant mt-2">Cloudflare控制台 → My Profile → API Tokens → 创建Token（需Zone:DNS:Edit权限）</p>
                    </div>
                    <div v-else class="space-y-4">
                        <div class="bg-[#146c2e]/5 border border-[#146c2e]/20 rounded-lg p-4"><p class="text-[#146c2e] text-sm"><i class="fab fa-cloudflare mr-2"></i>Cloudflare已连接 — DNS将在创建站点时自动配置</p></div>
                        <div v-if="cfAccounts.length"><label class="block text-sm font-medium text-on-surface mb-1">选择Cloudflare账号</label><select v-model="cfSelectedAccountId" class="w-full px-4 py-3 border rounded-lg focus:border-primary"><option value="">默认账号</option><option v-for="acc in cfAccounts" :key="acc.id" :value="acc.id">{{ acc.name }} <span v-if="acc.is_default" class="text-xs text-tertiary">（默认）</span></option></select><p class="text-xs text-on-surface-variant mt-1">DNS解析将使用所选账号自动创建</p></div>
                        <div v-else class="bg-surface-container-low rounded-lg p-4 text-center text-on-surface-variant"><p>暂无已保存的Cloudflare账号</p></div>
                    </div>

                    <!-- Brand Kit selection -->
                    <div class="border-t pt-4 mt-2">
                        <label class="block text-sm font-medium text-on-surface mb-1"><i class="fas fa-paint-brush mr-1 text-primary"></i>选择品牌套件（可选）</label>
                        <select v-model.number="wizardBrandKitId" class="w-full px-4 py-3 border rounded-lg focus:border-primary text-sm">
                            <option :value="null">— 不使用品牌套件 —</option>
                            <option v-for="k in brandKitsForWizard" :key="k.id" :value="k.id">{{ k.name }}{{ k.brand_name ? ' (' + k.brand_name + ')' : '' }}<span v-if="k.industry"> · {{ k.industry }}</span></option>
                        </select>
                        <p class="text-xs text-on-surface-variant mt-1">选择后将自动使用套件的品牌名、网站产品和页脚配置</p>
                        <!-- Display associated profile when fingerprint is enabled -->
                        <div v-if="fingerprintEnabled && wizardBrandKitId" class="mt-2 bg-blue-50 border border-primary-container/20 rounded-lg p-3">
                            <div class="flex items-center justify-between">
                                <p class="text-sm text-primary">
                                    <i class="fas fa-fingerprint mr-1"></i>
                                    <span v-if="brandKitsForWizard.find(k=>k.id===wizardBrandKitId)?.cloakbrowser_profile_name">
                                        指纹环境: <strong>{{ brandKitsForWizard.find(k=>k.id===wizardBrandKitId).cloakbrowser_profile_name }}</strong>
                                    </span>
                                    <span v-else class="text-primary">该套件暂未关联指纹环境</span>
                                </p>
                                <button v-if="brandKitsForWizard.find(k=>k.id===wizardBrandKitId)?.cloakbrowser_profile_name"
                                    @click="testProfileForWizard" :disabled="profileTestState.testing"
                                    class="px-3 py-1 text-xs rounded"
                                    :class="profileTestState.result === true ? 'bg-[#146c2e]/10 text-[#146c2e] border border-green-300' :
                                           profileTestState.result === false ? 'bg-error-container text-error border border-red-300' :
                                           'bg-blue-100 text-primary border border-blue-300 hover:bg-blue-200'">
                                    <i v-if="profileTestState.testing" class="fas fa-spinner fa-spin mr-1"></i>
                                    <i v-else-if="profileTestState.result === true" class="fas fa-check-circle mr-1"></i>
                                    <i v-else-if="profileTestState.result === false" class="fas fa-times-circle mr-1"></i>
                                    <i v-else class="fas fa-play mr-1"></i>
                                    {{ profileTestState.testing ? '测试中...' : profileTestState.result === true ? '已通过' : profileTestState.result === false ? '失败' : '测试环境' }}
                                </button>
                            </div>
                            <p v-if="profileTestState.result === false" class="mt-2 text-xs text-error">{{ profileTestState.message }}</p>
                            <p v-if="profileTestState.result === true" class="mt-2 text-xs text-[#146c2e]">{{ profileTestState.message }}</p>
                        </div>
                    </div>

                    <!-- Domain / Site Name -->
                    <div class="border-t pt-4 mt-2">
                        <div class="flex items-center gap-2 mb-1">
                            <label class="block text-sm font-medium text-on-surface"><span class="material-symbols-outlined">language</span>域名 / 站点名称</label>
                            <span class="text-xs text-on-surface-variant">（标签: 模板独立站）</span>
                        </div>
                        <div v-if="wizardMode === 'single'">
                            <input v-model="createForm.site_name" type="text" placeholder="例如: site1.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-primary">
                            <p class="text-xs text-on-surface-variant mt-1">将作为WordPress站点的主域名</p>
                        </div>
                        <div v-if="wizardMode === 'batch'">
                            <textarea v-model="createForm.domains" rows="5" placeholder="site1.example.com&#10;site2.example.com&#10;site3.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-primary"></textarea>
                            <p class="text-xs text-on-surface-variant mt-1">每行输入一个域名，将批量创建多个WordPress站点</p>
                        </div>
                    </div>

                    <!-- 1Panel status hint -->
                    <div v-if="!panelConnected" class="bg-error-container border border-error/20 rounded-lg p-3"><p class="text-error text-sm"><i class="fas fa-exclamation-triangle mr-2"></i>1Panel未连接，站点将仅保存到本地。</p></div>
                    <div v-else class="bg-[#146c2e]/5 border border-[#146c2e]/20 rounded-lg p-3"><p class="text-[#146c2e] text-sm"><i class="fas fa-check-circle mr-2"></i>1Panel已连接，将通过API实际安装WordPress。</p></div>
                </div>

                <!-- Wizard Footer -->
                <div class="p-6 border-t flex gap-3 justify-end">
                    <button @click="closeWizard" class="px-6 py-2 border rounded-lg hover:bg-surface-container-low">取消</button>
                    <button @click="wizardCreateSite" :disabled="loading || envTestBlocking" class="btn-primary text-on-primary px-6 py-2 rounded-lg" :title="envTestBlocking ? '请等待环境测试完成' : ''">
                        <i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>
                        <i v-else class="fas fa-rocket mr-2"></i>
                        {{ wizardMode === 'batch' ? '批量创建' : '创建站点' }}
                    </button>
                </div>
            </div>
        </div>

        <!-- Deploy Progress Overlay -->
        <div v-if="deployOverlay.show" class="modal-overlay modal-overlay">
            <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-2xl mx-4 p-6 fade-in max-h-[92vh] overflow-y-auto">

                <h3 class="text-lg font-bold mb-3"><i class="fas fa-rocket mr-2 text-primary"></i>站点部署</h3>
                <div class="mb-3">
                    <div class="flex items-center justify-between text-xs text-on-surface-variant mb-1">
                        <span>{{ deployOverlay.message }}</span>
                        <span class="font-medium">{{ currentProgressPct }}%</span>
                    </div>
                    <div class="w-full bg-surface-container-high rounded-full h-2.5 overflow-hidden">
                        <div class="bg-primary-container h-2.5 rounded-full transition-all duration-500" :style="{ width: currentProgressPct + '%' }"></div>
                    </div>
                </div>
                <div class="space-y-2 max-h-72 overflow-y-auto">
                    <div v-for="d in deployOverlay.domains" :key="d.domain" class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm"
                        :class="d.status === 'installed' ? 'bg-[#146c2e]/5' : d.status === 'failed' ? 'bg-error-container' : d.status === 'installing' || d.status === 'deploying' ? 'bg-blue-50' : 'bg-surface-container-low'">
                        <i v-if="d.status === 'installed'" class="fas fa-check-circle text-[#146c2e]"></i>
                        <i v-else-if="d.status === 'failed'" class="fas fa-times-circle text-error"></i>
                        <i v-else-if="d.status === 'pending'" class="fas fa-clock text-on-surface-variant"></i>
                        <i v-else class="fas fa-spinner fa-spin text-primary"></i>
                        <span class="flex-1 truncate font-medium" :class="d.status === 'failed' ? 'text-error' : 'text-on-surface'">{{ d.domain }}</span>
                        <span class="text-xs truncate max-w-[200px]" :class="d.status === 'failed' ? 'text-error' : 'text-on-surface-variant'">{{ d.message }}</span>
                    </div>
                </div>
                <div v-if="deployOverlay.done" class="mt-4 text-center">
                    <button @click="deployOverlay.show = false; loadSites();" class="btn-primary text-on-primary px-6 py-2 rounded-lg">
                        <i class="fas fa-check mr-2"></i>完成 — 返回站点列表
                    </button>
                </div>

            </div>
        </div>

        <!-- Edit Modal -->
        <div v-if="showEditModal" class="modal-overlay modal-overlay">
            <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 max-h-[90vh] overflow-y-auto w-full max-w-2xl mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between"><h2 class="text-lg font-bold">编辑站点</h2><button @click="showEditModal = false" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button></div>
                <div class="p-6 space-y-4">
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-on-surface mb-1">站点名称</label><input v-model="editForm.site_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div><div><label class="block text-sm font-medium text-on-surface mb-1">URL</label><input v-model="editForm.url" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-on-surface mb-1">管理员</label><input v-model="editForm.admin_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div><div><label class="block text-sm font-medium text-on-surface mb-1">管理员密码</label><input v-model="editForm.admin_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-on-surface mb-1">标签</label><input v-model="editForm.tag" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div><div><label class="block text-sm font-medium text-on-surface mb-1">安全ID</label><input v-model="editForm.security_id" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-on-surface mb-1">HTTP 用户名</label><input v-model="editForm.http_username" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div><div><label class="block text-sm font-medium text-on-surface mb-1">HTTP 密码</label><input v-model="editForm.http_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-primary"></div></div>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end"><button @click="showEditModal = false" class="px-6 py-2 border rounded-lg hover:bg-surface-container-low">取消</button><button @click="submitEdit" :disabled="loading" class="btn-primary text-on-primary px-6 py-2 rounded-lg"><i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>保存更改</button></div>
            </div>
        </div>

        <!-- Feed Product Edit Modal -->
        <div v-if="showFeedProductModal" class="modal-overlay modal-overlay">
            <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 max-h-[90vh] overflow-y-auto w-full max-w-2xl mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between"><h2 class="text-lg font-bold">{{ feedEditId ? '编辑商品' : '添加商品' }}</h2><button @click="closeFeedProductModal" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button></div>
                <div class="p-6 space-y-4">
                    <div><label class="block text-sm font-medium text-on-surface mb-1">商品标题 <span class="text-error">*</span></label><input v-model="feedEditForm.title" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                    <div><label class="block text-sm font-medium text-on-surface mb-1">描述</label><textarea v-model="feedEditForm.description" rows="2" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></textarea></div>
                    <div class="grid grid-cols-3 gap-4">
                        <div><label class="block text-sm font-medium text-on-surface mb-1">价格</label><input v-model="feedEditForm.price" placeholder="29.99 USD" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                        <div><label class="block text-sm font-medium text-on-surface mb-1">币种</label><select v-model="feedEditForm.currency" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"><option value="USD">USD</option><option value="EUR">EUR</option><option value="GBP">GBP</option><option value="CNY">CNY</option></select></div>
                        <div><label class="block text-sm font-medium text-on-surface mb-1">库存状态</label><select v-model="feedEditForm.availability" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"><option value="in_stock">有货 (in_stock)</option><option value="out_of_stock">缺货 (out_of_stock)</option><option value="preorder">预定 (preorder)</option></select></div>
                    </div>
                    <div class="grid grid-cols-3 gap-4">
                        <div><label class="block text-sm font-medium text-on-surface mb-1">品牌</label><input v-model="feedEditForm.brand" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                        <div><label class="block text-sm font-medium text-on-surface mb-1">GTIN</label><input v-model="feedEditForm.gtin" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                        <div><label class="block text-sm font-medium text-on-surface mb-1">MPN</label><input v-model="feedEditForm.mpn" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                    </div>
                    <div><label class="block text-sm font-medium text-on-surface mb-1">Google 商品类别</label><input v-model="feedEditForm.google_product_category" placeholder="Apparel & Accessories > Clothing > ..." type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                    <div class="grid grid-cols-2 gap-4">
                        <div><label class="block text-sm font-medium text-on-surface mb-1">商品类型</label><input v-model="feedEditForm.product_type" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                        <div><label class="block text-sm font-medium text-on-surface mb-1">状态</label><select v-model="feedEditForm.condition" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"><option value="new">全新 (new)</option><option value="used">二手 (used)</option><option value="refurbished">翻新 (refurbished)</option></select></div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div><label class="block text-sm font-medium text-on-surface mb-1">图片 URL</label><input v-model="feedEditForm.image_url" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                        <div><label class="block text-sm font-medium text-on-surface mb-1">商品链接</label><input v-model="feedEditForm.link" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                    </div>
                    <div><label class="block text-sm font-medium text-on-surface mb-1">运费</label><input v-model="feedEditForm.shipping" placeholder="US:0.00 USD" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-green-500"></div>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end"><button @click="closeFeedProductModal" class="px-6 py-2 border rounded-lg hover:bg-surface-container-low">取消</button><button @click="handleSaveFeedProduct" class="bg-[#146c2e] text-on-primary px-6 py-2 rounded-lg hover:bg-[#146c2e]/80"><i class="fas fa-check mr-2"></i>{{ feedEditId ? '更新' : '添加' }}</button></div>
            </div>
        </div>

        <!-- Brand Kit Create/Edit Modal -->
        <div v-if="showBrandKitModal" class="modal-overlay modal-overlay">
            <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-lg mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between">
                    <h2 class="text-lg font-bold">{{ brandKitEditId ? '编辑套件' : '创建品牌套件' }}</h2>
                    <button @click="closeBrandKitModal" class="text-on-surface-variant hover:text-on-surface-variant"><span class="material-symbols-outlined">close</span></button>
                </div>
                <div class="p-6 space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-on-surface mb-1">套件名称 <span class="text-error">*</span></label>
                        <input v-model="brandKitForm.name" type="text" placeholder="例如：我的品牌套件" class="w-full px-4 py-2 border rounded-lg focus:border-primary">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-on-surface mb-1">行业</label>
                        <select v-model="brandKitForm.industry" class="w-full px-4 py-2 border rounded-lg focus:border-primary">
                            <option value="">通用</option>
                            <option value="服装时尚">服装时尚</option>
                            <option value="电子产品">电子产品</option>
                            <option value="家居生活">家居生活</option>
                            <option value="美妆护肤">美妆护肤</option>
                            <option value="食品饮料">食品饮料</option>
                            <option value="运动户外">运动户外</option>
                            <option value="图书文具">图书文具</option>
                            <option value="母婴用品">母婴用品</option>
                            <option value="珠宝饰品">珠宝饰品</option>
                            <option value="汽车配件">汽车配件</option>
                        </select>
                    </div>
                    <!-- 指纹环境 (包含代理) — 从系统设置中同步 -->
                    <div>
                        <label class="block text-sm font-medium text-on-surface mb-1">指纹环境 <span class="text-xs text-on-surface-variant">(可选，含代理)</span></label>
                        <select v-model="brandKitForm.cloakbrowser_profile_name" @change="onProfileChange" class="w-full px-4 py-2 border rounded-lg focus:border-primary">
                            <option value="">自动创建新的指纹环境</option>
                            <option v-for="p in cloakbrowserProfiles" :key="p.name" :value="p.name" :disabled="p.bound && p.bound_kit_id !== brandKitEditId">
                                [{{ p.bound && p.bound_kit_id !== brandKitEditId ? '已绑定' : '可用' }}] {{ p.name }}
                                <template v-if="p.proxy"> — {{ typeof p.proxy === 'string' ? p.proxy.substring(0, 50) : '' }}</template>
                            </option>
                        </select>
                        <p class="text-xs text-on-surface-variant mt-1">
                            指纹环境 = CloakBrowser 指纹 + 代理，从系统设置 → 指纹环境中导入
                        </p>
                        <!-- 选中的指纹环境代理预览 -->
                        <div v-if="selectedProfileProxy" class="mt-2 px-3 py-2 bg-green-50 border border-green-200 rounded-lg text-xs text-green-800">
                            <span class="font-medium">代理:</span> {{ selectedProfileProxy }}
                        </div>
                    </div>

                    <!-- Google 账户 — 从系统设置中同步 -->
                    <div>
                        <label class="block text-sm font-medium text-on-surface mb-1">Google 账户 <span class="text-xs text-on-surface-variant">(可选)</span></label>
                        <select v-model="brandKitForm.google_account_id" class="w-full px-4 py-2 border rounded-lg focus:border-primary">
                            <option :value="null">不使用 Google 账户</option>
                            <option v-for="ga in availableGoogleAccounts" :key="ga.id" :value="ga.id" :disabled="ga.occupied_kit_id && ga.occupied_kit_id !== brandKitEditId">
                                [{{ ga.occupied_kit_name && ga.occupied_kit_id !== brandKitEditId ? '占用' : '可用' }}] {{ ga.email }} ({{ ga.country || '未知' }})
                                {{ ga.occupied_kit_name && ga.occupied_kit_id !== brandKitEditId ? ' — ' + ga.occupied_kit_name : '' }}
                            </option>
                        </select>
                        <p class="text-xs text-on-surface-variant mt-1">GMC 注册时将使用此账户自动登录 Google（支持 TOTP 2FA）。从系统设置 → 谷歌账户池中导入</p>
                    </div>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end">
                    <button @click="closeBrandKitModal" class="px-6 py-2 border rounded-lg hover:bg-surface-container-low">取消</button>
                    <button @click="handleSaveBrandKit" class="btn-primary text-on-primary px-6 py-2 rounded-lg"><i class="fas fa-check mr-2"></i>{{ brandKitEditId ? '更新' : '创建' }}</button>
                </div>
            </div>
        </div>

        <!-- GMC Task Log Viewer Modal -->
        <div v-if="taskLogVisible" class="modal-overlay modal-overlay">
            <div class="bg-gray-900 rounded-2xl shadow-level-3 w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col fade-in">
                <!-- Header -->
                <div class="flex items-center justify-between px-5 py-3 border-b border-gray-700">
                    <div class="flex items-center gap-2">
                        <i :class="['fas text-sm', taskLogStatus === 'running' ? 'fa-spinner fa-spin text-primary' : taskLogStatus === 'success' ? 'fa-check-circle text-green-400' : 'fa-times-circle text-red-400']"></i>
                        <span class="inline-flex items-center gap-xs text-on-primary font-medium text-sm">{{ taskLogTitle }}</span>
                        <span :class="['badge ml-2', taskLogStatus === 'running' ? 'bg-primary text-blue-100' : taskLogStatus === 'success' ? 'bg-green-600 text-green-100' : 'bg-error text-red-100']">
                            {{ taskLogStatus === 'running' ? '运行中' : taskLogStatus === 'success' ? '成功' : '失败' }}
                        </span>
                    </div>
                    <button @click="closeTaskLog" class="text-on-surface-variant hover:text-on-primary text-lg leading-none">&times;</button>
                </div>
                <!-- Log lines -->
                <div ref="taskLogRef" class="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed space-y-0.5" style="max-height: 55vh; background: #1a1a2e;">
                    <div v-if="taskLogLines.length === 0" class="text-on-surface-variant text-center py-8">
                        <span class="spinner w-4 h-4 inline-block"></span>等待日志...
                    </div>
                    <div v-for="line in taskLogLines" :key="line.i" class="flex gap-2">
                        <span class="text-on-surface-variant shrink-0">{{ line.t }}</span>
                        <span v-if="line.step" class="text-on-surface-variant shrink-0">[{{ line.step }}]</span>
                        <span :class="{'text-blue-300': line.level === 'info', 'text-yellow-300': line.level === 'warning', 'text-red-400': line.level === 'error', 'text-on-surface-variant': line.level !== 'info' && line.level !== 'warning' && line.level !== 'error'}">{{ line.msg }}</span>
                    </div>
                </div>
                <!-- Footer -->
                <div class="px-5 py-3 border-t border-gray-700 flex items-center justify-between">
                    <span class="text-on-surface-variant text-xs">{{ taskLogLines.length }} 条日志</span>
                    <button @click="closeTaskLog" :class="['px-4 py-1.5 rounded text-sm font-medium', taskLogStatus === 'running' ? 'bg-surface-container-highest text-on-surface-variant cursor-not-allowed' : 'bg-primary-container text-on-primary hover:bg-primary']" :disabled="taskLogStatus === 'running'">
                        {{ taskLogStatus === 'running' ? '请等待完成...' : '关闭' }}
                    </button>
                </div>
            </div>
        </div>

        <!-- Confirm Modal -->
        <div v-if="modal.show" class="modal-overlay modal-overlay">
            <div class="bg-surface-container-lowest rounded-2xl shadow-level-3 w-full max-w-md mx-4 fade-in">
                <div class="p-6"><h2 class="text-lg font-bold text-on-surface mb-2">{{ modal.title }}</h2><p class="text-on-surface-variant">{{ modal.content }}</p></div>
                <div v-if="modal.progress" class="px-6 pb-2"><p class="text-sm text-primary"><span class="spinner w-4 h-4 inline-block"></span>{{ modal.progress }}</p></div>
                <div class="p-6 border-t flex gap-3 justify-end"><button @click="modal.show = false" :disabled="modal.loading" class="px-6 py-2 border rounded-lg hover:bg-surface-container-low disabled:opacity-50">取消</button><button @click="modal.onConfirm()" :disabled="modal.loading" class="bg-error text-on-primary px-6 py-2 rounded-lg hover:bg-error disabled:opacity-50 disabled:cursor-not-allowed"><i v-if="modal.loading" class="fas fa-spinner fa-spin mr-2"></i>{{ modal.loading ? '删除中...' : '删除' }}</button></div>
            </div>
        </div>

        <!-- Toast -->
        <div v-if="toast.show" class="toast fade-in">
            <div :class="['rounded-lg shadow-level-2 px-6 py-4 flex items-center gap-3', toast.type === 'success' ? 'bg-[#146c2e] text-on-primary' : toast.type === 'error' ? 'bg-error text-on-primary' : 'bg-primary-container text-on-primary']">
                <i :class="toast.type === 'success' ? 'fas fa-check-circle' : toast.type === 'error' ? 'fas fa-exclamation-circle' : 'fas fa-info-circle'"></i><span>{{ toast.message }}</span>
            </div>
        </div>
    </div>
    `,
});

// Debug: global render error handler
app.config.errorHandler = function (err, instance, info) {
    console.error('[Vue Error]', err.message, '\n  info:', info, '\n  stack:', err.stack);
};

app.mount('#app');
