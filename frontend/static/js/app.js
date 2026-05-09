const { createApp, ref, reactive, computed, onMounted, watch, nextTick } = Vue;

const app = createApp({
    setup() {
        const isLoggedIn = ref(false);
        const currentUser = ref('');
        const currentPage = ref('dashboard');
        const loading = ref(false);
        const toast = reactive({ show: false, message: '', type: 'success' });
        const modal = reactive({ show: false, title: '', content: '', onConfirm: null });
        const loginForm = reactive({ username: 'adsadmin', password: 'Mm123567..' });
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

        // Step 2
        const themes = ref([]);
        const selectedThemeIds = ref([]);
        const selectedPluginIds = ref([]);
        const step2Installing = ref(false);
        const step2Results = ref([]);

        // Step 3
        const cfConnected = ref(false);
        const cfToken = ref('');
        const cfEmail = ref('');
        const cfKey = ref('');
        const cfAuthMode = ref('token'); // 'token' or 'global'
        const cfZones = ref([]);
        const cfSelectedZone = ref('');
        const cfProxied = ref(false);
        const cfServerIp = ref('');
        const cfCreating = ref(false);
        const cfDnsResult = ref(null);
        const cfAccounts = ref([]);
        const cfSelectedAccountId = ref('');

        // Edit
        const showEditModal = ref(false);
        const editForm = reactive({});
        const editingSiteId = ref('');

        // Plugins
        const plugins = ref([]);
        const uploadProgress = ref(0);

        // Config
        const globalConfig = reactive({
            default_admin_name: 'admin', default_admin_password: '',
            default_plugins: [], default_themes: [], db_service: 'mariadb',
        });

        // ---- Utility ----
        function showToast(message, type = 'success') {
            toast.message = message; toast.type = type; toast.show = true;
            setTimeout(() => { toast.show = false; }, 3000);
        }
        function showModal(title, content, onConfirm) {
            modal.title = title; modal.content = content; modal.onConfirm = onConfirm; modal.show = true;
        }
        function formatSize(bytes) {
            if (!bytes) return '0 B';
            const k = 1024; const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        // ---- Auth ----
        async function handleLogin() {
            loginError.value = ''; loading.value = true;
            try {
                const resp = await API.login(loginForm.username, loginForm.password);
                if (resp.code === 200) { isLoggedIn.value = true; currentUser.value = resp.data.username; showToast('登录成功'); loadInitialData(); }
                else { loginError.value = resp.message || '用户名或密码错误'; }
            } catch (e) { loginError.value = '连接错误'; } finally { loading.value = false; }
        }
        function handleLogout() { API.logout(); isLoggedIn.value = false; currentUser.value = ''; currentPage.value = 'dashboard'; }

        // ---- Data ----
        async function loadInitialData() {
            loading.value = true;
            try { await Promise.all([loadSites(), checkPanelStatus(), loadPanelData(), loadConfig(), loadPlugins(), loadThemes(), loadCfAccounts(), checkCfStatus()]); }
            finally { loading.value = false; }
        }
        async function loadSites() {
            try { const resp = await API.getSites(); if (resp.code === 200) sites.value = resp.data || []; } catch (e) {}
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
            try { const resp = await API.getConfig(); if (resp.code === 200) { Object.assign(globalConfig, resp.data); createForm.admin_name = resp.data.default_admin_name || 'admin'; createForm.db_service = resp.data.db_service || 'mariadb'; } } catch (e) {}
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
                    if (d.orphaned_wp_apps > 0) msg += `, 发现${d.orphaned_wp_apps}个未关联网站的WordPress应用`;
                    if (d.errors && d.errors.length) msg += `, ${d.errors.length}个错误`;
                    showToast(msg);
                    await loadSites();
                } else { showToast(resp.message || '同步失败', 'error'); }
            } catch (e) { showToast('同步失败', 'error'); } finally { loading.value = false; }
        }

        // ---- Plugins ----
        async function loadPlugins() {
            try { const resp = await API.getPlugins(); if (resp.code === 200) plugins.value = resp.data || []; } catch (e) {}
        }
        async function handlePluginUpload(event) {
            const file = event.target.files[0]; if (!file) return;
            if (!file.name.endsWith('.zip')) { showToast('仅支持.zip格式', 'error'); return; }
            const formData = new FormData(); formData.append('file', file); formData.append('name', file.name.replace('.zip', ''));
            try { const resp = await API.uploadPlugin(formData); if (resp.code === 200) { showToast('插件上传成功'); await loadPlugins(); } else { showToast(resp.message || '上传失败', 'error'); } } catch (e) { showToast('上传失败', 'error'); } finally { event.target.value = ''; }
        }
        async function handleDeletePlugin(plugin) {
            if (!confirm(`确定删除插件 "${plugin.name}"?`)) return;
            try { await API.deletePlugin(plugin.id); showToast('插件已删除'); await loadPlugins(); } catch (e) { showToast('删除失败', 'error'); }
        }
        async function handleTogglePlugin(plugin) {
            try { await API.togglePlugin(plugin.id); await loadPlugins(); } catch (e) { showToast('操作失败', 'error'); }
        }

        // ---- Themes ----
        async function loadThemes() {
            try { const resp = await API.getThemes(); if (resp.code === 200) themes.value = resp.data || []; } catch (e) {}
        }
        async function handleThemeUpload(event) {
            const file = event.target.files[0]; if (!file) return;
            if (!file.name.endsWith('.zip')) { showToast('仅支持.zip格式', 'error'); return; }
            const formData = new FormData(); formData.append('file', file); formData.append('name', file.name.replace('.zip', ''));
            try { const resp = await API.uploadTheme(formData); if (resp.code === 200) { showToast('主题上传成功'); await loadThemes(); } else { showToast(resp.message || '上传失败', 'error'); } } catch (e) { showToast('上传失败', 'error'); } finally { event.target.value = ''; }
        }
        async function handleDeleteTheme(theme) {
            if (!confirm(`确定删除主题 "${theme.name}"?`)) return;
            try { await API.deleteTheme(theme.id); showToast('主题已删除'); await loadThemes(); } catch (e) { showToast('删除失败', 'error'); }
        }

        // ---- Cloudflare ----
        async function loadCfAccounts() {
            try { const resp = await API.cfListAccounts(); if (resp.code === 200) cfAccounts.value = resp.data || []; } catch (e) {}
        }
        async function checkCfStatus() {
            const accountId = cfSelectedAccountId.value;
            try { const resp = await API.cfStatus(accountId || undefined); cfConnected.value = resp.data?.connected || false; } catch (e) { cfConnected.value = false; }
        }
        async function cfVerify() {
            loading.value = true;
            try {
                let resp;
                if (cfAuthMode.value === 'token') {
                    if (!cfToken.value.trim()) { showToast('请输入Cloudflare API Token', 'error'); loading.value = false; return; }
                    resp = await API.cfVerifyToken(cfToken.value.trim());
                } else {
                    if (!cfEmail.value.trim() || !cfKey.value.trim()) { showToast('请输入邮箱和Global API Key', 'error'); loading.value = false; return; }
                    resp = await API.cfVerifyGlobalKey(cfEmail.value.trim(), cfKey.value.trim());
                }
                if (resp.code === 200) { cfConnected.value = true; showToast('Cloudflare授权成功'); await loadCfAccounts(); await loadCfZones(); }
                else { showToast(resp.message || '验证失败', 'error'); }
            } catch (e) { showToast('验证失败', 'error'); } finally { loading.value = false; }
        }
        async function loadCfZones() {
            const accountId = cfSelectedAccountId.value;
            try { const resp = await API.cfListZones(accountId || undefined); if (resp.code === 200) cfZones.value = resp.data || []; } catch (e) {}
        }
        async function cfCreateDns() {
            if (!wizardSiteId.value) { showToast('请先完成站点创建', 'error'); return; }
            cfCreating.value = true;
            try {
                const data = { zone_id: cfSelectedZone.value, proxied: cfProxied.value };
                if (cfServerIp.value) data.server_ip = cfServerIp.value;
                if (cfSelectedAccountId.value) data.account_id = cfSelectedAccountId.value;
                const resp = await API.cfCreateDns(wizardSiteId.value, data);
                if (resp.code === 200) { cfDnsResult.value = resp.data; showToast('DNS A记录创建成功！'); await loadSites(); }
                else { showToast(resp.message || 'DNS创建失败', 'error'); }
            } catch (e) { showToast('DNS创建失败', 'error'); } finally { cfCreating.value = false; }
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

        // ---- 3-Step Wizard ----
        function openWizard(mode = 'single') {
            wizardMode.value = mode; wizardStep.value = 1; wizardSiteId.value = null;
            createForm.site_name = ''; createForm.url = ''; createForm.admin_name = globalConfig.default_admin_name || 'admin';
            createForm.admin_password = globalConfig.default_admin_password || ''; createForm.tag = ''; createForm.security_id = '';
            createForm.http_username = ''; createForm.http_password = ''; createForm.verify_certificate = true; createForm.ssl_version = 'auto';
            createForm.domains = ''; createForm.base_port = 8081;
            createProgress.show = false; createProgress.results = [];
            selectedThemeIds.value = []; selectedPluginIds.value = []; step2Results.value = [];
            cfDnsResult.value = null; cfSelectedAccountId.value = '';
            wizardOpen.value = true;
        }
        function closeWizard() { wizardOpen.value = false; loadSites(); loadPanelData(); }

        // Step 1: Create site(s)
        async function wizardCreateSite() {
            loading.value = true;
            try {
                const isBatch = wizardMode.value === 'batch';
                let domains = [];
                if (isBatch) {
                    domains = createForm.domains.split('\n').map(d => d.trim()).filter(d => d);
                    if (!domains.length) { showToast('请至少输入一个域名', 'error'); loading.value = false; return; }
                } else {
                    const domain = createForm.site_name.trim();
                    if (!domain) { showToast('请输入域名', 'error'); loading.value = false; return; }
                    domains = [domain];
                }
                createProgress.show = true;
                createProgress.results = [];
                createProgress.message = `正在通过1Panel部署 ${domains.length} 个WordPress站点...`;
                const resp = await API.batchCreateWordPress({
                    domains: domains, admin_name: createForm.admin_name, admin_password: createForm.admin_password,
                    tag: createForm.tag, security_id: createForm.security_id, http_username: createForm.http_username,
                    http_password: createForm.http_password, verify_certificate: createForm.verify_certificate,
                    ssl_version: createForm.ssl_version, base_port: createForm.base_port, db_service: createForm.db_service,
                    website_group_id: createForm.website_group_id || 1,
                });
                if (resp.code !== 200) { createProgress.message = `创建失败: ${resp.message}`; showToast(`创建失败: ${resp.message}`, 'error'); loading.value = false; return; }
                const results = resp.data.results || [];
                if (isBatch) {
                    // Batch mode: show summary
                    const ok = results.filter(r => r.status !== 'error').length;
                    const err = results.filter(r => r.status === 'error').length;
                    createProgress.results = results;
                    createProgress.message = `批量创建完成: ${ok} 成功, ${err} 失败`;
                    showToast(`批量创建完成: ${ok}/${results.length} 成功`);
                    await loadSites();
                    loading.value = false;
                    return; // Batch done, wizard stays open for user to review
                } else {
                    // Single mode: poll WP install status
                    const result = results[0];
                    if (result && result.status === 'error') { createProgress.message = `创建失败: ${result.message}`; showToast(result.message, 'error'); loading.value = false; return; }
                    wizardSiteId.value = result.site_id;
                    if (result.site_id && result.wp_install_status === 'installing') { startWPPolling(result.site_id, domains[0]); createProgress.message = `WordPress正在安装中...`; }
                    for (let i = 0; i < 48; i++) { await new Promise(r => setTimeout(r, 5000)); const s = wpInstallStatuses[result.site_id]; if (s && (s.status === 'installed' || s.status === 'failed')) break; }
                    const fs = wpInstallStatuses[result.site_id];
                    if (fs && fs.status === 'installed') { createProgress.message = `✅ 站点 ${domains[0]} 部署完成！`; showToast(`站点 ${domains[0]} 部署完成！`); }
                    else if (fs && fs.status === 'failed') { createProgress.message = `⚠️ 站点已创建，但WordPress安装未完成: ${fs.message}`; }
                    else { createProgress.message = `⏳ 站点部署已提交，1Panel正在处理...`; }
                    await loadSites();
                    setTimeout(() => { wizardStep.value = 2; }, 1000);
                }
            } catch (e) { createProgress.message = `创建失败: ${e.message}`; showToast(`错误: ${e.message}`, 'error'); } finally { loading.value = false; }
        }

        // Step 2: Install theme & plugins
        async function wizardInstallThemeAndPlugins() {
            if (!selectedThemeIds.value.length && !selectedPluginIds.value.length) { showToast('请至少选择一个主题或插件，或跳过此步骤', 'error'); return; }
            step2Installing.value = true; step2Results.value = [];
            try {
                if (selectedThemeIds.value.length) {
                    const resp = await API.installTheme(wizardSiteId.value, selectedThemeIds.value);
                    if (resp.code === 200) step2Results.value.push(...(resp.data.results || []));
                    else step2Results.value.push({ theme: '主题', status: 'error', message: resp.message });
                }
                if (selectedPluginIds.value.length) {
                    const resp = await API.installPlugins(wizardSiteId.value, selectedPluginIds.value);
                    if (resp.code === 200) step2Results.value.push(...(resp.data.results || []));
                    else step2Results.value.push({ plugin: '插件', status: 'error', message: resp.message });
                }
                const sc = step2Results.value.filter(r => r.status === 'success').length;
                showToast(`安装完成: ${sc}/${step2Results.value.length} 成功`);
            } catch (e) { showToast('安装失败: ' + e.message, 'error'); } finally { step2Installing.value = false; }
        }
        function wizardSkipStep2() { wizardStep.value = 3; }
        function wizardNextStep2() { wizardStep.value = 3; }
        function wizardFinish() { closeWizard(); }

        // ---- WP Polling ----
        function startWPPolling(siteId, domain) {
            if (wpPollingTimers[siteId]) return;
            wpInstallStatuses[siteId] = { status: 'installing', message: '1Panel正在创建数据库...', domain };
            const timer = setInterval(async () => {
                try {
                    const resp = await API.getWPInstallStatus(siteId);
                    if (resp.code === 200 && resp.data) { wpInstallStatuses[siteId] = { ...resp.data, domain }; if (resp.data.status === 'installed' || resp.data.status === 'failed') { stopWPPolling(siteId); await loadSites(); } }
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
            showModal('删除站点', `确定要删除 "${site.site_name}" 吗？${site.panel_website_id ? '同时从1Panel删除WordPress应用和网站。' : ''}此操作不可撤销。`,
                async () => {
                    loading.value = true;
                    try { if (site.panel_website_id && panelConnected.value) await API.panelDeleteWebsite(site.panel_website_id, true); await API.deleteSite(site.id); showToast('站点已删除'); await loadSites(); await loadPanelData(); } catch (e) { showToast('删除失败', 'error'); } finally { loading.value = false; }
                    modal.show = false;
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

        function exportCSV() { API.exportCSV(); showToast('CSV文件已导出'); }
        async function saveGlobalConfig() {
            loading.value = true;
            try { await API.saveConfig(globalConfig); showToast('配置已保存'); } catch (e) { showToast('保存配置失败', 'error'); } finally { loading.value = false; }
        }

        onMounted(async () => {
            if (API.token) { try { const resp = await API.checkAuth(); if (resp.code === 200) { isLoggedIn.value = true; currentUser.value = resp.data.username; await loadInitialData(); } } catch (e) { API.logout(); } }
        });

        return {
            isLoggedIn, currentUser, currentPage, loading, toast, modal,
            loginForm, loginError, sites, searchQuery, filteredSites,
            panelConnected, panelWebsites, panelInstalledApps, panelGroups,
            wizardStep, wizardOpen, wizardMode, wizardSiteId,
            createForm, createProgress, wpInstallStatuses,
            themes, selectedThemeIds, selectedPluginIds, step2Installing, step2Results,
            cfConnected, cfToken, cfEmail, cfKey, cfAuthMode, cfZones, cfSelectedZone, cfProxied, cfServerIp, cfCreating, cfDnsResult,
            cfAccounts, cfSelectedAccountId,
            showEditModal, editForm, editingSiteId, globalConfig,
            plugins, uploadProgress, formatSize,
            handleLogin, handleLogout, refreshSites, syncWithPanel,
            openWizard, closeWizard, wizardCreateSite,
            wizardInstallThemeAndPlugins, wizardSkipStep2, wizardNextStep2, wizardFinish,
            openEditModal, submitEdit, confirmDelete, fixSiteWebsite, saveGlobalConfig, exportCSV,
            loadPlugins, handlePluginUpload, handleDeletePlugin, handleTogglePlugin,
            loadThemes, handleThemeUpload, handleDeleteTheme,
            cfVerify, loadCfZones, cfCreateDns, loadCfAccounts, handleDeleteCfAccount, handleSetDefaultCfAccount,
            showToast, showModal,
        };
    },

    template: `
    <!-- Login -->
    <div v-if="!isLoggedIn" class="min-h-screen flex items-center justify-center login-bg">
        <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md fade-in">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-2xl flex items-center justify-center mx-auto mb-4"><i class="fab fa-wordpress text-white text-3xl"></i></div>
                <h1 class="text-2xl font-bold text-gray-800">WordPress 站点管理</h1><p class="text-gray-500 mt-2">登录以管理您的WordPress站点</p>
            </div>
            <form @submit.prevent="handleLogin">
                <div class="mb-4"><label class="block text-sm font-medium text-gray-700 mb-1">用户名</label><input v-model="loginForm.username" type="text" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:border-indigo-500" placeholder="请输入用户名"></div>
                <div class="mb-6"><label class="block text-sm font-medium text-gray-700 mb-1">密码</label><input v-model="loginForm.password" type="password" required class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:border-indigo-500" placeholder="请输入密码"></div>
                <p v-if="loginError" class="text-red-500 text-sm mb-4">{{ loginError }}</p>
                <button type="submit" :disabled="loading" class="w-full btn-primary text-white py-3 rounded-lg font-semibold hover:shadow-lg transition"><i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i><span v-else>登 录</span></button>
            </form>
        </div>
    </div>

    <!-- Main App -->
    <div v-else class="min-h-screen flex">
        <!-- Sidebar -->
        <aside class="w-64 sidebar-gradient text-white flex flex-col">
            <div class="p-6 border-b border-indigo-700"><div class="flex items-center gap-3"><div class="w-10 h-10 bg-white bg-opacity-20 rounded-lg flex items-center justify-center"><i class="fab fa-wordpress text-xl"></i></div><div><h2 class="font-bold text-lg">WP 管理器</h2><p class="text-xs text-indigo-300">站点管理平台</p></div></div></div>
            <nav class="flex-1 p-4 space-y-1">
                <a @click="currentPage = 'dashboard'" :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'dashboard' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']"><i class="fas fa-tachometer-alt w-5 text-center"></i><span>仪表盘</span></a>
                <a @click="currentPage = 'sites'" :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'sites' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']"><i class="fas fa-globe w-5 text-center"></i><span>站点列表</span><span class="ml-auto bg-indigo-500 text-xs px-2 py-0.5 rounded-full">{{ sites.length }}</span></a>
                <a @click="openWizard('single')" class="flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition hover:bg-white hover:bg-opacity-10"><i class="fas fa-plus-circle w-5 text-center"></i><span>创建站点</span></a>
                <a @click="currentPage = 'plugins'" :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'plugins' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']"><i class="fas fa-plug w-5 text-center"></i><span>插件管理</span><span class="ml-auto bg-indigo-500 text-xs px-2 py-0.5 rounded-full">{{ plugins.length }}</span></a>
                <a @click="currentPage = 'themes'" :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'themes' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']"><i class="fas fa-palette w-5 text-center"></i><span>主题管理</span><span class="ml-auto bg-indigo-500 text-xs px-2 py-0.5 rounded-full">{{ themes.length }}</span></a>
                <a @click="currentPage = 'settings'" :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'settings' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']"><i class="fas fa-cog w-5 text-center"></i><span>系统设置</span></a>
            </nav>
            <div class="p-4 border-t border-indigo-700"><div class="flex items-center gap-3 px-2"><div class="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center"><i class="fas fa-user text-sm"></i></div><div class="flex-1 min-w-0"><p class="text-sm font-medium truncate">{{ currentUser }}</p><p class="text-xs text-indigo-300">管理员</p></div><button @click="handleLogout" class="text-indigo-300 hover:text-white" title="退出登录"><i class="fas fa-sign-out-alt"></i></button></div></div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 overflow-auto">
            <header class="bg-white border-b px-8 py-4 flex items-center justify-between">
                <div><h1 class="text-xl font-bold text-gray-800">{{ currentPage === 'dashboard' ? '仪表盘' : currentPage === 'sites' ? '站点列表' : currentPage === 'plugins' ? '插件管理' : currentPage === 'themes' ? '主题管理' : '系统设置' }}</h1><p class="text-sm text-gray-500"><span :class="panelConnected ? 'text-green-500' : 'text-red-500'"><i class="fas fa-circle text-xs mr-1"></i>{{ panelConnected ? '1Panel 已连接' : '1Panel 未连接' }}</span></p></div>
                <button @click="syncWithPanel" class="px-4 py-2 bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 transition text-sm" title="从1Panel同步数据"><i class="fas fa-exchange-alt mr-2"></i>同步1Panel</button>
                <button @click="refreshSites" class="px-4 py-2 border rounded-lg hover:bg-gray-50 transition text-sm"><i class="fas fa-sync-alt mr-2" :class="{'fa-spin': loading}"></i>刷新</button>
            </header>

            <!-- Dashboard -->
            <div v-if="currentPage === 'dashboard'" class="p-8 fade-in">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">站点总数</p><p class="text-3xl font-bold text-gray-800 mt-1">{{ sites.length }}</p></div><div class="w-12 h-12 bg-indigo-100 rounded-lg flex items-center justify-center"><i class="fas fa-globe text-indigo-600 text-xl"></i></div></div></div>
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">1Panel连接</p><p class="text-3xl font-bold mt-1" :class="panelConnected ? 'text-green-600' : 'text-red-600'">{{ panelConnected ? '正常' : '断开' }}</p></div><div class="w-12 h-12 rounded-lg flex items-center justify-center" :class="panelConnected ? 'bg-green-100' : 'bg-red-100'"><i class="fas fa-server text-xl" :class="panelConnected ? 'text-green-600' : 'text-red-600'"></i></div></div></div>
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">已装插件</p><p class="text-3xl font-bold text-gray-800 mt-1">{{ plugins.length }}</p></div><div class="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center"><i class="fas fa-plug text-purple-600 text-xl"></i></div></div></div>
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">已装主题</p><p class="text-3xl font-bold text-gray-800 mt-1">{{ themes.length }}</p></div><div class="w-12 h-12 bg-orange-100 rounded-lg flex items-center justify-center"><i class="fas fa-palette text-orange-600 text-xl"></i></div></div></div>
                </div>
                <div class="bg-white rounded-xl card-shadow p-6">
                    <h3 class="font-semibold text-gray-800 mb-4"><i class="fas fa-bolt mr-2 text-indigo-500"></i>快速操作</h3>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <button @click="openWizard('single')" class="p-4 border-2 border-dashed border-indigo-200 rounded-xl hover:border-indigo-400 hover:bg-indigo-50 transition text-center"><i class="fas fa-plus-circle text-2xl text-indigo-500 mb-2"></i><p class="text-sm font-medium text-gray-700">创建单个站点</p></button>
                        <button @click="openWizard('batch')" class="p-4 border-2 border-dashed border-purple-200 rounded-xl hover:border-purple-400 hover:bg-purple-50 transition text-center"><i class="fas fa-layer-group text-2xl text-purple-500 mb-2"></i><p class="text-sm font-medium text-gray-700">批量创建站点</p></button>
                        <button @click="currentPage = 'plugins'" class="p-4 border-2 border-dashed border-blue-200 rounded-xl hover:border-blue-400 hover:bg-blue-50 transition text-center"><i class="fas fa-plug text-2xl text-blue-500 mb-2"></i><p class="text-sm font-medium text-gray-700">管理插件</p></button>
                        <button @click="exportCSV" class="p-4 border-2 border-dashed border-green-200 rounded-xl hover:border-green-400 hover:bg-green-50 transition text-center"><i class="fas fa-file-csv text-2xl text-green-500 mb-2"></i><p class="text-sm font-medium text-gray-700">导出CSV</p></button>
                    </div>
                </div>
            </div>

            <!-- Sites List -->
            <div v-if="currentPage === 'sites'" class="p-8 fade-in">
                <div class="flex items-center justify-between mb-6">
                    <div class="relative"><i class="fas fa-search absolute left-3 top-3 text-gray-400"></i><input v-model="searchQuery" type="text" placeholder="搜索站点..." class="pl-10 pr-4 py-2 border rounded-lg focus:border-indigo-500 w-64"></div>
                    <div class="flex gap-3"><button @click="exportCSV" class="px-4 py-2 border rounded-lg hover:bg-gray-50 text-sm"><i class="fas fa-download mr-2"></i>导出CSV</button></div>
                </div>
                <div class="bg-white rounded-xl card-shadow overflow-hidden">
                    <div v-if="!filteredSites.length" class="p-12 text-center text-gray-400"><i class="fas fa-inbox text-4xl mb-4"></i><p>暂无站点，点击"创建站点"开始</p></div>
                    <div v-else class="overflow-x-auto">
                        <table class="w-full text-sm">
                            <thead class="bg-gray-50"><tr><th class="px-6 py-3 text-left font-medium text-gray-600">站点</th><th class="px-6 py-3 text-left font-medium text-gray-600">URL</th><th class="px-6 py-3 text-left font-medium text-gray-600">标签</th><th class="px-6 py-3 text-left font-medium text-gray-600">端口</th><th class="px-6 py-3 text-left font-medium text-gray-600">1Panel</th><th class="px-6 py-3 text-left font-medium text-gray-600">DNS</th><th class="px-6 py-3 text-right font-medium text-gray-600">操作</th></tr></thead>
                            <tbody class="divide-y">
                                <tr v-for="site in filteredSites" :key="site.id" class="hover:bg-gray-50">
                                    <td class="px-6 py-4"><div class="font-medium text-gray-800">{{ site.site_name }}</div><div class="text-xs text-gray-500">{{ site.admin_name || '-' }}</div></td>
                                    <td class="px-6 py-4"><a :href="site.url" target="_blank" class="text-indigo-600 hover:text-indigo-800">{{ site.url }}</a></td>
                                    <td class="px-6 py-4"><span v-if="site.tag" class="bg-indigo-100 text-indigo-700 text-xs px-2 py-1 rounded-full">{{ site.tag }}</span><span v-else class="text-gray-400">-</span></td>
                                    <td class="px-6 py-4 text-gray-600">{{ site.port || '-' }}</td>
                                    <td class="px-6 py-4"><span v-if="site.panel_website_id" :class="[site.panel_status === 'Running' ? 'bg-green-100 text-green-700' : site.panel_status === 'deleted' ? 'bg-red-100 text-red-700' : 'bg-yellow-100 text-yellow-700']" class="text-xs px-2 py-1 rounded-full"><i :class="[site.panel_status === 'Running' ? 'fas fa-check-circle' : site.panel_status === 'deleted' ? 'fas fa-times-circle' : 'fas fa-exclamation-circle']" class="mr-1"></i>{{ site.panel_status === 'Running' ? '正常' : site.panel_status === 'deleted' ? '已删除' : site.panel_status || '未知' }}</span><span v-else class="text-gray-400 text-xs">未关联</span></td>
                                    <td class="px-6 py-4"><span v-if="site.cf_dns_record_id" class="bg-orange-100 text-orange-700 text-xs px-2 py-1 rounded-full"><i class="fab fa-cloudflare mr-1"></i>CF</span><span v-else class="text-gray-400">-</span></td>
                                    <td class="px-6 py-4 text-right"><div class="flex items-center justify-end gap-2"><button @click="openEditModal(site)" class="text-indigo-500 hover:text-indigo-700" title="编辑"><i class="fas fa-edit"></i></button><button v-if="site.panel_app_install_id && (!site.panel_website_id || site.panel_status === 'deleted')" @click="fixSiteWebsite(site)" class="text-orange-500 hover:text-orange-700" title="修复1Panel网站"><i class="fas fa-wrench"></i></button><button @click="confirmDelete(site)" class="text-red-400 hover:text-red-600" title="删除"><i class="fas fa-trash"></i></button></div></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Plugins -->
            <div v-if="currentPage === 'plugins'" class="p-8 fade-in">
                <div class="bg-white rounded-xl card-shadow overflow-hidden">
                    <div class="p-6 border-b flex items-center justify-between">
                        <h3 class="font-semibold text-gray-800"><i class="fas fa-plug mr-2 text-indigo-500"></i>插件库</h3>
                        <label class="btn-primary text-white px-4 py-2 rounded-lg cursor-pointer text-sm"><i class="fas fa-upload mr-2"></i>上传插件<input type="file" accept=".zip" @change="handlePluginUpload" class="hidden"></label>
                    </div>
                    <div v-if="!plugins.length" class="p-12 text-center text-gray-400"><i class="fas fa-puzzle-piece text-4xl mb-4"></i><p>暂无插件，请上传WordPress插件的.zip文件</p></div>
                    <div v-else class="divide-y">
                        <div v-for="p in plugins" :key="p.id" class="px-6 py-4 flex items-center gap-4 hover:bg-gray-50 transition">
                            <div class="w-10 h-10 rounded-lg flex items-center justify-center" :class="p.enabled ? 'bg-indigo-100' : 'bg-gray-100'"><i class="fas fa-puzzle-piece" :class="p.enabled ? 'text-indigo-600' : 'text-gray-400'"></i></div>
                            <div class="flex-1 min-w-0"><p class="font-medium text-sm">{{ p.name }}</p><p class="text-xs text-gray-500">{{ p.filename }} · {{ formatSize(p.file_size) }}</p></div>
                            <div class="flex items-center gap-3"><button @click="handleTogglePlugin(p)" :class="p.enabled ? 'text-green-600 hover:text-green-800' : 'text-gray-400 hover:text-gray-600'" :title="p.enabled ? '点击禁用' : '点击启用'"><i :class="p.enabled ? 'fas fa-toggle-on text-xl' : 'fas fa-toggle-off text-xl'"></i></button><button @click="handleDeletePlugin(p)" class="text-red-400 hover:text-red-600" title="删除"><i class="fas fa-trash"></i></button></div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Themes -->
            <div v-if="currentPage === 'themes'" class="p-8 fade-in">
                <div class="bg-white rounded-xl card-shadow overflow-hidden">
                    <div class="p-6 border-b flex items-center justify-between">
                        <h3 class="font-semibold text-gray-800"><i class="fas fa-palette mr-2 text-orange-500"></i>主题库</h3>
                        <label class="px-4 py-2 bg-orange-500 text-white rounded-lg cursor-pointer text-sm hover:bg-orange-600 transition"><i class="fas fa-upload mr-2"></i>上传主题<input type="file" accept=".zip" @change="handleThemeUpload" class="hidden"></label>
                    </div>
                    <div v-if="!themes.length" class="p-12 text-center text-gray-400"><i class="fas fa-palette text-4xl mb-4"></i><p>暂无主题，请上传WordPress主题的.zip文件</p></div>
                    <div v-else class="divide-y">
                        <div v-for="t in themes" :key="t.id" class="px-6 py-4 flex items-center gap-4 hover:bg-gray-50 transition">
                            <div class="w-10 h-10 rounded-lg flex items-center justify-center bg-orange-100"><i class="fas fa-palette text-orange-600"></i></div>
                            <div class="flex-1 min-w-0"><p class="font-medium text-sm">{{ t.name }}</p><p class="text-xs text-gray-500">{{ t.filename }} · {{ formatSize(t.file_size) }}</p></div>
                            <button @click="handleDeleteTheme(t)" class="text-red-400 hover:text-red-600" title="删除"><i class="fas fa-trash"></i></button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Settings -->
            <div v-if="currentPage === 'settings'" class="p-8 fade-in">
                <div class="max-w-3xl mx-auto space-y-6">
                    <div class="bg-white rounded-xl card-shadow p-6">
                        <h3 class="font-semibold text-gray-800 mb-4"><i class="fas fa-sliders-h mr-2 text-indigo-500"></i>默认WordPress配置</h3>
                        <div class="space-y-4">
                            <div><label class="block text-sm font-medium text-gray-700 mb-1">默认管理员用户名</label><input v-model="globalConfig.default_admin_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div>
                            <div><label class="block text-sm font-medium text-gray-700 mb-1">默认管理员密码</label><input v-model="globalConfig.default_admin_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"><p class="text-xs text-gray-500 mt-1">应用于所有新创建的WordPress站点</p></div>
                            <div><label class="block text-sm font-medium text-gray-700 mb-1">默认数据库服务</label><select v-model="globalConfig.db_service" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"><option value="mariadb">MariaDB</option><option value="mysql">MySQL</option></select></div>
                        </div>
                    </div>
                    <div class="bg-white rounded-xl card-shadow p-6">
                        <h3 class="font-semibold text-gray-800 mb-4"><i class="fab fa-cloudflare mr-2 text-orange-500"></i>Cloudflare 配置</h3>
                        <div class="space-y-4">
                            <!-- Saved accounts list -->
                            <div v-if="cfAccounts.length" class="bg-gray-50 rounded-lg p-3">
                                <h4 class="text-sm font-semibold text-gray-700 mb-2">已保存的账号</h4>
                                <div v-for="acc in cfAccounts" :key="acc.id" class="flex items-center justify-between py-2 border-b border-gray-200 last:border-b-0">
                                    <div class="flex items-center gap-2">
                                        <i class="fas fa-cloud text-orange-500"></i>
                                        <span class="text-sm font-medium">{{ acc.name }}</span>
                                        <span v-if="acc.is_default" class="text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full">默认</span>
                                        <span class="text-xs text-gray-400 pl-2">{{ acc.auth_type === 'global' ? acc.api_email : 'API Token' }}</span>
                                    </div>
                                    <div class="flex gap-1">
                                        <button v-if="!acc.is_default" @click="handleSetDefaultCfAccount(acc.id)" class="text-xs text-gray-400 hover:text-orange-500 px-2 py-1" title="设为默认"><i class="fas fa-star"></i></button>
                                        <button @click="handleDeleteCfAccount(acc.id)" class="text-xs text-gray-400 hover:text-red-500 px-2 py-1" title="删除"><i class="fas fa-trash"></i></button>
                                    </div>
                                </div>
                            </div>
                            <div class="flex items-center gap-3 mb-2"><span :class="cfConnected ? 'text-green-500' : 'text-red-500'"><i class="fas fa-circle text-xs mr-1"></i>{{ cfConnected ? '已连接' : '未连接' }}</span></div>
                            <div class="flex gap-2 mb-3">
                                <button @click="cfAuthMode='token'" :class="cfAuthMode==='token' ? 'bg-orange-500 text-white' : 'bg-gray-100 text-gray-600'" class="px-3 py-1.5 rounded-lg text-sm font-medium">API Token</button>
                                <button @click="cfAuthMode='global'" :class="cfAuthMode==='global' ? 'bg-orange-500 text-white' : 'bg-gray-100 text-gray-600'" class="px-3 py-1.5 rounded-lg text-sm font-medium">Global API Key</button>
                            </div>
                            <div v-if="cfAuthMode==='token'">
                                <label class="block text-sm font-medium text-gray-700 mb-1">API Token</label>
                                <div class="flex gap-2"><input v-model="cfToken" type="password" placeholder="输入Cloudflare API Token" class="flex-1 px-4 py-2 border rounded-lg focus:border-indigo-500"><button @click="cfVerify" :disabled="loading" class="btn-primary text-white px-4 py-2 rounded-lg"><i class="fas fa-check mr-2"></i>验证并保存</button></div>
                                <p class="text-xs text-gray-500 mt-1">Cloudflare控制台 → My Profile → API Tokens → 创建Token（需Zone:DNS:Edit权限）</p>
                            </div>
                            <div v-if="cfAuthMode==='global'">
                                <label class="block text-sm font-medium text-gray-700 mb-1">邮箱</label>
                                <input v-model="cfEmail" type="email" placeholder="Cloudflare账户邮箱" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500 mb-2">
                                <label class="block text-sm font-medium text-gray-700 mb-1">Global API Key</label>
                                <div class="flex gap-2"><input v-model="cfKey" type="password" placeholder="输入Global API Key" class="flex-1 px-4 py-2 border rounded-lg focus:border-indigo-500"><button @click="cfVerify" :disabled="loading" class="btn-primary text-white px-4 py-2 rounded-lg"><i class="fas fa-check mr-2"></i>验证并保存</button></div>
                                <p class="text-xs text-gray-500 mt-1">Cloudflare控制台 → My Profile → API Tokens → Global API Key</p>
                            </div>
                        </div>
                    </div>
                    <button @click="saveGlobalConfig" :disabled="loading" class="w-full btn-primary text-white py-3 rounded-lg font-semibold"><i class="fas fa-save mr-2"></i>保存设置</button>
                </div>
            </div>
        </main>

        <!-- 3-Step Wizard Modal -->
        <div v-if="wizardOpen" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl w-full max-w-3xl mx-4 max-h-[90vh] overflow-y-auto fade-in">
                <div class="p-6 border-b">
                    <div class="flex items-center justify-between mb-4"><h2 class="text-lg font-bold">创建WordPress站点</h2><button @click="closeWizard" class="text-gray-400 hover:text-gray-600"><i class="fas fa-times text-xl"></i></button></div>
                    <div class="flex items-center">
                        <div class="flex items-center" :class="wizardStep >= 1 ? 'text-indigo-600' : 'text-gray-400'"><div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" :class="wizardStep >= 1 ? 'bg-indigo-600 text-white' : 'bg-gray-200'">1</div><span class="ml-2 text-sm font-medium">站点设置</span></div>
                        <div class="flex-1 h-0.5 mx-3" :class="wizardStep >= 2 ? 'bg-indigo-600' : 'bg-gray-200'"></div>
                        <div class="flex items-center" :class="wizardStep >= 2 ? 'text-indigo-600' : 'text-gray-400'"><div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" :class="wizardStep >= 2 ? 'bg-indigo-600 text-white' : 'bg-gray-200'">2</div><span class="ml-2 text-sm font-medium">主题 & 插件</span></div>
                        <div class="flex-1 h-0.5 mx-3" :class="wizardStep >= 3 ? 'bg-indigo-600' : 'bg-gray-200'"></div>
                        <div class="flex items-center" :class="wizardStep >= 3 ? 'text-indigo-600' : 'text-gray-400'"><div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold" :class="wizardStep >= 3 ? 'bg-indigo-600 text-white' : 'bg-gray-200'">3</div><span class="ml-2 text-sm font-medium">DNS解析</span></div>
                    </div>
                </div>

                <!-- Step 1 -->
                <div v-if="wizardStep === 1" class="p-6 space-y-4">
                    <div v-if="!panelConnected" class="bg-red-50 border border-red-200 rounded-lg p-4"><p class="text-red-700 text-sm"><i class="fas fa-exclamation-triangle mr-2"></i>1Panel未连接，站点将仅保存到本地。</p></div>
                    <div v-else class="bg-green-50 border border-green-200 rounded-lg p-4"><p class="text-green-700 text-sm"><i class="fas fa-check-circle mr-2"></i>1Panel已连接，将通过API实际安装WordPress。</p></div>
                    <!-- Single mode: one domain -->
                    <div v-if="wizardMode === 'single'"><label class="block text-sm font-medium text-gray-700 mb-1">域名 / 站点名称</label><input v-model="createForm.site_name" type="text" placeholder="例如: site1.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"><p class="text-xs text-gray-500 mt-1">将作为WordPress站点的主域名</p></div>
                    <!-- Batch mode: multiple domains -->
                    <div v-if="wizardMode === 'batch'"><label class="block text-sm font-medium text-gray-700 mb-1">域名列表（每行一个）</label><textarea v-model="createForm.domains" rows="6" placeholder="site1.example.com&#10;site2.example.com&#10;site3.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></textarea><p class="text-xs text-gray-500 mt-1">每行输入一个域名，将批量创建多个WordPress站点</p></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">WP 管理员用户名</label><input v-model="createForm.admin_name" type="text" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">WP 管理员密码</label><input v-model="createForm.admin_password" type="text" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">标签</label><input v-model="createForm.tag" type="text" placeholder="例如: 生产环境" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">安全ID</label><input v-model="createForm.security_id" type="text" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="bg-gray-50 rounded-lg p-4"><h4 class="text-sm font-semibold text-gray-700 mb-3"><i class="fas fa-shield-alt mr-2"></i>HTTP 认证（可选）</h4><div class="grid grid-cols-2 gap-4"><div><label class="block text-xs font-medium text-gray-600 mb-1">HTTP 用户名</label><input v-model="createForm.http_username" type="text" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"></div><div><label class="block text-xs font-medium text-gray-600 mb-1">HTTP 密码</label><input v-model="createForm.http_password" type="text" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"></div></div></div>
                    <div class="bg-gray-50 rounded-lg p-4"><h4 class="text-sm font-semibold text-gray-700 mb-3"><i class="fas fa-server mr-2"></i>服务器配置</h4><div class="grid grid-cols-3 gap-4"><div><label class="block text-xs font-medium text-gray-600 mb-1">起始端口</label><input v-model.number="createForm.base_port" type="number" min="1024" max="65535" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"></div><div><label class="block text-xs font-medium text-gray-600 mb-1">数据库服务</label><select v-model="createForm.db_service" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><option value="mariadb">MariaDB</option><option value="mysql">MySQL</option></select></div><div><label class="block text-xs font-medium text-gray-600 mb-1">网站分组</label><select v-model.number="createForm.website_group_id" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><option value="1">默认分组</option><option v-for="g in panelGroups" :key="g.id" :value="g.id">{{ g.name }}</option></select></div></div></div>
                    <div v-if="createProgress.show" class="bg-blue-50 border border-blue-200 rounded-lg p-4">
                        <div class="flex items-center gap-2 mb-2"><i v-if="!createProgress.results.length" class="fas fa-spinner fa-spin text-blue-600"></i><i v-else class="fas fa-check-circle text-blue-600"></i><span class="text-sm font-semibold text-blue-800">{{ createProgress.message }}</span></div>
                        <div v-if="createProgress.results.length" class="space-y-1 mt-2 max-h-40 overflow-y-auto"><div v-for="(r, i) in createProgress.results" :key="i" class="flex items-center gap-2 text-xs"><i :class="r.status === 'error' ? 'fas fa-times-circle text-red-500' : 'fas fa-check-circle text-green-500'"></i><span class="font-medium">{{ r.domain || '站点' }}</span><span class="text-gray-500">— {{ r.message || (r.status === 'error' ? '失败' : '成功') }}</span></div></div>
                        <div v-if="!createProgress.results.length && wpInstallStatuses[wizardSiteId]" class="text-xs text-blue-600 mt-1">{{ wpInstallStatuses[wizardSiteId].message }}</div>
                    </div>
                </div>

                <!-- Step 2 -->
                <div v-if="wizardStep === 2" class="p-6 space-y-4">
                    <div class="bg-green-50 border border-green-200 rounded-lg p-4 mb-2"><p class="text-green-700 text-sm"><i class="fas fa-check-circle mr-2"></i>站点已创建成功！现在可以上传并安装主题和插件。</p></div>
                    <div class="bg-gray-50 rounded-lg p-4">
                        <div class="flex items-center justify-between mb-3"><h4 class="text-sm font-semibold text-gray-700"><i class="fas fa-palette mr-2 text-orange-500"></i>安装主题</h4><label class="text-xs bg-orange-500 text-white px-3 py-1 rounded-lg cursor-pointer hover:bg-orange-600 transition"><i class="fas fa-upload mr-1"></i>上传主题<input type="file" accept=".zip" @change="handleThemeUpload" class="hidden"></label></div>
                        <div v-if="!themes.length" class="text-sm text-gray-400 py-2">暂无主题，请先上传 .zip 格式的WordPress主题</div>
                        <div v-else class="space-y-2">
                            <div v-for="t in themes" :key="t.id" @click="selectedThemeIds = selectedThemeIds.includes(t.id) ? selectedThemeIds.filter(id => id !== t.id) : [...selectedThemeIds, t.id]" :class="['flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border', selectedThemeIds.includes(t.id) ? 'bg-orange-50 border-orange-300' : 'bg-white border-gray-200 hover:border-orange-200']"><i :class="[selectedThemeIds.includes(t.id) ? 'fas fa-check-square text-orange-600' : 'far fa-square text-gray-400']" class="text-lg"></i><div class="flex-1"><p class="text-sm font-medium">{{ t.name }}</p><p class="text-xs text-gray-500">{{ t.filename }} · {{ formatSize(t.file_size) }}</p></div></div>
                        </div>
                    </div>
                    <div class="bg-gray-50 rounded-lg p-4">
                        <div class="flex items-center justify-between mb-3"><h4 class="text-sm font-semibold text-gray-700"><i class="fas fa-plug mr-2 text-indigo-500"></i>安装插件</h4><label class="text-xs btn-primary text-white px-3 py-1 rounded-lg cursor-pointer"><i class="fas fa-upload mr-1"></i>上传插件<input type="file" accept=".zip" @change="handlePluginUpload" class="hidden"></label></div>
                        <div v-if="!plugins.filter(p => p.enabled).length" class="text-sm text-gray-400 py-2">暂无可用插件，请先上传 .zip 格式的WordPress插件</div>
                        <div v-else class="space-y-2">
                            <div v-for="p in plugins.filter(p => p.enabled)" :key="p.id" @click="selectedPluginIds = selectedPluginIds.includes(p.id) ? selectedPluginIds.filter(id => id !== p.id) : [...selectedPluginIds, p.id]" :class="['flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border', selectedPluginIds.includes(p.id) ? 'bg-indigo-50 border-indigo-300' : 'bg-white border-gray-200 hover:border-indigo-200']"><i :class="[selectedPluginIds.includes(p.id) ? 'fas fa-check-square text-indigo-600' : 'far fa-square text-gray-400']" class="text-lg"></i><div class="flex-1"><p class="text-sm font-medium">{{ p.name }}</p><p class="text-xs text-gray-500">{{ p.filename }} · {{ formatSize(p.file_size) }}</p></div></div>
                        </div>
                    </div>
                    <div v-if="step2Results.length" class="bg-gray-50 rounded-lg p-4"><h4 class="text-sm font-semibold text-gray-700 mb-2">安装结果</h4><div class="space-y-1"><div v-for="(r, i) in step2Results" :key="i" class="flex items-center gap-2 text-sm"><i :class="r.status === 'success' ? 'fas fa-check-circle text-green-500' : 'fas fa-exclamation-circle text-red-500'"></i><span>{{ r.theme || r.plugin || '项目' }}: {{ r.message }}</span></div></div></div>
                    <div v-if="step2Installing" class="text-center text-indigo-600 text-sm"><i class="fas fa-spinner fa-spin mr-2"></i>正在安装主题和插件...</div>
                </div>

                <!-- Step 3 -->
                <div v-if="wizardStep === 3" class="p-6 space-y-4">
                    <div v-if="!cfConnected" class="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-2">
                        <p class="text-yellow-700 text-sm mb-3"><i class="fas fa-exclamation-triangle mr-2"></i>Cloudflare未授权。授权后可自动配置DNS解析。</p>
                        <div class="flex gap-2 mb-2">
                            <button @click="cfAuthMode='token'" :class="cfAuthMode==='token' ? 'bg-orange-500 text-white' : 'bg-gray-100 text-gray-600'" class="px-3 py-1.5 rounded-lg text-sm font-medium">API Token</button>
                            <button @click="cfAuthMode='global'" :class="cfAuthMode==='global' ? 'bg-orange-500 text-white' : 'bg-gray-100 text-gray-600'" class="px-3 py-1.5 rounded-lg text-sm font-medium">Global API Key</button>
                        </div>
                        <div v-if="cfAuthMode==='token'" class="flex gap-2"><input v-model="cfToken" type="password" placeholder="输入Cloudflare API Token" class="flex-1 px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><button @click="cfVerify" :disabled="loading" class="bg-orange-500 text-white px-4 py-2 rounded-lg text-sm hover:bg-orange-600"><i class="fas fa-check mr-1"></i>验证</button></div>
                        <div v-if="cfAuthMode==='global'" class="space-y-2">
                            <input v-model="cfEmail" type="email" placeholder="Cloudflare账户邮箱" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                            <div class="flex gap-2"><input v-model="cfKey" type="password" placeholder="输入Global API Key" class="flex-1 px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><button @click="cfVerify" :disabled="loading" class="bg-orange-500 text-white px-4 py-2 rounded-lg text-sm hover:bg-orange-600"><i class="fas fa-check mr-1"></i>验证</button></div>
                        </div>
                        <p class="text-xs text-gray-500 mt-2">Cloudflare控制台 → My Profile → API Tokens</p>
                    </div>
                    <div v-else class="space-y-4">
                        <div class="bg-green-50 border border-green-200 rounded-lg p-4"><p class="text-green-700 text-sm"><i class="fab fa-cloudflare mr-2"></i>Cloudflare已连接，可以为站点自动创建DNS A记录。</p></div>
                        <div v-if="cfAccounts.length > 1"><label class="block text-sm font-medium text-gray-700 mb-1">选择Cloudflare账号</label><select v-model="cfSelectedAccountId" @change="loadCfZones" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"><option value="">默认账号</option><option v-for="acc in cfAccounts" :key="acc.id" :value="acc.id">{{ acc.name }} <span class="text-xs text-gray-400">({{ acc.auth_type === 'global' ? acc.api_email : 'API Token' }})</span></option></select></div>
                        <div><label class="block text-sm font-medium text-gray-700 mb-1">选择域名区域</label><select v-model="cfSelectedZone" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"><option value="">自动匹配</option><option v-for="z in cfZones" :key="z.id" :value="z.id">{{ z.name }}</option></select><p class="text-xs text-gray-500 mt-1">选择"自动匹配"将根据站点域名自动查找对应区域</p></div>
                        <div><label class="block text-sm font-medium text-gray-700 mb-1">服务器IP（可选）</label><input v-model="cfServerIp" type="text" placeholder="留空则使用1Panel主机IP" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div>
                        <div class="flex items-center gap-3"><label class="flex items-center gap-2 cursor-pointer"><input type="checkbox" v-model="cfProxied" class="w-4 h-4 text-indigo-600 rounded"><span class="text-sm text-gray-700">启用Cloudflare代理（橙色云朵）</span></label></div>
                        <div v-if="cfDnsResult" class="bg-green-50 border border-green-200 rounded-lg p-4"><p class="text-green-700 text-sm"><i class="fas fa-check-circle mr-2"></i>DNS A记录创建成功！</p><div class="mt-2 text-xs text-gray-600 space-y-1"><p>类型: {{ cfDnsResult.type }}</p><p>名称: {{ cfDnsResult.name }}</p><p>内容: {{ cfDnsResult.content }}</p><p>代理: {{ cfDnsResult.proxied ? '是' : '否' }}</p></div></div>
                        <div v-if="cfCreating" class="text-center text-orange-600 text-sm"><i class="fas fa-spinner fa-spin mr-2"></i>正在创建DNS记录...</div>
                    </div>
                </div>

                <!-- Wizard Footer -->
                <div class="p-6 border-t flex gap-3 justify-between">
                    <button v-if="wizardStep > 1" @click="wizardStep--" class="px-6 py-2 border rounded-lg hover:bg-gray-50"><i class="fas fa-arrow-left mr-2"></i>上一步</button>
                    <div v-else></div>
                    <div class="flex gap-3">
                        <template v-if="wizardStep === 1">
                            <button @click="closeWizard" class="px-6 py-2 border rounded-lg hover:bg-gray-50">取消</button>
                            <button v-if="wizardMode === 'batch' && createProgress.results.length" @click="closeWizard" class="btn-primary text-white px-6 py-2 rounded-lg"><i class="fas fa-check mr-2"></i>完成</button>
                            <button v-else @click="wizardCreateSite" :disabled="loading || (createProgress.show && !createProgress.results.length)" class="btn-primary text-white px-6 py-2 rounded-lg"><i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i><i v-else class="fas fa-rocket mr-2"></i>{{ wizardMode === 'batch' ? '批量创建' : '创建站点' }}</button>
                        </template>
                        <template v-if="wizardStep === 2">
                            <button @click="wizardSkipStep2" class="px-6 py-2 border rounded-lg hover:bg-gray-50">跳过</button>
                            <button v-if="!step2Installing && !step2Results.length" @click="wizardInstallThemeAndPlugins" :disabled="!selectedThemeIds.length && !selectedPluginIds.length" class="bg-orange-500 text-white px-6 py-2 rounded-lg hover:bg-orange-600"><i class="fas fa-download mr-2"></i>安装选中项</button>
                            <button v-if="step2Results.length" @click="wizardNextStep2" class="btn-primary text-white px-6 py-2 rounded-lg">下一步 <i class="fas fa-arrow-right ml-2"></i></button>
                        </template>
                        <template v-if="wizardStep === 3">
                            <button @click="wizardFinish" class="px-6 py-2 border rounded-lg hover:bg-gray-50">跳过</button>
                            <button v-if="cfConnected && !cfDnsResult" @click="cfCreateDns" :disabled="cfCreating" class="bg-orange-500 text-white px-6 py-2 rounded-lg hover:bg-orange-600"><i class="fab fa-cloudflare mr-2"></i>创建DNS记录</button>
                            <button v-if="cfDnsResult" @click="wizardFinish" class="btn-primary text-white px-6 py-2 rounded-lg"><i class="fas fa-check mr-2"></i>完成</button>
                        </template>
                    </div>
                </div>
            </div>
        </div>

        <!-- Edit Modal -->
        <div v-if="showEditModal" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl max-h-[90vh] overflow-y-auto w-full max-w-2xl mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between"><h2 class="text-lg font-bold">编辑站点</h2><button @click="showEditModal = false" class="text-gray-400 hover:text-gray-600"><i class="fas fa-times text-xl"></i></button></div>
                <div class="p-6 space-y-4">
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">站点名称</label><input v-model="editForm.site_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">URL</label><input v-model="editForm.url" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">管理员</label><input v-model="editForm.admin_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">管理员密码</label><input v-model="editForm.admin_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">标签</label><input v-model="editForm.tag" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">安全ID</label><input v-model="editForm.security_id" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">HTTP 用户名</label><input v-model="editForm.http_username" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">HTTP 密码</label><input v-model="editForm.http_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end"><button @click="showEditModal = false" class="px-6 py-2 border rounded-lg hover:bg-gray-50">取消</button><button @click="submitEdit" :disabled="loading" class="btn-primary text-white px-6 py-2 rounded-lg"><i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>保存更改</button></div>
            </div>
        </div>

        <!-- Confirm Modal -->
        <div v-if="modal.show" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 fade-in"><div class="p-6"><h2 class="text-lg font-bold text-gray-800 mb-2">{{ modal.title }}</h2><p class="text-gray-600">{{ modal.content }}</p></div><div class="p-6 border-t flex gap-3 justify-end"><button @click="modal.show = false" class="px-6 py-2 border rounded-lg hover:bg-gray-50">取消</button><button @click="modal.onConfirm()" class="bg-red-500 text-white px-6 py-2 rounded-lg hover:bg-red-600">删除</button></div></div>
        </div>

        <!-- Toast -->
        <div v-if="toast.show" class="toast fade-in">
            <div :class="['rounded-lg shadow-lg px-6 py-4 flex items-center gap-3', toast.type === 'success' ? 'bg-green-500 text-white' : toast.type === 'error' ? 'bg-red-500 text-white' : 'bg-blue-500 text-white']">
                <i :class="toast.type === 'success' ? 'fas fa-check-circle' : toast.type === 'error' ? 'fas fa-exclamation-circle' : 'fas fa-info-circle'"></i><span>{{ toast.message }}</span>
            </div>
        </div>
    </div>
    `,
});

app.mount('#app');
