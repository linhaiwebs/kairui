const { createApp, ref, reactive, computed, onMounted, watch, nextTick } = Vue;

const app = createApp({
    setup() {
        // ---- 状态 ----
        const isLoggedIn = ref(false);
        const currentUser = ref('');
        const currentPage = ref('dashboard');
        const loading = ref(false);
        const toast = reactive({ show: false, message: '', type: 'success' });
        const modal = reactive({ show: false, title: '', content: '', onConfirm: null });

        // 登录表单
        const loginForm = reactive({ username: 'adsadmin', password: 'Mm123567..' });
        const loginError = ref('');

        // 站点
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

        // 1Panel 数据
        const panelConnected = ref(false);
        const panelWebsites = ref([]);
        const panelInstalledApps = ref([]);
        const panelGroups = ref([]);

        // 创建站点表单
        const showCreateModal = ref(false);
        const createForm = reactive({
            mode: 'single',
            site_name: '',
            url: '',
            admin_name: 'admin',
            admin_password: '',
            tag: '',
            security_id: '',
            http_username: '',
            http_password: '',
            verify_certificate: true,
            ssl_version: 'auto',
            domains: '',
            base_port: 8081,
            db_service: 'mariadb',
            website_group_id: 1,
            selected_plugins: [],
        });

        // 编辑站点
        const showEditModal = ref(false);
        const editForm = reactive({});
        const editingSiteId = ref('');

        // 创建进度
        const createProgress = reactive({ show: false, current: 0, total: 0, message: '', results: [] });

        // WP 安装状态轮询
        const wpInstallStatuses = reactive({}); // site_id -> {status, message}
        const wpPollingTimers = reactive({}); // site_id -> intervalId

        // 插件
        const plugins = ref([]);
        const uploadProgress = ref(0);
        const showPluginUpload = ref(false);

        // 全局配置
        const globalConfig = reactive({
            default_admin_name: 'admin',
            default_admin_password: '',
            default_plugins: [],
            default_themes: [],
            db_service: 'mariadb',
        });

        // ---- 方法 ----
        function showToast(message, type = 'success') {
            toast.message = message;
            toast.type = type;
            toast.show = true;
            setTimeout(() => { toast.show = false; }, 3000);
        }

        function showModal(title, content, onConfirm) {
            modal.title = title;
            modal.content = content;
            modal.onConfirm = onConfirm;
            modal.show = true;
        }

        function formatSize(bytes) {
            if (!bytes) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        async function handleLogin() {
            loginError.value = '';
            loading.value = true;
            try {
                const resp = await API.login(loginForm.username, loginForm.password);
                if (resp.code === 200) {
                    isLoggedIn.value = true;
                    currentUser.value = resp.data.username;
                    showToast('登录成功');
                    loadInitialData();
                } else {
                    loginError.value = resp.message || '用户名或密码错误';
                }
            } catch (e) {
                loginError.value = '连接错误';
            } finally {
                loading.value = false;
            }
        }

        function handleLogout() {
            API.logout();
            isLoggedIn.value = false;
            currentUser.value = '';
            currentPage.value = 'dashboard';
        }

        async function loadInitialData() {
            loading.value = true;
            try {
                await Promise.all([
                    loadSites(),
                    checkPanelStatus(),
                    loadPanelData(),
                    loadConfig(),
                    loadPlugins(),
                ]);
            } finally {
                loading.value = false;
            }
        }

        async function loadSites() {
            try {
                const resp = await API.getSites();
                if (resp.code === 200) {
                    sites.value = resp.data || [];
                    // Stop polling for site IDs that no longer exist (after compact)
                    const currentIds = new Set(sites.value.map(s => s.id));
                    for (const id of Object.keys(wpPollingTimers)) {
                        if (!currentIds.has(Number(id))) {
                            stopWPPolling(Number(id));
                            delete wpInstallStatuses[Number(id)];
                        }
                    }
                }
            } catch (e) {
                console.error('加载站点失败:', e);
            }
        }

        async function checkPanelStatus() {
            try {
                const resp = await API.panelStatus();
                panelConnected.value = resp.data?.connected || false;
            } catch (e) {
                panelConnected.value = false;
            }
        }

        async function loadPanelData() {
            try {
                const [websitesResp, installedResp, groupsResp] = await Promise.all([
                    API.panelSearchWebsites(),
                    API.panelSearchInstalled(),
                    API.panelSearchGroups(),
                ]);
                if (websitesResp.code === 200) {
                    panelWebsites.value = websitesResp.data?.items || [];
                }
                if (installedResp.code === 200) {
                    panelInstalledApps.value = installedResp.data?.items || [];
                }
                if (groupsResp.code === 200) {
                    panelGroups.value = groupsResp.data || [];
                }
            } catch (e) {
                console.error('加载1Panel数据失败:', e);
            }
        }

        async function loadConfig() {
            try {
                const resp = await API.getConfig();
                if (resp.code === 200) {
                    Object.assign(globalConfig, resp.data);
                    createForm.admin_name = resp.data.default_admin_name || 'admin';
                    createForm.db_service = resp.data.db_service || 'mariadb';
                }
            } catch (e) {
                console.error('加载配置失败:', e);
            }
        }

        async function refreshSites() {
            loading.value = true;
            try {
                await Promise.all([loadSites(), loadPanelData()]);
                showToast('数据已刷新');
            } finally {
                loading.value = false;
            }
        }

        // ---- 插件 ----
        async function loadPlugins() {
            try {
                const resp = await API.getPlugins();
                if (resp.code === 200) {
                    plugins.value = resp.data || [];
                }
            } catch (e) {
                console.error('加载插件失败:', e);
            }
        }

        async function handlePluginUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            if (!file.name.endsWith('.zip')) {
                showToast('仅支持 .zip 格式的插件文件', 'error');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);
            formData.append('description', '');

            loading.value = true;
            try {
                const resp = await API.uploadPlugin(formData);
                if (resp.code === 200) {
                    showToast(`插件 ${file.name} 上传成功`);
                    await loadPlugins();
                    // Auto-select the newly uploaded plugin
                    if (resp.data?.id) {
                        createForm.selected_plugins.push(resp.data.id);
                    }
                } else {
                    showToast(resp.message || '上传失败', 'error');
                }
            } catch (e) {
                showToast('上传失败', 'error');
            } finally {
                loading.value = false;
                event.target.value = '';
            }
        }

        async function handleDeletePlugin(plugin) {
            showModal(
                '删除插件',
                `确定要删除插件 "${plugin.name}" 吗？已安装的站点不受影响，但新创建的站点将不再自动安装此插件。`,
                async () => {
                    loading.value = true;
                    try {
                        await API.deletePlugin(plugin.id);
                        createForm.selected_plugins = createForm.selected_plugins.filter(id => id !== plugin.id);
                        showToast('插件已删除');
                        await loadPlugins();
                    } catch (e) {
                        showToast('删除失败', 'error');
                    } finally {
                        loading.value = false;
                    }
                    modal.show = false;
                }
            );
        }

        async function handleTogglePlugin(plugin) {
            try {
                await API.togglePlugin(plugin.id);
                await loadPlugins();
                showToast(plugin.enabled ? '插件已禁用' : '插件已启用');
            } catch (e) {
                showToast('操作失败', 'error');
            }
        }

        function togglePluginSelection(pluginId) {
            const idx = createForm.selected_plugins.indexOf(pluginId);
            if (idx >= 0) {
                createForm.selected_plugins.splice(idx, 1);
            } else {
                createForm.selected_plugins.push(pluginId);
            }
        }

        // ---- 创建站点 ----
        function openCreateModal(mode = 'single') {
            createForm.mode = mode;
            createForm.site_name = '';
            createForm.url = '';
            createForm.admin_name = globalConfig.default_admin_name || 'admin';
            createForm.admin_password = globalConfig.default_admin_password || '';
            createForm.tag = '';
            createForm.security_id = '';
            createForm.http_username = '';
            createForm.http_password = '';
            createForm.verify_certificate = true;
            createForm.ssl_version = 'auto';
            createForm.domains = '';
            createForm.base_port = 8081;
            createForm.selected_plugins = [];
            showCreateModal.value = true;
        }

        async function submitCreate() {
            loading.value = true;
            try {
                if (createForm.mode === 'single') {
                    if (!createForm.site_name.trim()) {
                        showToast('请输入域名', 'error');
                        loading.value = false;
                        return;
                    }
                    const domain = createForm.site_name.trim();
                    await createSingleSite(domain);
                    showCreateModal.value = false;
                } else {
                    const domains = createForm.domains.split('\n')
                        .map(d => d.trim())
                        .filter(d => d.length > 0);

                    if (!domains.length) {
                        showToast('请输入至少一个域名', 'error');
                        loading.value = false;
                        return;
                    }

                    if (panelConnected.value) {
                        // Use batch API for efficient creation
                        createProgress.show = true;
                        createProgress.total = domains.length;
                        createProgress.current = 0;
                        createProgress.results = [];
                        createProgress.message = `正在批量一键部署 ${domains.length} 个WordPress站点...`;

                        const resp = await API.batchCreateWordPress({
                            domains: domains,
                            admin_name: createForm.admin_name,
                            admin_password: createForm.admin_password,
                            tag: createForm.tag,
                            security_id: createForm.security_id,
                            http_username: createForm.http_username,
                            http_password: createForm.http_password,
                            verify_certificate: createForm.verify_certificate,
                            ssl_version: createForm.ssl_version,
                            base_port: createForm.base_port,
                            db_service: createForm.db_service,
                            website_group_id: createForm.website_group_id || 1,
                            plugin_ids: createForm.selected_plugins,
                        });

                        if (resp.code === 200 && resp.data) {
                            createProgress.results = resp.data.results || [];
                            const s = resp.data.success || 0;
                            const e = resp.data.error || 0;
                            createProgress.message = `批量部署已提交：${s} 个站点正在部署中...`;
                            showToast(`批量部署已提交：${s} 个站点正在1Panel中部署`);
                            // Start polling WP install status for each site
                            for (const r of createProgress.results) {
                                if (r.site_id && r.wp_install_status === 'installing') {
                                    startWPPolling(r.site_id, r.domain);
                                }
                            }
                        } else {
                            createProgress.message = `批量创建失败: ${resp.message || '未知错误'}`;
                            showToast(`批量创建失败: ${resp.message}`, 'error');
                        }
                    } else {
                        // Offline mode - just save to local DB
                        createProgress.show = true;
                        createProgress.total = domains.length;
                        createProgress.current = 0;
                        createProgress.results = [];
                        for (const domain of domains) {
                            createProgress.current++;
                            createProgress.message = `正在保存 ${domain}...`;
                            try {
                                await API.createSite({
                                    site_name: domain,
                                    url: `http://${domain}`,
                                    admin_name: createForm.admin_name,
                                    admin_password: createForm.admin_password,
                                    tag: createForm.tag,
                                    security_id: createForm.security_id,
                                    http_username: createForm.http_username,
                                    http_password: createForm.http_password,
                                    verify_certificate: createForm.verify_certificate,
                                    ssl_version: createForm.ssl_version,
                                });
                                createProgress.results.push({ domain, status: 'success', message: '已保存' });
                            } catch (e) {
                                createProgress.results.push({ domain, status: 'error', message: e.message });
                            }
                        }
                        showToast('1Panel未连接，站点仅保存到本地', 'error');
                    }
                    showCreateModal.value = false;
                }

                await loadSites();
                await loadPanelData();
            } catch (e) {
                showToast(`错误: ${e.message}`, 'error');
            } finally {
                loading.value = false;
            }
        }

        async function createSingleSite(domain) {
            const alias = domain.replace(/\./g, '-');

            if (panelConnected.value) {
                // Use batch-create API which handles DB creation, WP install, and allowPort correctly
                createProgress.message = `正在通过1Panel一键部署WordPress站点 ${domain}...`;

                const resp = await API.batchCreateWordPress({
                    domains: [domain],
                    admin_name: createForm.admin_name,
                    admin_password: createForm.admin_password,
                    tag: createForm.tag,
                    security_id: createForm.security_id,
                    http_username: createForm.http_username,
                    http_password: createForm.http_password,
                    verify_certificate: createForm.verify_certificate,
                    ssl_version: createForm.ssl_version,
                    base_port: createForm.base_port,
                    db_service: createForm.db_service,
                    website_group_id: createForm.website_group_id || 1,
                    plugin_ids: createForm.selected_plugins,
                });

                if (resp.code !== 200) {
                    throw new Error(resp.message || '创建站点失败');
                }

                const result = resp.data.results[0];
                if (result && result.status === 'error') {
                    throw new Error(result.message || '创建站点失败');
                }

                await loadSites();
                await loadPanelData();
                
                // Start WP install status polling
                if (result?.site_id && result?.wp_install_status === 'installing') {
                    startWPPolling(result.site_id, domain);
                    showToast(`站点 ${domain} 部署已提交，1Panel正在部署...`);
                } else {
                    showToast(`站点 ${domain} 部署已提交，端口: ${result?.port || '未知'}`);
                }
            } else {
                await API.createSite({
                    site_name: domain,
                    url: createForm.url || `http://${domain}`,
                    admin_name: createForm.admin_name,
                    admin_password: createForm.admin_password,
                    tag: createForm.tag,
                    security_id: createForm.security_id,
                    http_username: createForm.http_username,
                    http_password: createForm.http_password,
                    verify_certificate: createForm.verify_certificate,
                    ssl_version: createForm.ssl_version,
                });
                showToast(`站点 ${domain} 已保存（1Panel未连接，未实际安装）`, 'error');
            }
        }

        // ---- WP安装状态轮询 ----
        function startWPPolling(siteId, domain) {
            if (wpPollingTimers[siteId]) return; // Already polling
            wpInstallStatuses[siteId] = { status: 'installing', message: '1Panel正在创建数据库...', domain };
            
            const timer = setInterval(async () => {
                try {
                    const resp = await API.getWPInstallStatus(siteId);
                    if (resp.code === 200 && resp.data) {
                        wpInstallStatuses[siteId] = { ...resp.data, domain };
                        if (resp.data.status === 'installed') {
                            stopWPPolling(siteId);
                            showToast(`${domain || siteId} 部署完成！WordPress安装成功`, 'success');
                            await loadSites();
                        } else if (resp.data.status === 'failed') {
                            stopWPPolling(siteId);
                            showToast(`${domain || siteId} 部署失败: ${resp.data.message}`, 'error');
                        }
                    }
                } catch (e) {
                    console.error('WP polling error:', e);
                }
            }, 5000); // Poll every 5 seconds for real-time updates
            
            wpPollingTimers[siteId] = timer;
        }

        function stopWPPolling(siteId) {
            if (wpPollingTimers[siteId]) {
                clearInterval(wpPollingTimers[siteId]);
                delete wpPollingTimers[siteId];
            }
        }

        // ---- 编辑站点 ----
        function openEditModal(site) {
            editingSiteId.value = site.id;
            Object.assign(editForm, {
                site_name: site.site_name,
                url: site.url,
                admin_name: site.admin_name,
                admin_password: site.admin_password,
                tag: site.tag,
                security_id: site.security_id,
                http_username: site.http_username,
                http_password: site.http_password,
                verify_certificate: !!site.verify_certificate,
                ssl_version: site.ssl_version || 'auto',
            });
            showEditModal.value = true;
        }

        async function submitEdit() {
            loading.value = true;
            try {
                await API.updateSite(editingSiteId.value, editForm);
                showEditModal.value = false;
                showToast('站点已更新');
                await loadSites();
            } catch (e) {
                showToast('更新失败', 'error');
            } finally {
                loading.value = false;
            }
        }

        // ---- 删除站点 ----
        function confirmDelete(site) {
            showModal(
                '删除站点',
                `确定要删除 "${site.site_name}" 吗？${site.panel_website_id ? '同时从1Panel删除WordPress应用和网站。' : ''}此操作不可撤销。`,
                async () => {
                    loading.value = true;
                    try {
                        if (site.panel_website_id && panelConnected.value) {
                            await API.panelDeleteWebsite(site.panel_website_id, true);
                        }
                        await API.deleteSite(site.id);
                        showToast('站点已删除');
                        await loadSites();
                        await loadPanelData();
                    } catch (e) {
                        showToast('删除失败', 'error');
                    } finally {
                        loading.value = false;
                    }
                    modal.show = false;
                }
            );
        }

        // ---- 修复1Panel网站 ----
        async function fixWebsite(site) {
            showModal(
                '修复1Panel网站',
                `站点 "${site.site_name}" 已有WordPress应用但缺少1Panel网站(OpenResty)。\n点击确定将创建1Panel部署网站并关联到现有WordPress应用。`,
                async () => {
                    loading.value = true;
                    try {
                        const resp = await API.request('POST', `/api/sites/${site.id}/fix-website`);
                        if (resp.code === 200) {
                            showToast('1Panel网站已修复创建');
                            await loadSites();
                        } else {
                            showToast(resp.message || '修复失败', 'error');
                        }
                    } catch (e) {
                        showToast('修复请求失败', 'error');
                    } finally {
                        loading.value = false;
                    }
                    modal.show = false;
                }
            );
        }

        function exportCSV() {
            API.exportCSV();
            showToast('CSV文件已导出');
        }

        async function saveGlobalConfig() {
            loading.value = true;
            try {
                await API.saveConfig(globalConfig);
                showToast('配置已保存');
            } catch (e) {
                showToast('保存配置失败', 'error');
            } finally {
                loading.value = false;
            }
        }

        onMounted(async () => {
            if (API.token) {
                try {
                    const resp = await API.checkAuth();
                    if (resp.code === 200) {
                        isLoggedIn.value = true;
                        currentUser.value = resp.data.username;
                        await loadInitialData();
                    }
                } catch (e) {
                    API.logout();
                }
            }
        });

        return {
            isLoggedIn, currentUser, currentPage, loading, toast, modal,
            loginForm, loginError,
            sites, searchQuery, filteredSites,
            panelConnected, panelWebsites, panelInstalledApps, panelGroups,
            showCreateModal, createForm,
            showEditModal, editForm, editingSiteId,
            globalConfig, createProgress,
            wpInstallStatuses, wpPollingTimers,
            plugins, uploadProgress, showPluginUpload, formatSize,
            handleLogin, handleLogout, refreshSites,
            openCreateModal, submitCreate,
            openEditModal, submitEdit,
            confirmDelete, fixWebsite, saveGlobalConfig, exportCSV,
            loadPlugins, handlePluginUpload, handleDeletePlugin, handleTogglePlugin, togglePluginSelection,
            showToast, showModal,
        };
    },

    template: `
    <!-- 登录页面 -->
    <div v-if="!isLoggedIn" class="min-h-screen flex items-center justify-center login-bg">
        <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md fade-in">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-2xl flex items-center justify-center mx-auto mb-4">
                    <i class="fab fa-wordpress text-white text-3xl"></i>
                </div>
                <h1 class="text-2xl font-bold text-gray-800">WordPress 站点管理</h1>
                <p class="text-gray-500 mt-2">登录以管理您的WordPress站点</p>
            </div>
            <form @submit.prevent="handleLogin">
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 mb-1">用户名</label>
                    <input v-model="loginForm.username" type="text" required
                        class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:border-indigo-500"
                        placeholder="请输入用户名">
                </div>
                <div class="mb-6">
                    <label class="block text-sm font-medium text-gray-700 mb-1">密码</label>
                    <input v-model="loginForm.password" type="password" required
                        class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:border-indigo-500"
                        placeholder="请输入密码">
                </div>
                <p v-if="loginError" class="text-red-500 text-sm mb-4">{{ loginError }}</p>
                <button type="submit" :disabled="loading"
                    class="w-full btn-primary text-white py-3 rounded-lg font-semibold hover:shadow-lg transition">
                    <i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>
                    <span v-else>登 录</span>
                </button>
            </form>
        </div>
    </div>

    <!-- 主应用 -->
    <div v-else class="min-h-screen flex">
        <!-- 侧边栏 -->
        <aside class="w-64 sidebar-gradient text-white flex flex-col">
            <div class="p-6 border-b border-indigo-700">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 bg-white bg-opacity-20 rounded-lg flex items-center justify-center">
                        <i class="fab fa-wordpress text-xl"></i>
                    </div>
                    <div>
                        <h2 class="font-bold text-lg">WP 管理器</h2>
                        <p class="text-xs text-indigo-300">站点管理平台</p>
                    </div>
                </div>
            </div>
            <nav class="flex-1 p-4 space-y-1">
                <a @click="currentPage = 'dashboard'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'dashboard' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-tachometer-alt w-5 text-center"></i><span>仪表盘</span>
                </a>
                <a @click="currentPage = 'sites'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'sites' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-globe w-5 text-center"></i><span>站点列表</span>
                    <span class="ml-auto bg-indigo-500 text-xs px-2 py-0.5 rounded-full">{{ sites.length }}</span>
                </a>
                <a @click="currentPage = 'create'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'create' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-plus-circle w-5 text-center"></i><span>创建站点</span>
                </a>
                <a @click="currentPage = 'plugins'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'plugins' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-plug w-5 text-center"></i><span>插件管理</span>
                    <span class="ml-auto bg-indigo-500 text-xs px-2 py-0.5 rounded-full">{{ plugins.length }}</span>
                </a>
                <a @click="currentPage = 'settings'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'settings' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-cog w-5 text-center"></i><span>系统设置</span>
                </a>
            </nav>
            <div class="p-4 border-t border-indigo-700">
                <div class="flex items-center gap-3 px-2">
                    <div class="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center">
                        <i class="fas fa-user text-sm"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium truncate">{{ currentUser }}</p>
                        <p class="text-xs text-indigo-300">管理员</p>
                    </div>
                    <button @click="handleLogout" class="text-indigo-300 hover:text-white" title="退出登录">
                        <i class="fas fa-sign-out-alt"></i>
                    </button>
                </div>
            </div>
        </aside>

        <!-- 主内容区 -->
        <main class="flex-1 overflow-auto">
            <!-- 顶部栏 -->
            <header class="bg-white border-b px-8 py-4 flex items-center justify-between">
                <div>
                    <h1 class="text-xl font-bold text-gray-800">
                        {{ currentPage === 'dashboard' ? '仪表盘' : currentPage === 'sites' ? '站点列表' : currentPage === 'create' ? '创建WordPress站点' : currentPage === 'plugins' ? '插件管理' : '系统设置' }}
                    </h1>
                    <p class="text-sm text-gray-500">
                        <span :class="panelConnected ? 'text-green-500' : 'text-red-500'">
                            <i class="fas fa-circle text-xs mr-1"></i>
                            {{ panelConnected ? '1Panel 已连接' : '1Panel 未连接' }}
                        </span>
                    </p>
                </div>
                <button @click="refreshSites" class="px-4 py-2 border rounded-lg hover:bg-gray-50 transition text-sm">
                    <i class="fas fa-sync-alt mr-2" :class="{'fa-spin': loading}"></i>刷新
                </button>
            </header>

            <!-- 仪表盘 -->
            <div v-if="currentPage === 'dashboard'" class="p-8 fade-in">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">站点总数</p><p class="text-3xl font-bold text-gray-800 mt-1">{{ sites.length }}</p></div><div class="w-12 h-12 bg-indigo-100 rounded-lg flex items-center justify-center"><i class="fas fa-globe text-indigo-600 text-xl"></i></div></div></div>
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">活跃站点</p><p class="text-3xl font-bold text-green-600 mt-1">{{ sites.filter(s => s.status === 'active').length }}</p></div><div class="w-12 h-12 bg-green-100 rounded-lg flex items-center justify-center"><i class="fas fa-check-circle text-green-600 text-xl"></i></div></div></div>
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">1Panel 网站</p><p class="text-3xl font-bold text-purple-600 mt-1">{{ panelWebsites.length }}</p></div><div class="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center"><i class="fas fa-server text-purple-600 text-xl"></i></div></div></div>
                    <div class="bg-white rounded-xl p-6 card-shadow"><div class="flex items-center justify-between"><div><p class="text-sm text-gray-500">已安装应用</p><p class="text-3xl font-bold text-orange-600 mt-1">{{ panelInstalledApps.length }}</p></div><div class="w-12 h-12 bg-orange-100 rounded-lg flex items-center justify-center"><i class="fas fa-cubes text-orange-600 text-xl"></i></div></div></div>
                </div>
                <div class="bg-white rounded-xl card-shadow mb-8">
                    <div class="px-6 py-4 border-b"><h3 class="font-semibold text-gray-800">1Panel 网站</h3></div>
                    <div class="overflow-x-auto"><table class="w-full"><thead><tr class="bg-gray-50 text-left text-sm text-gray-600"><th class="px-6 py-3">域名</th><th class="px-6 py-3">类型</th><th class="px-6 py-3">状态</th><th class="px-6 py-3">应用</th><th class="px-6 py-3">SSL</th></tr></thead><tbody>
                        <tr v-for="w in panelWebsites" :key="w.id" class="border-t table-row"><td class="px-6 py-3 font-medium">{{ w.primaryDomain }}</td><td class="px-6 py-3"><span class="badge bg-blue-100 text-blue-800">{{ w.type }}</span></td><td class="px-6 py-3"><span :class="w.status === 'Running' ? 'status-running' : 'status-stopped'"><i class="fas fa-circle text-xs mr-1"></i>{{ w.status }}</span></td><td class="px-6 py-3">{{ w.appName || '-' }}</td><td class="px-6 py-3"><span class="badge" :style="{background: w.sslStatus === 'success' ? '#dcfce7' : '#fee2e2', color: w.sslStatus === 'success' ? '#166534' : '#991b1b'}">{{ w.sslStatus === 'success' ? 'SSL 已启用' : '未启用SSL' }}</span></td></tr>
                        <tr v-if="!panelWebsites.length"><td colspan="5" class="px-6 py-8 text-center text-gray-400"><i class="fas fa-inbox text-4xl mb-2"></i><p>1Panel中暂无网站</p></td></tr>
                    </tbody></table></div>
                </div>
                <div class="bg-white rounded-xl card-shadow">
                    <div class="px-6 py-4 border-b"><h3 class="font-semibold text-gray-800">1Panel 已安装应用</h3></div>
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 p-6">
                        <div v-for="a in panelInstalledApps" :key="a.id" class="border rounded-lg p-4 hover:shadow-md transition"><div class="flex items-center justify-between mb-2"><h4 class="font-semibold">{{ a.appName }}</h4><span :class="a.status === 'Running' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'" class="badge">{{ a.status }}</span></div><p class="text-sm text-gray-500">v{{ a.version }}</p><p class="text-sm text-gray-500" v-if="a.httpPort">端口: {{ a.httpPort }}</p></div>
                    </div>
                </div>
            </div>

            <!-- 站点列表 -->
            <div v-if="currentPage === 'sites'" class="p-8 fade-in">
                <div class="bg-white rounded-xl card-shadow">
                    <div class="px-6 py-4 border-b flex items-center justify-between flex-wrap gap-4">
                        <div class="relative flex-1 max-w-md"><i class="fas fa-search absolute left-3 top-3 text-gray-400"></i><input v-model="searchQuery" type="text" placeholder="搜索站点..." class="w-full pl-10 pr-4 py-2 border rounded-lg focus:border-indigo-500"></div>
                        <div class="flex gap-3">
                            <button @click="exportCSV" class="px-4 py-2 border rounded-lg hover:bg-gray-50 text-sm"><i class="fas fa-download mr-2"></i>导出CSV</button>
                            <button @click="currentPage = 'create'" class="btn-primary text-white px-4 py-2 rounded-lg text-sm"><i class="fas fa-plus mr-2"></i>添加站点</button>
                        </div>
                    </div>
                    <div class="overflow-x-auto"><table class="w-full"><thead><tr class="bg-gray-50 text-left text-xs text-gray-600 uppercase"><th class="px-4 py-3">站点名称</th><th class="px-4 py-3">URL</th><th class="px-4 py-3">端口</th><th class="px-4 py-3">管理员</th><th class="px-4 py-3">管理员密码</th><th class="px-4 py-3">标签</th><th class="px-4 py-3">安全ID</th><th class="px-4 py-3">WP状态</th><th class="px-4 py-3">HTTP用户</th><th class="px-4 py-3">HTTP密码</th><th class="px-4 py-3">验证证书</th><th class="px-4 py-3">SSL版本</th><th class="px-4 py-3">操作</th></tr></thead><tbody>
                        <tr v-for="s in filteredSites" :key="s.id" class="border-t table-row">
                            <td class="px-4 py-3 font-medium text-sm">{{ s.site_name }}</td>
                            <td class="px-4 py-3"><a :href="s.url" target="_blank" class="text-indigo-600 hover:underline text-sm">{{ s.url }}</a></td>
                            <td class="px-4 py-3 text-sm">{{ s.port || '-' }}</td>
                            <td class="px-4 py-3 text-sm">{{ s.admin_name || '-' }}</td>
                            <td class="px-4 py-3 text-sm"><span class="font-mono text-xs bg-gray-100 px-2 py-1 rounded">{{ s.admin_password ? '••••••' : '-' }}</span></td>
                            <td class="px-4 py-3"><span class="badge bg-indigo-100 text-indigo-800" v-if="s.tag">{{ s.tag }}</span><span v-else>-</span></td>
                            <td class="px-4 py-3 text-sm">{{ s.security_id || '-' }}</td>
                            <td class="px-4 py-3 text-sm">
                                <span v-if="wpInstallStatuses[s.id]?.status === 'deploying'" class="badge bg-blue-100 text-blue-800" :title="wpInstallStatuses[s.id]?.message"><i class="fas fa-server fa-spin mr-1"></i>部署中</span>
                                <span v-else-if="wpInstallStatuses[s.id]?.status === 'installing'" class="badge bg-yellow-100 text-yellow-800" :title="wpInstallStatuses[s.id]?.message"><i class="fas fa-spinner fa-spin mr-1"></i>安装中</span>
                                <span v-else-if="wpInstallStatuses[s.id]?.status === 'installed'" class="badge bg-green-100 text-green-800" :title="wpInstallStatuses[s.id]?.message"><i class="fas fa-check-circle mr-1"></i>已完成</span>
                                <span v-else-if="wpInstallStatuses[s.id]?.status === 'failed'" class="badge bg-red-100 text-red-800" :title="wpInstallStatuses[s.id]?.message"><i class="fas fa-times-circle mr-1"></i>失败</span>
                                <span v-else-if="s.panel_app_install_id && !s.panel_website_id" class="badge bg-orange-100 text-orange-800" title="WordPress应用已安装，但1Panel网站缺失，点击扳手修复"><i class="fas fa-exclamation-triangle mr-1"></i>缺网站</span>
                                <span v-else-if="s.panel_website_id" class="badge bg-green-100 text-green-800" title="已通过1Panel一键部署"><i class="fas fa-rocket mr-1"></i>已部署</span>
                                <span v-else class="text-gray-400">-</span>
                                <div v-if="wpInstallStatuses[s.id]?.message" class="text-xs text-gray-500 mt-1 max-w-[180px] truncate">{{ wpInstallStatuses[s.id].message }}</div>
                            </td>
                            <td class="px-4 py-3 text-sm">{{ s.http_username || '-' }}</td>
                            <td class="px-4 py-3 text-sm"><span class="font-mono text-xs" v-if="s.http_password">••••••</span><span v-else>-</span></td>
                            <td class="px-4 py-3 text-sm"><i :class="s.verify_certificate ? 'fas fa-check-circle text-green-500' : 'fas fa-times-circle text-red-500'"></i> {{ s.verify_certificate ? '1' : '0' }}</td>
                            <td class="px-4 py-3 text-sm">{{ s.ssl_version || 'auto' }}</td>
                            <td class="px-4 py-3"><div class="flex gap-2"><button @click="openEditModal(s)" class="text-indigo-600 hover:text-indigo-800" title="编辑"><i class="fas fa-edit"></i></button><button v-if="s.panel_app_install_id && !s.panel_website_id" @click="fixWebsite(s)" class="text-orange-500 hover:text-orange-700" title="修复1Panel网站"><i class="fas fa-wrench"></i></button><button @click="confirmDelete(s)" class="text-red-500 hover:text-red-700" title="删除"><i class="fas fa-trash"></i></button></div></td>
                        </tr>
                        <tr v-if="!filteredSites.length"><td colspan="13" class="px-6 py-12 text-center text-gray-400"><i class="fas fa-inbox text-4xl mb-3 block"></i><p class="text-lg">暂无站点</p><p class="text-sm mt-1">点击"创建站点"开始安装您的第一个WordPress网站</p></td></tr>
                    </tbody></table></div>
                </div>
            </div>

            <!-- 创建站点 -->
            <div v-if="currentPage === 'create'" class="p-8 fade-in">
                <div class="max-w-3xl mx-auto">
                    <div class="bg-white rounded-xl card-shadow p-8">
                        <div class="flex gap-4 mb-8">
                            <button @click="openCreateModal('single')" :class="['flex-1 py-3 rounded-lg font-semibold transition', createForm.mode === 'single' ? 'btn-primary text-white' : 'border hover:bg-gray-50']"><i class="fas fa-plus mr-2"></i>单个创建</button>
                            <button @click="openCreateModal('batch')" :class="['flex-1 py-3 rounded-lg font-semibold transition', createForm.mode === 'batch' ? 'btn-primary text-white' : 'border hover:bg-gray-50']"><i class="fas fa-layer-group mr-2"></i>批量创建</button>
                        </div>
                        <div v-if="!panelConnected" class="mb-6 bg-red-50 border border-red-200 rounded-lg p-4"><p class="text-red-700 text-sm"><i class="fas fa-exclamation-triangle mr-2"></i>1Panel未连接，站点将仅保存到本地，不会实际安装WordPress。</p></div>
                        <div v-else class="mb-6 bg-green-50 border border-green-200 rounded-lg p-4"><p class="text-green-700 text-sm"><i class="fas fa-check-circle mr-2"></i>1Panel已连接，站点将通过1Panel API实际安装WordPress到服务器。</p></div>
                        <div v-if="createForm.mode === 'single'" class="mb-6"><label class="block text-sm font-medium text-gray-700 mb-1">域名 / 站点名称</label><input v-model="createForm.site_name" type="text" placeholder="例如: site1.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"><p class="text-xs text-gray-500 mt-1">将作为WordPress站点的主域名，同时用于1Panel创建网站</p></div>
                        <div v-else class="mb-6"><label class="block text-sm font-medium text-gray-700 mb-1">域名列表（每行一个）</label><textarea v-model="createForm.domains" rows="6" placeholder="site1.example.com&#10;site2.example.com&#10;site3.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></textarea><p class="text-xs text-gray-500 mt-1">每个域名将创建一个独立的WordPress站点，端口自动递增避免冲突</p></div>
                        <div v-if="createForm.mode === 'single'" class="mb-6"><label class="block text-sm font-medium text-gray-700 mb-1">URL（可选，留空则自动生成）</label><input v-model="createForm.url" type="text" placeholder="http://site1.example.com" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div>
                        <div class="grid grid-cols-2 gap-6 mb-6"><div><label class="block text-sm font-medium text-gray-700 mb-1">WP 管理员用户名</label><input v-model="createForm.admin_name" type="text" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">WP 管理员密码</label><input v-model="createForm.admin_password" type="text" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div></div>
                        <div class="grid grid-cols-2 gap-6 mb-6"><div><label class="block text-sm font-medium text-gray-700 mb-1">标签</label><input v-model="createForm.tag" type="text" placeholder="例如: 生产环境" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">安全ID</label><input v-model="createForm.security_id" type="text" placeholder="安全标识" class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></div></div>
                        <div class="bg-gray-50 rounded-lg p-4 mb-6"><h4 class="text-sm font-semibold text-gray-700 mb-3"><i class="fas fa-shield-alt mr-2"></i>HTTP 认证</h4><div class="grid grid-cols-2 gap-4"><div><label class="block text-xs font-medium text-gray-600 mb-1">HTTP 用户名</label><input v-model="createForm.http_username" type="text" placeholder="可选" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"></div><div><label class="block text-xs font-medium text-gray-600 mb-1">HTTP 密码</label><input v-model="createForm.http_password" type="text" placeholder="可选" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"></div></div></div>
                        <div class="bg-gray-50 rounded-lg p-4 mb-6"><h4 class="text-sm font-semibold text-gray-700 mb-3"><i class="fas fa-certificate mr-2"></i>SSL 配置</h4><div class="grid grid-cols-2 gap-4"><div><label class="block text-xs font-medium text-gray-600 mb-1">验证证书</label><select v-model="createForm.verify_certificate" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><option :value="true">是 (1)</option><option :value="false">否 (0)</option></select></div><div><label class="block text-xs font-medium text-gray-600 mb-1">SSL 版本</label><select v-model="createForm.ssl_version" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><option value="auto">自动</option><option value="1.3">TLS 1.3</option><option value="1.2">TLS 1.2</option><option value="1.1">TLS 1.1</option><option value="1.0">TLS 1.0</option></select></div></div></div>

                        <!-- 插件选择 -->
                        <div class="bg-gray-50 rounded-lg p-4 mb-6">
                            <div class="flex items-center justify-between mb-3">
                                <h4 class="text-sm font-semibold text-gray-700"><i class="fas fa-plug mr-2"></i>预装插件</h4>
                                <button @click="currentPage = 'plugins'" class="text-xs text-indigo-600 hover:text-indigo-800"><i class="fas fa-upload mr-1"></i>上传插件</button>
                            </div>
                            <div v-if="!plugins.filter(p => p.enabled).length" class="text-sm text-gray-400 py-2">暂无可用插件，请先上传 .zip 格式的WordPress插件</div>
                            <div v-else class="space-y-2">
                                <div v-for="p in plugins.filter(p => p.enabled)" :key="p.id"
                                    @click="togglePluginSelection(p.id)"
                                    :class="['flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition border', createForm.selected_plugins.includes(p.id) ? 'bg-indigo-50 border-indigo-300' : 'bg-white border-gray-200 hover:border-indigo-200']">
                                    <i :class="[createForm.selected_plugins.includes(p.id) ? 'fas fa-check-square text-indigo-600' : 'far fa-square text-gray-400']" class="text-lg"></i>
                                    <div class="flex-1">
                                        <p class="text-sm font-medium">{{ p.name }}</p>
                                        <p class="text-xs text-gray-500">{{ p.filename }} · {{ formatSize(p.file_size) }}</p>
                                    </div>
                                </div>
                            </div>
                            <p class="text-xs text-gray-500 mt-2">选中的插件将在WordPress安装完成后自动复制到站点并启用</p>
                        </div>

                        <div class="bg-gray-50 rounded-lg p-4 mb-6"><h4 class="text-sm font-semibold text-gray-700 mb-3"><i class="fas fa-server mr-2"></i>服务器配置</h4><div class="grid grid-cols-2 gap-4"><div><label class="block text-xs font-medium text-gray-600 mb-1">起始端口</label><input v-model.number="createForm.base_port" type="number" min="1024" max="65535" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><p class="text-xs text-gray-400 mt-1">自动检测冲突，按顺序分配可用端口</p></div><div><label class="block text-xs font-medium text-gray-600 mb-1">数据库服务</label><select v-model="createForm.db_service" class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500"><option value="mariadb">MariaDB</option><option value="mysql">MySQL</option></select></div></div></div>

                        <!-- 创建进度 -->
                        <div v-if="createProgress.show" class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
                            <h4 class="text-sm font-semibold text-blue-800 mb-2"><i class="fas fa-spinner fa-spin mr-2"></i>{{ createProgress.message }}</h4>
                            <div v-if="createProgress.total > 1" class="w-full bg-blue-200 rounded-full h-2 mb-2"><div class="bg-blue-600 h-2 rounded-full transition-all" :style="{width: (createProgress.current / createProgress.total * 100) + '%'}"></div></div>
                            <div v-for="r in createProgress.results" :key="r.domain" class="text-xs mt-1"><span :class="r.status === 'success' ? 'text-green-600' : 'text-red-600'"><i :class="r.status === 'success' ? 'fas fa-check' : 'fas fa-times'" class="mr-1"></i>{{ r.domain }} - {{ r.message }}</span></div>
                        </div>

                        <button @click="submitCreate" :disabled="loading" class="w-full btn-primary text-white py-3 rounded-lg font-semibold hover:shadow-lg transition">
                            <i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i><i v-else class="fas fa-rocket mr-2"></i>
                            {{ createForm.mode === 'single' ? '创建WordPress站点' : '批量创建WordPress站点' }}
                        </button>
                    </div>
                </div>
            </div>

            <!-- 插件管理 -->
            <div v-if="currentPage === 'plugins'" class="p-8 fade-in">
                <div class="max-w-4xl mx-auto">
                    <!-- 上传区域 -->
                    <div class="bg-white rounded-xl card-shadow p-6 mb-6">
                        <h3 class="font-semibold text-gray-800 mb-4"><i class="fas fa-upload mr-2 text-indigo-500"></i>上传插件</h3>
                        <div class="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-indigo-400 transition cursor-pointer" @click="$refs.pluginFile.click()">
                            <i class="fas fa-cloud-upload-alt text-4xl text-gray-400 mb-3"></i>
                            <p class="text-gray-600 mb-1">点击上传 WordPress 插件 (.zip)</p>
                            <p class="text-xs text-gray-400">支持标准WordPress插件zip格式，上传后可在创建站点时选择安装</p>
                            <input ref="pluginFile" type="file" accept=".zip" @change="handlePluginUpload" class="hidden">
                        </div>
                    </div>
                    <!-- 插件列表 -->
                    <div class="bg-white rounded-xl card-shadow">
                        <div class="px-6 py-4 border-b flex items-center justify-between">
                            <h3 class="font-semibold text-gray-800">已上传插件</h3>
                            <span class="text-sm text-gray-500">共 {{ plugins.length }} 个</span>
                        </div>
                        <div v-if="!plugins.length" class="p-8 text-center text-gray-400">
                            <i class="fas fa-puzzle-piece text-4xl mb-3"></i>
                            <p>暂无插件，请上传WordPress插件的.zip文件</p>
                        </div>
                        <div v-else class="divide-y">
                            <div v-for="p in plugins" :key="p.id" class="px-6 py-4 flex items-center gap-4 hover:bg-gray-50 transition">
                                <div class="w-10 h-10 rounded-lg flex items-center justify-center" :class="p.enabled ? 'bg-indigo-100' : 'bg-gray-100'">
                                    <i class="fas fa-puzzle-piece" :class="p.enabled ? 'text-indigo-600' : 'text-gray-400'"></i>
                                </div>
                                <div class="flex-1 min-w-0">
                                    <p class="font-medium text-sm">{{ p.name }}</p>
                                    <p class="text-xs text-gray-500">{{ p.filename }} · {{ formatSize(p.file_size) }}</p>
                                </div>
                                <div class="flex items-center gap-3">
                                    <button @click="handleTogglePlugin(p)" :class="p.enabled ? 'text-green-600 hover:text-green-800' : 'text-gray-400 hover:text-gray-600'" :title="p.enabled ? '点击禁用' : '点击启用'">
                                        <i :class="p.enabled ? 'fas fa-toggle-on text-xl' : 'fas fa-toggle-off text-xl'"></i>
                                    </button>
                                    <button @click="handleDeletePlugin(p)" class="text-red-400 hover:text-red-600" title="删除">
                                        <i class="fas fa-trash"></i>
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 设置 -->
            <div v-if="currentPage === 'settings'" class="p-8 fade-in">
                <div class="max-w-3xl mx-auto space-y-6">
                    <div class="bg-white rounded-xl card-shadow p-6"><h3 class="font-semibold text-gray-800 mb-4"><i class="fas fa-sliders-h mr-2 text-indigo-500"></i>默认WordPress配置</h3><div class="space-y-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">默认管理员用户名</label><input v-model="globalConfig.default_admin_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">默认管理员密码</label><input v-model="globalConfig.default_admin_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"><p class="text-xs text-gray-500 mt-1">应用于所有新创建的WordPress站点</p></div><div><label class="block text-sm font-medium text-gray-700 mb-1">默认数据库服务</label><select v-model="globalConfig.db_service" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"><option value="mariadb">MariaDB</option><option value="mysql">MySQL</option></select></div></div></div>
                    <button @click="saveGlobalConfig" :disabled="loading" class="w-full btn-primary text-white py-3 rounded-lg font-semibold"><i class="fas fa-save mr-2"></i>保存设置</button>
                </div>
            </div>
        </main>

        <!-- 编辑弹窗 -->
        <div v-if="showEditModal" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl max-h-[90vh] overflow-y-auto w-full max-w-2xl mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between"><h2 class="text-lg font-bold">编辑站点</h2><button @click="showEditModal = false" class="text-gray-400 hover:text-gray-600"><i class="fas fa-times text-xl"></i></button></div>
                <div class="p-6 space-y-4">
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">站点名称</label><input v-model="editForm.site_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">URL</label><input v-model="editForm.url" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">管理员</label><input v-model="editForm.admin_name" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">管理员密码</label><input v-model="editForm.admin_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">标签</label><input v-model="editForm.tag" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">安全ID</label><input v-model="editForm.security_id" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">HTTP 用户名</label><input v-model="editForm.http_username" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div><div><label class="block text-sm font-medium text-gray-700 mb-1">HTTP 密码</label><input v-model="editForm.http_password" type="text" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></div></div>
                    <div class="grid grid-cols-2 gap-4"><div><label class="block text-sm font-medium text-gray-700 mb-1">验证证书</label><select v-model="editForm.verify_certificate" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"><option :value="true">是 (1)</option><option :value="false">否 (0)</option></select></div><div><label class="block text-sm font-medium text-gray-700 mb-1">SSL 版本</label><select v-model="editForm.ssl_version" class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"><option value="auto">自动</option><option value="1.3">TLS 1.3</option><option value="1.2">TLS 1.2</option><option value="1.1">TLS 1.1</option><option value="1.0">TLS 1.0</option></select></div></div>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end"><button @click="showEditModal = false" class="px-6 py-2 border rounded-lg hover:bg-gray-50">取消</button><button @click="submitEdit" :disabled="loading" class="btn-primary text-white px-6 py-2 rounded-lg"><i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>保存更改</button></div>
            </div>
        </div>

        <!-- 确认弹窗 -->
        <div v-if="modal.show" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 fade-in"><div class="p-6"><h2 class="text-lg font-bold text-gray-800 mb-2">{{ modal.title }}</h2><p class="text-gray-600">{{ modal.content }}</p></div><div class="p-6 border-t flex gap-3 justify-end"><button @click="modal.show = false" class="px-6 py-2 border rounded-lg hover:bg-gray-50">取消</button><button @click="modal.onConfirm()" class="bg-red-500 text-white px-6 py-2 rounded-lg hover:bg-red-600">删除</button></div></div>
        </div>

        <!-- 提示 -->
        <div v-if="toast.show" class="toast fade-in">
            <div :class="['rounded-lg shadow-lg px-6 py-4 flex items-center gap-3', toast.type === 'success' ? 'bg-green-500 text-white' : toast.type === 'error' ? 'bg-red-500 text-white' : 'bg-blue-500 text-white']">
                <i :class="toast.type === 'success' ? 'fas fa-check-circle' : toast.type === 'error' ? 'fas fa-exclamation-circle' : 'fas fa-info-circle'"></i>
                <span>{{ toast.message }}</span>
            </div>
        </div>
    </div>
    `,
});

app.mount('#app');
