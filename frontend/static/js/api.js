// API client for the WordPress Site Manager backend
const API = {
    token: sessionStorage.getItem('wp_token') || '',

    async request(method, url, data = null) {
        const headers = {
            'Content-Type': 'application/json',
        };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        const opts = { method, headers };
        if (data && method !== 'GET') {
            opts.body = JSON.stringify(data);
        }

        try {
            const resp = await fetch(url, opts);
            
            if (resp.status === 401 && !url.includes('/auth/login')) {
                this.logout();
                throw new Error('登录已过期，请重新登录');
            }
            
            const result = await resp.json();
            return result;
        } catch (e) {
            if (e.message.includes('登录已过期')) throw e;
            // Network errors, timeouts, etc.
            return { code: 503, message: `网络错误: ${e.message}`, data: null };
        }
    },
    
    setToken(token) {
        this.token = token;
        sessionStorage.setItem('wp_token', token);
    },
    
    logout() {
        this.token = '';
        sessionStorage.removeItem('wp_token');
    },
    
    // Auth
    async login(username, password) {
        const resp = await this.request('POST', '/api/auth/login', { username, password });
        if (resp.code === 200 && resp.data?.token) {
            this.setToken(resp.data.token);
        }
        return resp;
    },
    
    async checkAuth() {
        return this.request('GET', '/api/auth/check');
    },
    
    // Sites
    async getSites() {
        return this.request('GET', '/api/sites');
    },
    async getNextPort() {
        return this.request('GET', '/api/sites/next-port');
    },
    
    async createSite(data) {
        return this.request('POST', '/api/sites', data);
    },
    
    async updateSite(id, data) {
        return this.request('PUT', `/api/sites/${id}`, data);
    },
    
    async deleteSite(id) {
        return this.request('DELETE', `/api/sites/${id}`);
    },

    async fixWebsite(siteId) {
        return this.request('POST', `/api/sites/${siteId}/fix-website`);
    },
    
    async exportCSV() {
        const headers = { 'Authorization': `Bearer ${this.token}` };
        try {
            const resp = await fetch('/api/sites/export/csv', { headers });
            if (!resp.ok) throw new Error(`导出失败: HTTP ${resp.status}`);
            const blob = await resp.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'wordpress_sites.csv';
            a.click();
            window.URL.revokeObjectURL(url);
        } catch (e) {
            return { code: 500, message: e.message };
        }
    },
    
    // Config
    async getConfig() {
        return this.request('GET', '/api/config');
    },
    
    async saveConfig(data) {
        return this.request('PUT', '/api/config', data);
    },
    
    // DeepSeek
    async deepseekVerify(apiKey) {
        return this.request('POST', '/api/deepseek/verify', { api_key: apiKey });
    },

    // Crawlbase
    async crawlbaseVerify(apiKey) {
        return this.request('POST', '/api/crawlbase/verify', { api_key: apiKey });
    },

    // CloakBrowser profiles (local directories)
    async listCloakbrowserProfiles() {
        return this.request('GET', '/api/cloakbrowser/profiles');
    },
    async createCloakbrowserProfile(name, googleEmail = '', proxy = '', country = 'US', platform = null) {
        return this.request('POST', '/api/cloakbrowser/profiles', { name, google_email: googleEmail, proxy, country, platform });
    },
    async testCloakbrowserProfile(profileName) {
        return this.request('POST', '/api/cloakbrowser/profiles/test', { profile_name: profileName });
    },
    async updateCloakbrowserProfile(name, data) {
        return this.request('PUT', `/api/cloakbrowser/profiles/${encodeURIComponent(name)}`, data);
    },
    async deleteCloakbrowserProfile(name) {
        return this.request('DELETE', `/api/cloakbrowser/profiles/${encodeURIComponent(name)}`);
    },

    // Google Merchant Center
    async generateFeed(siteId) {
        return this.request('POST', `/api/sites/${siteId}/generate-feed`);
    },
    async verifyGoogleSite(siteId, verificationCode, method = 'meta') {
        return this.request('POST', `/api/sites/${siteId}/verify-google-site`, { verification_code: verificationCode, method });
    },
    async registerMC(siteId, profileDir, googleEmail = '') {
        return this.request('POST', `/api/sites/${siteId}/register-mc`, { profile_dir: profileDir, google_email: googleEmail });
    },
    async getMCStatus(siteId) {
        return this.request('GET', `/api/sites/${siteId}/mc-status`);
    },

    // GMC async tasks with log streaming
    async taskGenerateFeed(siteId) {
        return this.request('POST', '/api/tasks/generate-feed', { site_id: siteId });
    },
    async taskRegisterMC(siteId, profileDir, googleEmail = '') {
        return this.request('POST', '/api/tasks/register-mc', { site_id: siteId, profile_dir: profileDir, google_email: googleEmail });
    },
    async taskGmcRecon(siteId, profileDir, onboardingUrl = '') {
        return this.request('POST', '/api/tasks/gmc-recon', { site_id: siteId, profile_dir: profileDir, onboarding_url: onboardingUrl });
    },
    async getTaskLogs(taskId, after = 0) {
        return this.request('GET', `/api/tasks/${encodeURIComponent(taskId)}/logs?after=${after}`);
    },
    async cancelTask(taskId) {
        return this.request('POST', `/api/tasks/${encodeURIComponent(taskId)}/cancel`);
    },


    // Server Environments
    async listPanelEnvironments() {
        return this.request('GET', '/api/panel/environments');
    },
    async createPanelEnvironment(data) {
        return this.request('POST', '/api/panel/environments', data);
    },
    async updatePanelEnvironment(id, data) {
        return this.request('PUT', `/api/panel/environments/${id}`, data);
    },
    async deletePanelEnvironment(id) {
        return this.request('DELETE', `/api/panel/environments/${id}`);
    },
    async setDefaultPanelEnvironment(id) {
        return this.request('PUT', `/api/panel/environments/${id}/default`);
    },
    async getCurrentPanelEnvironment() {
        return this.request('GET', '/api/panel/environments/current');
    },
    
    // Batch create static sites
    async batchCreateStaticSite(data) {
        return this.request('POST', '/api/sites/create-static', data);
    },

    // Batch create (legacy WordPress, kept for backward compatibility)
    async batchCreateWordPress(data) {
        return this.request('POST', '/api/wordpress/batch-create', data);
    },

    // WordPress install status (legacy)
    async getWPInstallStatus(siteId) {
        return this.request('GET', `/api/wordpress/install-status/${siteId}`);
    },

    async installTheme(siteId) {
        return this.request('POST', `/api/sites/${siteId}/install-theme`);
    },

    async installPlugins(siteId, pluginIds) {
        return this.request('POST', `/api/sites/${siteId}/install-plugins`, { plugin_ids: pluginIds });
    },

    // Cloudflare Accounts
    async cfListAccounts() {
        return this.request('GET', '/api/cloudflare/accounts');
    },

    async cfCreateAccount(data) {
        return this.request('POST', '/api/cloudflare/accounts', data);
    },

    async cfDeleteAccount(id) {
        return this.request('DELETE', `/api/cloudflare/accounts/${id}`);
    },

    async cfSetDefaultAccount(id) {
        return this.request('PUT', `/api/cloudflare/accounts/${id}/default`);
    },

    async cfUpdateAccount(id, data) {
        return this.request('PUT', `/api/cloudflare/accounts/${id}`, data);
    },

    // Cloudflare
    async cfVerifyToken(apiToken, notes = '') {
        return this.request('POST', '/api/cloudflare/verify', { api_token: apiToken, notes });
    },


    async cfListZones(accountId) {
        let url = '/api/cloudflare/zones';
        if (accountId) url += `?account_id=${accountId}`;
        return this.request('GET', url);
    },

    async cfListDnsRecords(zoneId, accountId, page = 1, perPage = 10) {
        let url = `/api/cloudflare/dns-records/${zoneId}?page=${page}&per_page=${perPage}`;
        if (accountId) url += `&account_id=${accountId}`;
        return this.request('GET', url);
    },

    async cfUpdateDnsRecord(zoneId, recordId, data) {
        return this.request('PUT', `/api/cloudflare/dns-records/${zoneId}/${recordId}`, data);
    },

    async cfDeleteDnsRecord(zoneId, recordId, accountId) {
        let url = `/api/cloudflare/dns-records/${zoneId}/${recordId}`;
        if (accountId) url += `?account_id=${accountId}`;
        return this.request('DELETE', url);
    },

    async cfCreateDns(siteId, data) {
        return this.request('POST', `/api/sites/${siteId}/dns`, data);
    },

    async cfCreateDnsRecord(zoneId, data) {
        return this.request('POST', `/api/cloudflare/dns-records/${zoneId}`, data);
    },

    async cfStatus(accountId) {
        let url = '/api/cloudflare/status';
        if (accountId) url += `?account_id=${accountId}`;
        return this.request('GET', url);
    },

    // Feed Product Statistics (dashboard)
    async getFeedStats() {
        return this.request('GET', '/api/feed/stats');
    },

    // Analytics (Kairui Tracker)
    async getDashboardAnalytics() {
        return this.request('GET', '/api/analytics/dashboard');
    },
    async getAnalytics(siteId, path, params = {}) {
        const qs = new URLSearchParams({ site_id: siteId, ...params }).toString();
        return this.request('GET', `/api/analytics/${path}?${qs}`);
    },
    async setAnalyticsKey(target, apiKey) {
        return this.request('POST', '/api/analytics/key', { target, api_key: apiKey });
    },

    async getPaymentEvents(siteUrl) {
        let url = '/api/analytics/payment-events';
        if (siteUrl) url += '?site_url=' + encodeURIComponent(siteUrl);
        return this.request('GET', url);
    },

    // 筛品 - Walmart Bestsellers (Crawlbase)
    async getWalmartCategories() {
        return this.request('GET', '/api/shai-pin/walmart/categories');
    },
    async fetchWalmartBestsellers(category, limit = 100) {
        return this.request('POST', '/api/shai-pin/walmart/fetch', { category, limit });
    },
    async loadWalmartProducts(category = '') {
        const params = category ? `?category=${category}` : '';
        return this.request('GET', `/api/shai-pin/walmart/products${params}`);
    },
    async exportWalmartData(data, category, format) {
        const resp = await fetch('/api/shai-pin/walmart/export', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${this.token}`
            },
            body: JSON.stringify({ data, category, format })
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ message: '导出失败' }));
            throw new Error(err.message || '导出失败');
        }
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const ext = format === 'json' ? 'json' : 'xlsx';
        a.download = `walmart_${category}_${Date.now()}.${ext}`;
        a.click();
        window.URL.revokeObjectURL(url);
    },

    // 筛品 - Feed生成 (product detail enrichment)
    async enrichWalmartProducts(urls, category) {
        return this.request('POST', '/api/shai-pin/walmart/enrich', { urls, category });
    },
    async listGeneratedFeed(siteId, page, limit) {
        const params = new URLSearchParams();
        if (siteId) params.set("site_id", siteId);
        if (page) params.set("page", page);
        if (limit) params.set("limit", limit || 50);
        const q = params.toString() ? "?" + params.toString() : "";
        return this.request('GET', '/api/shai-pin/feed/list' + q);
    },
    async clearGeneratedFeed() {
        return this.request('DELETE', '/api/shai-pin/feed/clear');
    },
    async deleteFeedItems(ids) {
        return this.request('DELETE', '/api/shai-pin/feed/items', { ids });
    },

    // 筛品 - 爆品导入 (Amazon search & convert)
    async searchAmazonProducts(productNames) {
        return this.request('POST', '/api/shai-pin/amazon/search', { product_names: productNames });
    },
    async convertAmazonToFeed(products) {
        return this.request('POST', '/api/shai-pin/amazon/convert', { products });
    },
    async loadAmazonSearchResults() {
        return this.request('GET', '/api/shai-pin/amazon/search-results');
    },
    async deleteAmazonSearchResults(ids) {
        return this.request('DELETE', '/api/shai-pin/amazon/search-results', { ids });
    },

    // WooCommerce Products
    async convertToWooCommerce(products) {
        return this.request('POST', '/api/shai-pin/woocommerce/convert', { products });
    },

    async getWooCommerceProducts(siteId, page, limit) {
        const params = new URLSearchParams();
        if (siteId) params.set("site_id", siteId);
        if (page) params.set("page", page);
        if (limit) params.set("limit", limit || 50);
        const q = params.toString() ? "?" + params.toString() : "";
        return this.request('GET', '/api/shai-pin/woocommerce/products' + q);
    },

    async deleteWooCommerceProducts(ids) {
        return this.request('DELETE', '/api/shai-pin/woocommerce/products', { ids });
    },

    // Site Sync
    async syncFeedToSite(siteId) {
        return this.request('POST', '/api/shai-pin/feed/sync-to-site', { site_id: siteId });
    },

    async cleanFeedFromSite(siteId) {
        return this.request('DELETE', '/api/shai-pin/feed/sync-to-site', { site_id: siteId });
    },

    async syncWooToSite(siteId) {
        return this.request('POST', '/api/shai-pin/woocommerce/sync-to-site', { site_id: siteId });
    },

    async cleanWooFromSite(siteId) {
        return this.request('DELETE', '/api/shai-pin/woocommerce/sync-to-site', { site_id: siteId });
    },

    // ---- Run Products (跑品) ----

    async getRunProductCategories() {
        return this.request('GET', '/api/run-products/categories');
    },

    async listRunProducts(category, page, perPage) {
        let url = '/api/run-products/list?page=' + (page || 1) + '&per_page=' + (perPage || 20);
        if (category) url += '&category=' + encodeURIComponent(category);
        return this.request('GET', url);
    },

    async importRunCsv(file) {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await fetch('/api/run-products/import-csv', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + (this.token || '') },
            body: formData,
        });
        return resp;  // Return raw Response for streaming NDJSON
    },

    async importLocalRunCsv(filePath, removeAfter = false) {
        return this.request('POST', '/api/run-products/import-local-csv', { file_path: filePath, remove_after: removeAfter });
    },

    async deleteRunProducts(ids) {
        return this.request('DELETE', '/api/run-products/items', { ids });
    },

    async clearRunProducts() {
        return this.request('DELETE', '/api/run-products/clear');
    },

    async syncRunFeedToSite(siteId, runProductIds) {
        return this.request('POST', '/api/run-products/sync-to-site', { site_id: siteId, run_product_ids: runProductIds });
    },

    async cleanRunFeedFromSite(siteId) {
        return this.request('DELETE', '/api/run-products/sync-to-site', { site_id: siteId });
    },

    async importCsvProducts(siteId, file, action) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('action', action || 'preview');
        const resp = await fetch('/api/sites/' + siteId + '/import-csv', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + (this.token || '') },
            body: formData,
        });
        return await resp.json();
    },

    // Feed Products (Google Merchant Center)
    async getFeedProducts(siteId) {
        return this.request('GET', `/api/sites/${siteId}/feed-products`);
    },

    async createFeedProduct(siteId, data) {
        return this.request('POST', `/api/sites/${siteId}/feed-products`, data);
    },

    async updateFeedProduct(productId, data) {
        return this.request('PUT', `/api/feed-products/${productId}`, data);
    },

    async deleteFeedProduct(productId) {
        return this.request('DELETE', `/api/feed-products/${productId}`);
    },

    async createSampleFeedProducts(siteId) {
        return this.request('POST', `/api/sites/${siteId}/feed-products/sample`);
    },

    async exportFeedProducts(siteId) {
        const resp = await fetch(`/api/sites/${siteId}/feed-products/export`, {
            headers: { 'Authorization': `Bearer ${this.token}` }
        });
        if (!resp.ok) throw new Error('Export failed');
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `feed_${siteId}.xml`;
        a.click();
        window.URL.revokeObjectURL(url);
        return { code: 200, message: '导出成功' };
    },

    // Post-Install WordPress & WooCommerce Configuration
    async getWpSettings(siteId) {
        return this.request('GET', `/api/sites/${siteId}/wp-settings`);
    },
    async updateWpSettings(siteId, data) {
        return this.request('POST', `/api/sites/${siteId}/wp-settings`, data);
    },
    async getWooStatus(siteId) {
        return this.request('GET', `/api/sites/${siteId}/woo-status`);
    },
    async installWooCommerce(siteId) {
        return this.request('POST', `/api/sites/${siteId}/woo-install`);
    },
    async getWooSettings(siteId, group) {
        return this.request('GET', `/api/sites/${siteId}/woo-settings/${group}`);
    },
    async updateWooSettings(siteId, group, data) {
        return this.request('POST', `/api/sites/${siteId}/woo-settings/${group}`, data);
    },

    // AI-Powered WordPress Configuration
    async aiConfig(siteId, brandName) {
        return this.request('POST', `/api/sites/${siteId}/ai-config`, { brand_name: brandName });
    },
    async getAiConfigStatus(siteId, configKey) {
        return this.request('GET', `/api/sites/${siteId}/ai-config/status?config_key=${encodeURIComponent(configKey)}`);
    },

    // Simplified WooCommerce Configuration
    async saveWooConfig(siteId, data) {
        return this.request('POST', `/api/sites/${siteId}/woo-config`, data);
    },

    // Brand Kits
    async getBrandKits() {
        return this.request('GET', '/api/brand-kits');
    },
    async createBrandKit(data) {
        return this.request('POST', '/api/brand-kits', data);
    },
    async getBrandKit(id) {
        return this.request('GET', `/api/brand-kits/${id}`);
    },
    async updateBrandKit(id, data) {
        return this.request('PUT', `/api/brand-kits/${id}`, data);
    },
    async deleteBrandKit(id, mode = 'release') {
        return this.request('DELETE', `/api/brand-kits/${id}?mode=${mode}`);
    },
    async generateBrandKit(id) {
        return this.request('POST', `/api/brand-kits/${id}/generate`);
    },
    async getBrandKitStatus(id) {
        return this.request('GET', `/api/brand-kits/${id}/status`);
    },

    // Open CloakBrowser for a site
    async openSiteBrowser(siteId) {
        return this.request('POST', `/api/sites/${siteId}/open-browser`);
    },

    // ---- Demo Import (WoodMart) ----
    async getPrebuiltDemos(siteId) {
        return this.request('GET', `/api/sites/${siteId}/prebuilt-demos`);
    },
    async importPrebuiltDemo(siteId, demoId) {
        return this.request('POST', `/api/sites/${siteId}/prebuilt-demos/import`, { demo_id: demoId });
    },
    async getPrebuiltDemoStatus(siteId, demoId) {
        return this.request('GET', `/api/sites/${siteId}/prebuilt-demos/status?demo_id=${encodeURIComponent(demoId)}`);
    },

    // ---- Proxy Pool ----
    async getProxies() {
        return this.request('GET', '/api/proxies');
    },
    async getAvailableProxies() {
        return this.request('GET', '/api/proxies/available');
    },
    async importProxies() {
        return this.request('POST', '/api/proxies/import');
    },
    async importProxiesText(text, proxyType = 'http') {
        return this.request('POST', '/api/proxies/import-text', { text, proxy_type: proxyType });
    },

    // ---- Google Account Pool ----
    async getGoogleAccounts() {
        return this.request('GET', '/api/google-accounts');
    },
    async getAvailableGoogleAccounts() {
        return this.request('GET', '/api/google-accounts/available');
    },
    async importGoogleAccounts(text) {
        return this.request('POST', '/api/google-accounts/import', { text });
    },
    async deleteGoogleAccount(id) {
        return this.request('DELETE', `/api/google-accounts/${id}`);
    },

    async downloadBrandKitFile(id, filename) {
        const headers = { 'Authorization': `Bearer ${this.token}` };
        try {
            const resp = await fetch(`/api/brand-kits/${id}/download/${filename}`, { headers });
            if (!resp.ok) throw new Error(`下载失败: HTTP ${resp.status}`);
            const blob = await resp.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            window.URL.revokeObjectURL(url);
        } catch (e) {
            return { code: 500, message: e.message };
        }
    },

    // ---- Brand Kit Application (site creation step 5) ----
    async applyBrandKit(siteId, data) {
        const resp = await fetch(`/api/sites/${siteId}/apply-brand-kit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
            body: JSON.stringify(data),
        });
        return await resp.json();
    },
    async getApplyBrandKitStatus(siteId, configKey) {
        const resp = await fetch(`/api/sites/${siteId}/apply-brand-kit/status?config_key=${configKey}`, {
            headers: { 'Authorization': `Bearer ${this.token}` },
        });
        return await resp.json();
    },

    // ---- Unified Brand Config (AI + WooCommerce + Logo + Footer) ----
    async brandConfig(siteId, data) {
        const resp = await fetch(`/api/sites/${siteId}/brand-config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${this.token}` },
            body: JSON.stringify(data),
        });
        return await resp.json();
    },
    async getBrandConfigStatus(siteId, configKey) {
        const resp = await fetch(`/api/sites/${siteId}/brand-config/status?config_key=${configKey}`, {
            headers: { 'Authorization': `Bearer ${this.token}` },
        });
        return await resp.json();
    },
    async saveBrandKitConfig(kitId, data) {
        return this.request('PUT', `/api/brand-kits/${kitId}/config`, data);
    },

    async installCfSsl(siteId) {
        const resp = await fetch(`/api/sites/${siteId}/install-cf-ssl`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${this.token}` },
        });
        return await resp.json();
    },

    // User Management (admin only)
    async getUsers() {
        return this.request('GET', '/api/users');
    },
    async createUser(data) {
        return this.request('POST', '/api/users', data);
    },
    async updateUser(id, data) {
        return this.request('PUT', `/api/users/${id}`, data);
    },
    async deleteUser(id) {
        return this.request('DELETE', `/api/users/${id}`);
    },

    // Fingerprint Categories
    async getFingerprintCategories() {
        return this.request('GET', '/api/fingerprint-categories');
    },
    async createFingerprintCategory(data) {
        return this.request('POST', '/api/fingerprint-categories', data);
    },
    async deleteFingerprintCategory(id) {
        return this.request('DELETE', `/api/fingerprint-categories/${id}`);
    },

    
    // System Export / Import
    async exportSystem() {
        return this.request('GET', '/api/system/export');
    },
    async importSystem(data) {
        return this.request('POST', '/api/system/import', data);
    },

    // Profile Category Mapping (CloakBrowser profiles ← categories)
    async getProfileCategories() {
        return this.request('GET', '/api/profile-categories');
    },
    async setProfileCategory(profileName, categoryId) {
        return this.request('PUT', `/api/cloakbrowser/profiles/${encodeURIComponent(profileName)}/category`, { category_id: categoryId });
    },

};
