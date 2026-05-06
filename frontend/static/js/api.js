// API client for the WordPress Site Manager backend
const API = {
    token: localStorage.getItem('wp_token') || '',
    
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
            
            if (resp.status === 401) {
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
        localStorage.setItem('wp_token', token);
    },
    
    logout() {
        this.token = '';
        localStorage.removeItem('wp_token');
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
    
    // Panel proxy
    async panelSearchApps(name = '') {
        return this.request('POST', '/api/panel/apps/search', { name, page: 1, pageSize: 100 });
    },
    
    async panelSearchInstalled(name = '') {
        return this.request('POST', '/api/panel/apps/installed/search', { name, page: 1, pageSize: 100 });
    },
    
    async panelGetAppDetail(appId, version) {
        return this.request('GET', `/api/panel/apps/detail/${appId}/${version}`);
    },
    
    async panelInstallApp(data) {
        return this.request('POST', '/api/panel/apps/install', data);
    },
    
    async panelGetAppServices(key) {
        return this.request('GET', `/api/panel/apps/services/${key}`);
    },
    
    async panelSearchWebsites(name = '') {
        return this.request('POST', '/api/panel/websites/search', { name, page: 1, pageSize: 100 });
    },
    
    async panelGetWebsitesList() {
        return this.request('GET', '/api/panel/websites/list');
    },
    
    async panelCreateWebsite(data) {
        return this.request('POST', '/api/panel/websites/create', data);
    },
    
    async panelCheckWebsite(data) {
        return this.request('POST', '/api/panel/websites/check', data);
    },
    
    async panelDeleteWebsite(id, deleteApp = true) {
        return this.request('DELETE', `/api/panel/websites/${id}`, { deleteApp, deleteBackup: true });
    },
    
    async panelOperateWebsite(id, operate) {
        return this.request('POST', '/api/panel/websites/operate', { id, operate });
    },
    
    async panelSearchGroups() {
        return this.request('POST', '/api/panel/groups/search', { type: 'website' });
    },
    
    async panelStatus() {
        return this.request('GET', '/api/panel/status');
    },

    async panelSync(importOrphans = false) {
        return this.request('POST', '/api/panel/sync', { import_orphans: importOrphans });
    },
    
    // Batch create
    async batchCreateWordPress(data) {
        return this.request('POST', '/api/wordpress/batch-create', data);
    },

    // Plugins
    async getPlugins() {
        return this.request('GET', '/api/plugins');
    },

    async uploadPlugin(formData) {
        const headers = { 'Authorization': `Bearer ${this.token}` };
        try {
            const resp = await fetch('/api/plugins', { method: 'POST', headers, body: formData });
            return resp.json();
        } catch (e) {
            return { code: 503, message: `上传失败: ${e.message}` };
        }
    },

    async deletePlugin(id) {
        return this.request('DELETE', `/api/plugins/${id}`);
    },

    async togglePlugin(id) {
        return this.request('POST', `/api/plugins/${id}/toggle`);
    },

    // WordPress install status
    async getWPInstallStatus(siteId) {
        return this.request('GET', `/api/wordpress/install-status/${siteId}`);
    },

    // Themes
    async getThemes() {
        return this.request('GET', '/api/themes');
    },

    async uploadTheme(formData) {
        const headers = { 'Authorization': `Bearer ${this.token}` };
        try {
            const resp = await fetch('/api/themes/upload', { method: 'POST', headers, body: formData });
            return resp.json();
        } catch (e) {
            return { code: 503, message: `上传失败: ${e.message}` };
        }
    },

    async deleteTheme(id) {
        return this.request('DELETE', `/api/themes/${id}`);
    },

    async installTheme(siteId, themeIds) {
        return this.request('POST', `/api/sites/${siteId}/install-theme`, { theme_ids: themeIds });
    },

    async installPlugins(siteId, pluginIds) {
        return this.request('POST', `/api/sites/${siteId}/install-plugins`, { plugin_ids: pluginIds });
    },

    // Cloudflare
    async cfVerifyToken(apiToken) {
        return this.request('POST', '/api/cloudflare/verify', { api_token: apiToken });
    },

    async cfListZones() {
        return this.request('GET', '/api/cloudflare/zones');
    },

    async cfListDnsRecords(zoneId) {
        return this.request('GET', `/api/cloudflare/dns-records/${zoneId}`);
    },

    async cfCreateDns(siteId, data) {
        return this.request('POST', `/api/sites/${siteId}/dns`, data);
    },

    async cfStatus() {
        return this.request('GET', '/api/cloudflare/status');
    },
};
