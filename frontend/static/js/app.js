const { createApp, ref, reactive, computed, onMounted, watch, nextTick } = Vue;

const app = createApp({
    setup() {
        // ---- State ----
        const isLoggedIn = ref(false);
        const currentUser = ref('');
        const currentPage = ref('dashboard');
        const loading = ref(false);
        const toast = reactive({ show: false, message: '', type: 'success' });
        const modal = reactive({ show: false, title: '', content: '', onConfirm: null });

        // Login form
        const loginForm = reactive({ username: '', password: '' });
        const loginError = ref('');

        // Sites
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

        // Panel data
        const panelConnected = ref(false);
        const panelWebsites = ref([]);
        const panelInstalledApps = ref([]);
        const panelGroups = ref([]);

        // Create site form
        const showCreateModal = ref(false);
        const createForm = reactive({
            mode: 'single', // 'single' or 'batch'
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
            // Batch fields
            domains: '',
            base_port: 8081,
            db_service: 'mariadb',
            website_group_id: 1,
        });

        // Edit site
        const showEditModal = ref(false);
        const editForm = reactive({});
        const editingSiteId = ref('');

        // Config
        const globalConfig = reactive({
            default_admin_name: 'admin',
            default_admin_password: '',
            default_plugins: [],
            default_themes: [],
            db_service: 'mariadb',
        });

        // ---- Methods ----
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

        async function handleLogin() {
            loginError.value = '';
            loading.value = true;
            try {
                const resp = await API.login(loginForm.username, loginForm.password);
                if (resp.code === 200) {
                    isLoggedIn.value = true;
                    currentUser.value = resp.data.username;
                    showToast('Login successful');
                    loadInitialData();
                } else {
                    loginError.value = resp.message || 'Invalid credentials';
                }
            } catch (e) {
                loginError.value = 'Connection error';
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
                }
            } catch (e) {
                console.error('Failed to load sites:', e);
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
                console.error('Failed to load panel data:', e);
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
                console.error('Failed to load config:', e);
            }
        }

        async function refreshSites() {
            loading.value = true;
            try {
                await Promise.all([loadSites(), loadPanelData()]);
                showToast('Data refreshed');
            } finally {
                loading.value = false;
            }
        }

        // ---- Create Site ----
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
            showCreateModal.value = true;
        }

        async function submitCreate() {
            loading.value = true;
            try {
                if (createForm.mode === 'single') {
                    // Create via 1Panel
                    if (panelConnected.value) {
                        const domain = createForm.site_name;
                        const alias = domain.replace(/\./g, '-');

                        // Get WordPress app info
                        const appResp = await API.panelSearchApps('wordpress');
                        if (appResp.code !== 200 || !appResp.data?.items?.length) {
                            showToast('Failed to find WordPress app in 1Panel', 'error');
                            loading.value = false;
                            return;
                        }

                        const wpApp = appResp.data.items[0];
                        const versions = wpApp.versions || [];
                        const version = versions[0] || '6.9.4';

                        // Get app detail
                        const detailResp = await API.panelGetAppDetail(wpApp.id, version);
                        if (detailResp.code !== 200) {
                            showToast('Failed to get WordPress app detail', 'error');
                            loading.value = false;
                            return;
                        }
                        const appDetailId = detailResp.data.id;

                        // Get DB services
                        const dbResp = await API.panelGetAppServices(createForm.db_service);
                        if (dbResp.code !== 200 || !dbResp.data?.length) {
                            showToast('Failed to get database service', 'error');
                            loading.value = false;
                            return;
                        }
                        const dbService = dbResp.data[0];

                        // Find available port
                        let port = createForm.base_port;
                        const usedPorts = new Set(
                            panelInstalledApps.value
                                .filter(a => a.appKey === 'wordpress')
                                .map(a => a.httpPort)
                        );
                        while (usedPorts.has(port)) port++;

                        // Generate DB credentials
                        const dbSuffix = Math.random().toString(36).substring(2, 8);
                        const installData = {
                            appDetailId: appDetailId,
                            name: alias,
                            params: {
                                PANEL_DB_TYPE: createForm.db_service,
                                PANEL_DB_NAME: `wp_${dbSuffix}`,
                                PANEL_DB_USER: `wp_${dbSuffix}`,
                                PANEL_DB_USER_PASSWORD: Math.random().toString(36).substring(2, 14),
                                PANEL_APP_PORT_HTTP: port,
                            },
                            services: { [createForm.db_service]: dbService.value },
                        };

                        const installResp = await API.panelInstallApp(installData);
                        if (installResp.code !== 200) {
                            showToast(`Install failed: ${installResp.message}`, 'error');
                            loading.value = false;
                            return;
                        }

                        // Wait a bit for the app to register
                        await new Promise(r => setTimeout(r, 2000));

                        // Find the newly installed app
                        const newInstalled = await API.panelSearchInstalled('wordpress');
                        let appInstallId = null;
                        if (newInstalled.code === 200) {
                            const newApp = newInstalled.data.items.find(a => a.name === alias);
                            if (newApp) appInstallId = newApp.id;
                        }

                        // Create website in 1Panel
                        const websiteResp = await API.panelCreateWebsite({
                            primaryDomain: domain,
                            alias: alias,
                            appType: appInstallId ? 'installed' : 'new',
                            appInstallID: appInstallId,
                            appDetailID: appDetailId,
                            appID: wpApp.id,
                            webSiteGroupID: createForm.website_group_id || 1,
                            proxy: `http://127.0.0.1:${port}`,
                            remark: createForm.tag,
                        });

                        let panelWebsiteId = null;
                        if (websiteResp.code === 200) {
                            panelWebsiteId = websiteResp.data?.id || websiteResp.data;
                        }

                        // Save to local DB
                        await API.createSite({
                            site_name: createForm.site_name,
                            url: createForm.url || `http://${domain}`,
                            admin_name: createForm.admin_name,
                            admin_password: createForm.admin_password,
                            tag: createForm.tag,
                            security_id: createForm.security_id,
                            http_username: createForm.http_username,
                            http_password: createForm.http_password,
                            verify_certificate: createForm.verify_certificate,
                            ssl_version: createForm.ssl_version,
                            panel_website_id: panelWebsiteId,
                            panel_app_install_id: appInstallId,
                            panel_app_detail_id: appDetailId,
                        });
                    } else {
                        // Save locally only
                        await API.createSite({
                            site_name: createForm.site_name,
                            url: createForm.url,
                            admin_name: createForm.admin_name,
                            admin_password: createForm.admin_password,
                            tag: createForm.tag,
                            security_id: createForm.security_id,
                            http_username: createForm.http_username,
                            http_password: createForm.http_password,
                            verify_certificate: createForm.verify_certificate,
                            ssl_version: createForm.ssl_version,
                        });
                    }
                    showToast('Site created successfully');
                } else {
                    // Batch create
                    const domains = createForm.domains.split('\n')
                        .map(d => d.trim())
                        .filter(d => d.length > 0);

                    if (!domains.length) {
                        showToast('Please enter at least one domain', 'error');
                        loading.value = false;
                        return;
                    }

                    const resp = await API.batchCreateWordPress({
                        domains,
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
                    });

                    if (resp.code === 200) {
                        const { success, error, total } = resp.data;
                        showToast(`Created ${success}/${total} sites (${error} errors)`);
                    } else {
                        showToast(`Batch create failed: ${resp.message}`, 'error');
                    }
                }

                showCreateModal.value = false;
                await loadSites();
                await loadPanelData();
            } catch (e) {
                showToast(`Error: ${e.message}`, 'error');
            } finally {
                loading.value = false;
            }
        }

        // ---- Edit Site ----
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
                showToast('Site updated');
                await loadSites();
            } catch (e) {
                showToast('Update failed', 'error');
            } finally {
                loading.value = false;
            }
        }

        // ---- Delete Site ----
        function confirmDelete(site) {
            showModal(
                'Delete Site',
                `Are you sure you want to delete "${site.site_name}"? This will also delete the WordPress application from 1Panel.`,
                async () => {
                    loading.value = true;
                    try {
                        if (site.panel_website_id && panelConnected.value) {
                            await API.panelDeleteWebsite(site.panel_website_id, true);
                        }
                        if (site.panel_app_install_id && panelConnected.value) {
                            // Operate: delete the installed app
                            // Not needed as deleteWebsite with deleteApp should handle it
                        }
                        await API.deleteSite(site.id);
                        showToast('Site deleted');
                        await loadSites();
                        await loadPanelData();
                    } catch (e) {
                        showToast('Delete failed', 'error');
                    } finally {
                        loading.value = false;
                    }
                    modal.show = false;
                }
            );
        }

        // ---- Save Config ----
        async function saveGlobalConfig() {
            loading.value = true;
            try {
                await API.saveConfig(globalConfig);
                showToast('Configuration saved');
            } catch (e) {
                showToast('Failed to save config', 'error');
            } finally {
                loading.value = false;
            }
        }

        // ---- Init ----
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
            globalConfig,
            handleLogin, handleLogout, refreshSites,
            openCreateModal, submitCreate,
            openEditModal, submitEdit,
            confirmDelete, saveGlobalConfig,
            showToast, showModal,
        };
    },

    template: `
    <!-- Login Page -->
    <div v-if="!isLoggedIn" class="min-h-screen flex items-center justify-center login-bg">
        <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md fade-in">
            <div class="text-center mb-8">
                <div class="w-16 h-16 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-2xl flex items-center justify-center mx-auto mb-4">
                    <i class="fab fa-wordpress text-white text-3xl"></i>
                </div>
                <h1 class="text-2xl font-bold text-gray-800">WordPress Site Manager</h1>
                <p class="text-gray-500 mt-2">Sign in to manage your WordPress sites</p>
            </div>
            <form @submit.prevent="handleLogin">
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 mb-1">Username</label>
                    <input v-model="loginForm.username" type="text" required
                        class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:border-indigo-500"
                        placeholder="Enter username">
                </div>
                <div class="mb-6">
                    <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
                    <input v-model="loginForm.password" type="password" required
                        class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:border-indigo-500"
                        placeholder="Enter password">
                </div>
                <p v-if="loginError" class="text-red-500 text-sm mb-4">{{ loginError }}</p>
                <button type="submit" :disabled="loading"
                    class="w-full btn-primary text-white py-3 rounded-lg font-semibold hover:shadow-lg transition">
                    <i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>
                    <span v-else>Sign In</span>
                </button>
            </form>
        </div>
    </div>

    <!-- Main App -->
    <div v-else class="min-h-screen flex">
        <!-- Sidebar -->
        <aside class="w-64 sidebar-gradient text-white flex flex-col">
            <div class="p-6 border-b border-indigo-700">
                <div class="flex items-center gap-3">
                    <div class="w-10 h-10 bg-white bg-opacity-20 rounded-lg flex items-center justify-center">
                        <i class="fab fa-wordpress text-xl"></i>
                    </div>
                    <div>
                        <h2 class="font-bold text-lg">WP Manager</h2>
                        <p class="text-xs text-indigo-300">Site Administration</p>
                    </div>
                </div>
            </div>

            <nav class="flex-1 p-4 space-y-1">
                <a @click="currentPage = 'dashboard'" 
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'dashboard' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-tachometer-alt w-5 text-center"></i>
                    <span>Dashboard</span>
                </a>
                <a @click="currentPage = 'sites'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'sites' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-globe w-5 text-center"></i>
                    <span>Sites</span>
                    <span class="ml-auto bg-indigo-500 text-xs px-2 py-0.5 rounded-full">{{ sites.length }}</span>
                </a>
                <a @click="currentPage = 'create'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'create' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-plus-circle w-5 text-center"></i>
                    <span>Create Site</span>
                </a>
                <a @click="currentPage = 'settings'"
                    :class="['flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition', currentPage === 'settings' ? 'bg-white bg-opacity-20' : 'hover:bg-white hover:bg-opacity-10']">
                    <i class="fas fa-cog w-5 text-center"></i>
                    <span>Settings</span>
                </a>
            </nav>

            <div class="p-4 border-t border-indigo-700">
                <div class="flex items-center gap-3 px-2">
                    <div class="w-8 h-8 bg-indigo-500 rounded-full flex items-center justify-center">
                        <i class="fas fa-user text-sm"></i>
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="text-sm font-medium truncate">{{ currentUser }}</p>
                        <p class="text-xs text-indigo-300">Administrator</p>
                    </div>
                    <button @click="handleLogout" class="text-indigo-300 hover:text-white">
                        <i class="fas fa-sign-out-alt"></i>
                    </button>
                </div>
            </div>
        </aside>

        <!-- Main Content -->
        <main class="flex-1 overflow-auto">
            <!-- Header -->
            <header class="bg-white border-b px-8 py-4 flex items-center justify-between">
                <div>
                    <h1 class="text-xl font-bold text-gray-800">
                        {{ currentPage === 'dashboard' ? 'Dashboard' : currentPage === 'sites' ? 'Site Management' : currentPage === 'create' ? 'Create WordPress Site' : 'Settings' }}
                    </h1>
                    <p class="text-sm text-gray-500">
                        <span :class="panelConnected ? 'text-green-500' : 'text-red-500'">
                            <i :class="panelConnected ? 'fas fa-circle' : 'fas fa-circle'"></i>
                            {{ panelConnected ? '1Panel Connected' : '1Panel Disconnected' }}
                        </span>
                    </p>
                </div>
                <div class="flex gap-3">
                    <button @click="refreshSites" class="px-4 py-2 border rounded-lg hover:bg-gray-50 transition text-sm">
                        <i class="fas fa-sync-alt mr-2" :class="{'fa-spin': loading}"></i>Refresh
                    </button>
                </div>
            </header>

            <!-- Dashboard Page -->
            <div v-if="currentPage === 'dashboard'" class="p-8 fade-in">
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                    <div class="bg-white rounded-xl p-6 card-shadow">
                        <div class="flex items-center justify-between">
                            <div>
                                <p class="text-sm text-gray-500">Total Sites</p>
                                <p class="text-3xl font-bold text-gray-800 mt-1">{{ sites.length }}</p>
                            </div>
                            <div class="w-12 h-12 bg-indigo-100 rounded-lg flex items-center justify-center">
                                <i class="fas fa-globe text-indigo-600 text-xl"></i>
                            </div>
                        </div>
                    </div>
                    <div class="bg-white rounded-xl p-6 card-shadow">
                        <div class="flex items-center justify-between">
                            <div>
                                <p class="text-sm text-gray-500">Active Sites</p>
                                <p class="text-3xl font-bold text-green-600 mt-1">{{ sites.filter(s => s.status === 'active').length }}</p>
                            </div>
                            <div class="w-12 h-12 bg-green-100 rounded-lg flex items-center justify-center">
                                <i class="fas fa-check-circle text-green-600 text-xl"></i>
                            </div>
                        </div>
                    </div>
                    <div class="bg-white rounded-xl p-6 card-shadow">
                        <div class="flex items-center justify-between">
                            <div>
                                <p class="text-sm text-gray-500">1Panel Websites</p>
                                <p class="text-3xl font-bold text-purple-600 mt-1">{{ panelWebsites.length }}</p>
                            </div>
                            <div class="w-12 h-12 bg-purple-100 rounded-lg flex items-center justify-center">
                                <i class="fas fa-server text-purple-600 text-xl"></i>
                            </div>
                        </div>
                    </div>
                    <div class="bg-white rounded-xl p-6 card-shadow">
                        <div class="flex items-center justify-between">
                            <div>
                                <p class="text-sm text-gray-500">Installed Apps</p>
                                <p class="text-3xl font-bold text-orange-600 mt-1">{{ panelInstalledApps.length }}</p>
                            </div>
                            <div class="w-12 h-12 bg-orange-100 rounded-lg flex items-center justify-center">
                                <i class="fas fa-cubes text-orange-600 text-xl"></i>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Panel Websites Table -->
                <div class="bg-white rounded-xl card-shadow mb-8">
                    <div class="px-6 py-4 border-b flex items-center justify-between">
                        <h3 class="font-semibold text-gray-800">1Panel Websites</h3>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full">
                            <thead>
                                <tr class="bg-gray-50 text-left text-sm text-gray-600">
                                    <th class="px-6 py-3">Domain</th>
                                    <th class="px-6 py-3">Type</th>
                                    <th class="px-6 py-3">Status</th>
                                    <th class="px-6 py-3">App</th>
                                    <th class="px-6 py-3">SSL</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="w in panelWebsites" :key="w.id" class="border-t table-row">
                                    <td class="px-6 py-3 font-medium">{{ w.primaryDomain }}</td>
                                    <td class="px-6 py-3"><span class="badge bg-blue-100 text-blue-800">{{ w.type }}</span></td>
                                    <td class="px-6 py-3">
                                        <span :class="w.status === 'Running' ? 'status-running' : 'status-stopped'">
                                            <i class="fas fa-circle text-xs mr-1"></i>{{ w.status }}
                                        </span>
                                    </td>
                                    <td class="px-6 py-3">{{ w.appName || '-' }}</td>
                                    <td class="px-6 py-3">
                                        <span :class="w.sslStatus === 'success' ? 'text-green-500' : 'text-red-500'" class="badge"
                                            :style="{background: w.sslStatus === 'success' ? '#dcfce7' : '#fee2e2', color: w.sslStatus === 'success' ? '#166534' : '#991b1b'}">
                                            {{ w.sslStatus === 'success' ? 'SSL Active' : 'No SSL' }}
                                        </span>
                                    </td>
                                </tr>
                                <tr v-if="!panelWebsites.length">
                                    <td colspan="5" class="px-6 py-8 text-center text-gray-400">
                                        <i class="fas fa-inbox text-4xl mb-2"></i>
                                        <p>No websites in 1Panel</p>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Installed Apps -->
                <div class="bg-white rounded-xl card-shadow">
                    <div class="px-6 py-4 border-b flex items-center justify-between">
                        <h3 class="font-semibold text-gray-800">1Panel Installed Applications</h3>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 p-6">
                        <div v-for="a in panelInstalledApps" :key="a.id"
                            class="border rounded-lg p-4 hover:shadow-md transition">
                            <div class="flex items-center justify-between mb-2">
                                <h4 class="font-semibold">{{ a.appName }}</h4>
                                <span :class="a.status === 'Running' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'"
                                    class="badge">{{ a.status }}</span>
                            </div>
                            <p class="text-sm text-gray-500">v{{ a.version }}</p>
                            <p class="text-sm text-gray-500" v-if="a.httpPort">Port: {{ a.httpPort }}</p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Sites Page -->
            <div v-if="currentPage === 'sites'" class="p-8 fade-in">
                <div class="bg-white rounded-xl card-shadow">
                    <div class="px-6 py-4 border-b flex items-center justify-between flex-wrap gap-4">
                        <div class="flex items-center gap-4 flex-1">
                            <div class="relative flex-1 max-w-md">
                                <i class="fas fa-search absolute left-3 top-3 text-gray-400"></i>
                                <input v-model="searchQuery" type="text" placeholder="Search sites..."
                                    class="w-full pl-10 pr-4 py-2 border rounded-lg focus:border-indigo-500">
                            </div>
                        </div>
                        <div class="flex gap-3">
                            <button @click="exportCSV" class="px-4 py-2 border rounded-lg hover:bg-gray-50 text-sm">
                                <i class="fas fa-download mr-2"></i>Export CSV
                            </button>
                            <button @click="openCreateModal('single')" class="btn-primary text-white px-4 py-2 rounded-lg text-sm">
                                <i class="fas fa-plus mr-2"></i>Add Site
                            </button>
                        </div>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full">
                            <thead>
                                <tr class="bg-gray-50 text-left text-xs text-gray-600 uppercase">
                                    <th class="px-4 py-3">Site Name</th>
                                    <th class="px-4 py-3">URL</th>
                                    <th class="px-4 py-3">Admin Name</th>
                                    <th class="px-4 py-3">Admin Password</th>
                                    <th class="px-4 py-3">Tag</th>
                                    <th class="px-4 py-3">Security ID</th>
                                    <th class="px-4 py-3">HTTP User</th>
                                    <th class="px-4 py-3">HTTP Pass</th>
                                    <th class="px-4 py-3">Verify Cert</th>
                                    <th class="px-4 py-3">SSL Version</th>
                                    <th class="px-4 py-3">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="s in filteredSites" :key="s.id" class="border-t table-row">
                                    <td class="px-4 py-3 font-medium text-sm">{{ s.site_name }}</td>
                                    <td class="px-4 py-3">
                                        <a :href="s.url" target="_blank" class="text-indigo-600 hover:underline text-sm">{{ s.url }}</a>
                                    </td>
                                    <td class="px-4 py-3 text-sm">{{ s.admin_name || '-' }}</td>
                                    <td class="px-4 py-3 text-sm">
                                        <span class="font-mono text-xs bg-gray-100 px-2 py-1 rounded">{{ s.admin_password ? '••••••' : '-' }}</span>
                                    </td>
                                    <td class="px-4 py-3"><span class="badge bg-indigo-100 text-indigo-800" v-if="s.tag">{{ s.tag }}</span><span v-else>-</span></td>
                                    <td class="px-4 py-3 text-sm">{{ s.security_id || '-' }}</td>
                                    <td class="px-4 py-3 text-sm">{{ s.http_username || '-' }}</td>
                                    <td class="px-4 py-3 text-sm">
                                        <span class="font-mono text-xs" v-if="s.http_password">••••••</span>
                                        <span v-else>-</span>
                                    </td>
                                    <td class="px-4 py-3 text-sm">
                                        <i :class="s.verify_certificate ? 'fas fa-check-circle text-green-500' : 'fas fa-times-circle text-red-500'"></i>
                                        {{ s.verify_certificate ? '1' : '0' }}
                                    </td>
                                    <td class="px-4 py-3 text-sm">{{ s.ssl_version || 'auto' }}</td>
                                    <td class="px-4 py-3">
                                        <div class="flex gap-2">
                                            <button @click="openEditModal(s)" class="text-indigo-600 hover:text-indigo-800" title="Edit">
                                                <i class="fas fa-edit"></i>
                                            </button>
                                            <button @click="confirmDelete(s)" class="text-red-500 hover:text-red-700" title="Delete">
                                                <i class="fas fa-trash"></i>
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                                <tr v-if="!filteredSites.length">
                                    <td colspan="11" class="px-6 py-12 text-center text-gray-400">
                                        <i class="fas fa-inbox text-4xl mb-3 block"></i>
                                        <p class="text-lg">No sites found</p>
                                        <p class="text-sm mt-1">Create your first WordPress site to get started</p>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Create Page -->
            <div v-if="currentPage === 'create'" class="p-8 fade-in">
                <div class="max-w-3xl mx-auto">
                    <div class="bg-white rounded-xl card-shadow p-8">
                        <div class="flex gap-4 mb-8">
                            <button @click="openCreateModal('single')"
                                :class="['flex-1 py-3 rounded-lg font-semibold transition', createForm.mode === 'single' ? 'btn-primary text-white' : 'border hover:bg-gray-50']">
                                <i class="fas fa-plus mr-2"></i>Single Site
                            </button>
                            <button @click="openCreateModal('batch')"
                                :class="['flex-1 py-3 rounded-lg font-semibold transition', createForm.mode === 'batch' ? 'btn-primary text-white' : 'border hover:bg-gray-50']">
                                <i class="fas fa-layer-group mr-2"></i>Batch Create
                            </button>
                        </div>

                        <!-- Domain Input -->
                        <div v-if="createForm.mode === 'single'" class="mb-6">
                            <label class="block text-sm font-medium text-gray-700 mb-1">Domain / Site Name</label>
                            <input v-model="createForm.site_name" type="text" placeholder="e.g., site1.example.com"
                                class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500">
                            <p class="text-xs text-gray-500 mt-1">This will be used as the primary domain for the WordPress site</p>
                        </div>
                        <div v-else class="mb-6">
                            <label class="block text-sm font-medium text-gray-700 mb-1">Domains (one per line)</label>
                            <textarea v-model="createForm.domains" rows="6" placeholder="site1.example.com&#10;site2.example.com&#10;site3.example.com"
                                class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500"></textarea>
                            <p class="text-xs text-gray-500 mt-1">Each domain will create a separate WordPress site</p>
                        </div>

                        <!-- URL -->
                        <div v-if="createForm.mode === 'single'" class="mb-6">
                            <label class="block text-sm font-medium text-gray-700 mb-1">URL (optional, auto-generated if empty)</label>
                            <input v-model="createForm.url" type="text" placeholder="http://site1.example.com"
                                class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500">
                        </div>

                        <div class="grid grid-cols-2 gap-6 mb-6">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">WP Admin Name</label>
                                <input v-model="createForm.admin_name" type="text"
                                    class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">WP Admin Password</label>
                                <input v-model="createForm.admin_password" type="text"
                                    class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500">
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-6 mb-6">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Tag</label>
                                <input v-model="createForm.tag" type="text" placeholder="e.g., production"
                                    class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Security ID</label>
                                <input v-model="createForm.security_id" type="text" placeholder="Security identifier"
                                    class="w-full px-4 py-3 border rounded-lg focus:border-indigo-500">
                            </div>
                        </div>

                        <div class="bg-gray-50 rounded-lg p-4 mb-6">
                            <h4 class="text-sm font-semibold text-gray-700 mb-3">
                                <i class="fas fa-shield-alt mr-2"></i>HTTP Authentication
                            </h4>
                            <div class="grid grid-cols-2 gap-4">
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1">HTTP Username</label>
                                    <input v-model="createForm.http_username" type="text" placeholder="Optional"
                                        class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1">HTTP Password</label>
                                    <input v-model="createForm.http_password" type="text" placeholder="Optional"
                                        class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                                </div>
                            </div>
                        </div>

                        <div class="bg-gray-50 rounded-lg p-4 mb-6">
                            <h4 class="text-sm font-semibold text-gray-700 mb-3">
                                <i class="fas fa-certificate mr-2"></i>SSL Configuration
                            </h4>
                            <div class="grid grid-cols-2 gap-4">
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1">Verify Certificate</label>
                                    <select v-model="createForm.verify_certificate"
                                        class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                                        <option :value="true">Yes (1)</option>
                                        <option :value="false">No (0)</option>
                                    </select>
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1">SSL Version</label>
                                    <select v-model="createForm.ssl_version"
                                        class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                                        <option value="auto">Auto</option>
                                        <option value="1.3">TLS 1.3</option>
                                        <option value="1.2">TLS 1.2</option>
                                        <option value="1.1">TLS 1.1</option>
                                        <option value="1.0">TLS 1.0</option>
                                    </select>
                                </div>
                            </div>
                        </div>

                        <div v-if="createForm.mode === 'batch'" class="bg-gray-50 rounded-lg p-4 mb-6">
                            <h4 class="text-sm font-semibold text-gray-700 mb-3">
                                <i class="fas fa-server mr-2"></i>Server Configuration
                            </h4>
                            <div class="grid grid-cols-2 gap-4">
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1">Starting Port</label>
                                    <input v-model.number="createForm.base_port" type="number" min="1024" max="65535"
                                        class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                                </div>
                                <div>
                                    <label class="block text-xs font-medium text-gray-600 mb-1">Database Service</label>
                                    <select v-model="createForm.db_service"
                                        class="w-full px-3 py-2 border rounded-lg text-sm focus:border-indigo-500">
                                        <option value="mariadb">MariaDB</option>
                                        <option value="mysql">MySQL</option>
                                    </select>
                                </div>
                            </div>
                        </div>

                        <button @click="submitCreate" :disabled="loading"
                            class="w-full btn-primary text-white py-3 rounded-lg font-semibold hover:shadow-lg transition">
                            <i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>
                            <i v-else class="fas fa-rocket mr-2"></i>
                            {{ createForm.mode === 'single' ? 'Create WordPress Site' : 'Batch Create WordPress Sites' }}
                        </button>
                    </div>
                </div>
            </div>

            <!-- Settings Page -->
            <div v-if="currentPage === 'settings'" class="p-8 fade-in">
                <div class="max-w-3xl mx-auto space-y-6">
                    <!-- Global Defaults -->
                    <div class="bg-white rounded-xl card-shadow p-6">
                        <h3 class="font-semibold text-gray-800 mb-4">
                            <i class="fas fa-sliders-h mr-2 text-indigo-500"></i>Default WordPress Configuration
                        </h3>
                        <div class="space-y-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Default Admin Name</label>
                                <input v-model="globalConfig.default_admin_name" type="text"
                                    class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Default Admin Password</label>
                                <input v-model="globalConfig.default_admin_password" type="text"
                                    class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                                <p class="text-xs text-gray-500 mt-1">Applied to all newly created WordPress sites</p>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-1">Default Database Service</label>
                                <select v-model="globalConfig.db_service"
                                    class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                                    <option value="mariadb">MariaDB</option>
                                    <option value="mysql">MySQL</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <!-- Default Plugins -->
                    <div class="bg-white rounded-xl card-shadow p-6">
                        <h3 class="font-semibold text-gray-800 mb-4">
                            <i class="fas fa-plug mr-2 text-indigo-500"></i>Default Plugins (for reference)
                        </h3>
                        <textarea v-model="globalConfig.default_plugins" rows="4"
                            placeholder="Enter plugin names, one per line"
                            class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></textarea>
                        <p class="text-xs text-gray-500 mt-1">These plugins will be noted for manual installation on new sites</p>
                    </div>

                    <!-- Default Themes -->
                    <div class="bg-white rounded-xl card-shadow p-6">
                        <h3 class="font-semibold text-gray-800 mb-4">
                            <i class="fas fa-palette mr-2 text-indigo-500"></i>Default Themes (for reference)
                        </h3>
                        <textarea v-model="globalConfig.default_themes" rows="4"
                            placeholder="Enter theme names, one per line"
                            class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500"></textarea>
                        <p class="text-xs text-gray-500 mt-1">These themes will be noted for manual installation on new sites</p>
                    </div>

                    <button @click="saveGlobalConfig" :disabled="loading"
                        class="w-full btn-primary text-white py-3 rounded-lg font-semibold">
                        <i class="fas fa-save mr-2"></i>Save Settings
                    </button>
                </div>
            </div>
        </main>

        <!-- Create/Edit Modal -->
        <div v-if="showCreateModal" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl max-h-[90vh] overflow-y-auto w-full max-w-2xl mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between">
                    <h2 class="text-lg font-bold">{{ createForm.mode === 'single' ? 'Add WordPress Site' : 'Batch Create Sites' }}</h2>
                    <button @click="showCreateModal = false" class="text-gray-400 hover:text-gray-600">
                        <i class="fas fa-times text-xl"></i>
                    </button>
                </div>
                <div class="p-6">
                    <p class="text-gray-500">Please use the Create Site page to configure and create WordPress sites.</p>
                    <button @click="showCreateModal = false; currentPage = 'create'"
                        class="mt-4 btn-primary text-white px-6 py-2 rounded-lg">
                        Go to Create Page
                    </button>
                </div>
            </div>
        </div>

        <!-- Edit Modal -->
        <div v-if="showEditModal" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl max-h-[90vh] overflow-y-auto w-full max-w-2xl mx-4 fade-in">
                <div class="p-6 border-b flex items-center justify-between">
                    <h2 class="text-lg font-bold">Edit Site</h2>
                    <button @click="showEditModal = false" class="text-gray-400 hover:text-gray-600">
                        <i class="fas fa-times text-xl"></i>
                    </button>
                </div>
                <div class="p-6 space-y-4">
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Site Name</label>
                            <input v-model="editForm.site_name" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">URL</label>
                            <input v-model="editForm.url" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Admin Name</label>
                            <input v-model="editForm.admin_name" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Admin Password</label>
                            <input v-model="editForm.admin_password" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Tag</label>
                            <input v-model="editForm.tag" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Security ID</label>
                            <input v-model="editForm.security_id" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">HTTP Username</label>
                            <input v-model="editForm.http_username" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">HTTP Password</label>
                            <input v-model="editForm.http_password" type="text"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                        </div>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Verify Certificate</label>
                            <select v-model="editForm.verify_certificate"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                                <option :value="true">Yes (1)</option>
                                <option :value="false">No (0)</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">SSL Version</label>
                            <select v-model="editForm.ssl_version"
                                class="w-full px-4 py-2 border rounded-lg focus:border-indigo-500">
                                <option value="auto">Auto</option>
                                <option value="1.3">TLS 1.3</option>
                                <option value="1.2">TLS 1.2</option>
                                <option value="1.1">TLS 1.1</option>
                                <option value="1.0">TLS 1.0</option>
                            </select>
                        </div>
                    </div>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end">
                    <button @click="showEditModal = false" class="px-6 py-2 border rounded-lg hover:bg-gray-50">Cancel</button>
                    <button @click="submitEdit" :disabled="loading" class="btn-primary text-white px-6 py-2 rounded-lg">
                        <i v-if="loading" class="fas fa-spinner fa-spin mr-2"></i>
                        Save Changes
                    </button>
                </div>
            </div>
        </div>

        <!-- Confirm Modal -->
        <div v-if="modal.show" class="fixed inset-0 z-50 flex items-center justify-center modal-overlay">
            <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md mx-4 fade-in">
                <div class="p-6">
                    <h2 class="text-lg font-bold text-gray-800 mb-2">{{ modal.title }}</h2>
                    <p class="text-gray-600">{{ modal.content }}</p>
                </div>
                <div class="p-6 border-t flex gap-3 justify-end">
                    <button @click="modal.show = false" class="px-6 py-2 border rounded-lg hover:bg-gray-50">Cancel</button>
                    <button @click="modal.onConfirm()" class="bg-red-500 text-white px-6 py-2 rounded-lg hover:bg-red-600">Delete</button>
                </div>
            </div>
        </div>

        <!-- Toast -->
        <div v-if="toast.show" class="toast fade-in">
            <div :class="['rounded-lg shadow-lg px-6 py-4 flex items-center gap-3',
                toast.type === 'success' ? 'bg-green-500 text-white' : toast.type === 'error' ? 'bg-red-500 text-white' : 'bg-blue-500 text-white']">
                <i :class="toast.type === 'success' ? 'fas fa-check-circle' : toast.type === 'error' ? 'fas fa-exclamation-circle' : 'fas fa-info-circle'"></i>
                <span>{{ toast.message }}</span>
            </div>
        </div>
    </div>
    `,
});

app.mount('#app');
