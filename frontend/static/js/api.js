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
        
        const resp = await fetch(url, opts);
        const result = await resp.json();
        
        if (resp.status === 401) {
            this.logout();
            throw new Error('Session expired');
        }
        
        return result;
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
    
    async exportCSV() {
        const headers = { 'Authorization': `Bearer ${this.token}` };
        const resp = await fetch('/api/sites/export/csv', { headers });
        const blob = await resp.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'wordpress_sites.csv';
        a.click();
        window.URL.revokeObjectURL(url);
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
    
    // Batch create
    async batchCreateWordPress(data) {
        return this.request('POST', '/api/wordpress/batch-create', data);
    },
};
