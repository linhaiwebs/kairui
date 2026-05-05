# ============================================================
# WordPress Site Manager - Makefile
# ============================================================

.PHONY: help install dev start stop restart status docker-build docker-up docker-down docker-logs clean

# Default
help: ## 显示帮助信息
	@echo "WordPress 站点管理器 - 命令列表"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ---- 本地部署 ----
install: ## 安装依赖
	@bash start.sh install

dev: ## 开发模式启动 (前台)
	@bash start.sh dev

start: ## 生产模式启动 (后台)
	@bash start.sh start

stop: ## 停止服务
	@bash start.sh stop

restart: ## 重启服务
	@bash start.sh restart

status: ## 查看服务状态
	@bash start.sh status

# ---- Docker 部署 ----
docker-build: ## 构建 Docker 镜像
	docker build -t wp-site-manager:latest .

docker-up: ## 启动 Docker 容器 (后台)
	docker compose up -d

docker-down: ## 停止 Docker 容器
	docker compose down

docker-logs: ## 查看 Docker 日志
	docker compose logs -f

docker-restart: ## 重启 Docker 容器
	docker compose restart

# ---- 清理 ----
clean: ## 清理临时文件
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf venv/
	rm -rf logs/
	rm -f app.pid

distclean: clean ## 深度清理 (包括数据库)
	rm -f backend/wp_manager.db
	rm -f backend/plugins/*.zip
