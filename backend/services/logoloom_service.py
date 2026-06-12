"""
LogoLoom MCP Service — Python wrapper for @mcpware/logoloom.

Communicates with the LogoLoom MCP server via JSON-RPC 2.0 over stdin/stdout.
Provides SVG processing tools: text-to-path, optimize, brand kit export, image-to-svg.
Also integrates with DeepSeek for AI-powered SVG logo generation.
"""

import json
import logging
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# Module-level singleton
_server_process: subprocess.Popen | None = None
_server_lock = threading.RLock()
_request_id = 0
_request_id_lock = threading.Lock()
_logoloom_available: bool | None = None  # None=not checked, True/False


class LogoLoomError(Exception):
    """Raised when LogoLoom operations fail."""
    pass


class LogoLoomService:
    """Python client for LogoLoom MCP server (@mcpware/logoloom).

    Uses a persistent subprocess with JSON-RPC 2.0 over stdin/stdout.
    Thread-safe: all methods use a module-level lock for server access.
    """

    MCP_TIMEOUT = 60
    EXPORT_TIMEOUT = 120

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    # Path to locally installed LogoLoom (npm install in /app/logoloom/)
    _LOCOLOOM_PATH = "/app/logoloom/node_modules/@mcpware/logoloom/src/server.mjs"

    @classmethod
    def is_available(cls) -> bool:
        """Check if LogoLoom can be started. Caches result."""
        global _logoloom_available
        if _logoloom_available is not None:
            return _logoloom_available
        _logoloom_available = os.path.isfile(cls._LOCOLOOM_PATH)
        if not _logoloom_available:
            logger.warning("LogoLoom is not available (server.mjs not found at %s)", cls._LOCOLOOM_PATH)
        return _logoloom_available

    @classmethod
    def _ensure_server(cls):
        """Start LogoLoom MCP server if not already running."""
        global _server_process
        with _server_lock:
            if _server_process is not None and _server_process.poll() is None:
                return  # Already running
            if _server_process is not None:
                cls._kill_server()

            try:
                _server_process = subprocess.Popen(
                    ["node", cls._LOCOLOOM_PATH],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                # MCP initialization handshake
                init_resp = cls._send_raw({
                    "jsonrpc": "2.0",
                    "id": cls._next_id(),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "wp-site-manager", "version": "1.0.0"},
                    },
                })
                logger.info("LogoLoom initialized: %s", init_resp.get("result", {}).get("serverInfo", {}))
                # Send initialized notification (no response expected)
                cls._send_raw({
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                }, expect_response=False)
                logger.info("LogoLoom server ready (pid=%s)", _server_process.pid)
            except Exception as e:
                logger.error("Failed to start LogoLoom: %s", e)
                cls._kill_server()
                raise LogoLoomError(f"LogoLoom 启动失败: {e}")

    @classmethod
    def _kill_server(cls):
        """Kill the LogoLoom process."""
        global _server_process
        if _server_process:
            try:
                _server_process.stdin.close()
                _server_process.stdout.close()
                if _server_process.stderr:
                    _server_process.stderr.close()
                _server_process.kill()
                _server_process.wait(timeout=5)
            except Exception:
                pass
            _server_process = None

    @classmethod
    def shutdown(cls):
        """Clean shutdown of LogoLoom server."""
        cls._kill_server()

    # ------------------------------------------------------------------
    # JSON-RPC communication
    # ------------------------------------------------------------------

    @classmethod
    def _next_id(cls) -> int:
        global _request_id
        with _request_id_lock:
            _request_id += 1
            return _request_id

    @classmethod
    def _send_raw(cls, payload: dict, expect_response: bool = True) -> dict | None:
        """Send a raw JSON-RPC message and return the response.

        If expect_response is False, sends the message and returns None
        (for notifications that don't get a response).

        Must be called while _server_lock is held (via _ensure_server or
        a higher-level method that acquires it).
        """
        global _server_process
        msg = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            _server_process.stdin.write(msg)
            _server_process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            logger.error("LogoLoom write error: %s", e)
            cls._kill_server()
            raise LogoLoomError(f"LogoLoom 通信失败(写): {e}")

        if not expect_response:
            return None

        try:
            line = _server_process.stdout.readline()
            if not line:
                raise LogoLoomError("LogoLoom 已关闭(无响应)")
            return json.loads(line)
        except (BrokenPipeError, OSError) as e:
            logger.error("LogoLoom read error: %s", e)
            cls._kill_server()
            raise LogoLoomError(f"LogoLoom 通信失败(读): {e}")
        except json.JSONDecodeError as e:
            logger.error("LogoLoom invalid JSON response: %s", str(e)[:200])
            raise LogoLoomError("LogoLoom 返回了无效的JSON")

    @classmethod
    def _call_tool(cls, tool_name: str, arguments: dict, timeout: int = None) -> dict:
        """Call a LogoLoom MCP tool and return the result content."""
        timeout = timeout or cls.MCP_TIMEOUT
        with _server_lock:
            cls._ensure_server()
            resp = cls._send_raw({
                "jsonrpc": "2.0",
                "id": cls._next_id(),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            })
        if "error" in resp:
            err = resp["error"]
            raise LogoLoomError(f"LogoLoom {tool_name}: {err.get('message', str(err))}")
        result = resp.get("result", {})
        content = result.get("content", [])
        if content and len(content) > 0:
            return content[0]
        return {}

    # ------------------------------------------------------------------
    # Public tool methods
    # ------------------------------------------------------------------

    @classmethod
    def _extract_svg_from_result(cls, result: dict) -> str | None:
        """Extract SVG string from MCP tool result.

        MCP tools may return either raw SVG or JSON like {"svg": "<svg>...</svg>"}.
        """
        text = result.get("text", "")
        if not text:
            return None
        # Try parsing as JSON first (LogoLoom wraps results in JSON)
        try:
            data = json.loads(text)
            svg = data.get("svg", "")
            if svg and "<svg" in svg:
                return svg
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: raw SVG in text
        if "<svg" in text:
            return text
        return None

    @classmethod
    def text_to_path(cls, svg_content: str) -> str:
        """Convert SVG <text> elements to <path> for font-independent rendering.

        Returns the converted SVG string, or the original if conversion fails.
        """
        if not cls.is_available():
            logger.warning("LogoLoom unavailable — skipping text_to_path")
            return svg_content
        try:
            result = cls._call_tool("text_to_path", {"svg": svg_content})
            svg = cls._extract_svg_from_result(result)
            if svg:
                return svg
            logger.warning("text_to_path returned no valid SVG")
            return svg_content
        except LogoLoomError as e:
            logger.warning("text_to_path failed: %s — using original SVG", e)
            return svg_content

    @classmethod
    def optimize_svg(cls, svg_content: str) -> str:
        """Optimize and compress SVG via SVGO.

        Returns the optimized SVG string, or the original if optimization fails.
        """
        if not cls.is_available():
            logger.warning("LogoLoom unavailable — skipping optimize_svg")
            return svg_content
        try:
            result = cls._call_tool("optimize_svg", {"svg": svg_content})
            svg = cls._extract_svg_from_result(result)
            if svg:
                return svg
            logger.warning("optimize_svg returned no valid SVG")
            return svg_content
        except LogoLoomError as e:
            logger.warning("optimize_svg failed: %s — using original SVG", e)
            return svg_content

    @classmethod
    def export_brand_kit(cls, svg_content: str, output_dir: str, name: str = "brand") -> dict:
        """Export brand kit assets: PNG, ICO, WebP, OG image, BRAND.md.

        Writes files to output_dir and returns a dict of filenames keyed by type.

        Returns dict like:
            {"png_256": "logo-256.png", "png_512": "logo-512.png",
             "png_1024": "logo-1024.png", "ico": "favicon.ico",
             "webp": "logo.webp", "og_image": "og-image.png",
             "brand_md": "BRAND.md"}
        On failure, returns as many files as were generated.
        """
        result = {}
        if not cls.is_available():
            logger.warning("LogoLoom unavailable — cannot export brand kit")
            return result

        os.makedirs(output_dir, exist_ok=True)
        try:
            mcp_result = cls._call_tool("export_brand_kit", {
                "svg": svg_content,
                "outputDir": output_dir,
                "name": name,
            }, timeout=cls.EXPORT_TIMEOUT)

            # Parse MCP result for file references
            text = mcp_result.get("text", "")
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = {"message": text}

                # Map filenames: LogoLoom may use various naming conventions,
                # so we detect by listing the output directory
                for fname in os.listdir(output_dir):
                    fname_lower = fname.lower()
                    if "256" in fname_lower and fname.endswith(".png"):
                        result["png_256"] = fname
                    elif "512" in fname_lower and fname.endswith(".png"):
                        result["png_512"] = fname
                    elif "1024" in fname_lower and fname.endswith(".png"):
                        result["png_1024"] = fname
                    elif fname.endswith(".ico"):
                        result["ico"] = fname
                    elif fname.endswith(".webp"):
                        result["webp"] = fname
                    elif fname_lower.startswith("og-") and fname.endswith(".png"):
                        result["og_image"] = fname
                    elif fname.upper() == "BRAND.MD":
                        result["brand_md"] = fname
        except LogoLoomError as e:
            logger.warning("export_brand_kit failed: %s — listing existing files", e)
            # Try to list whatever files were generated before the failure
            if os.path.isdir(output_dir):
                for fname in os.listdir(output_dir):
                    result[fname] = fname

        # If no structured result but files exist, auto-detect
        if not result and os.path.isdir(output_dir):
            for fname in sorted(os.listdir(output_dir)):
                result[fname] = fname

        return result

    @classmethod
    def image_to_svg(cls, image_path: str) -> str | None:
        """Convert a raster image (PNG/JPG) to SVG vector via vtracer.

        Returns SVG string or None on failure.
        """
        if not cls.is_available() or not os.path.isfile(image_path):
            return None
        try:
            result = cls._call_tool("image_to_svg", {"imagePath": image_path})
            text = result.get("text", "")
            if text and "<svg" in text:
                return text
            logger.warning("image_to_svg returned no valid SVG")
            return None
        except LogoLoomError as e:
            logger.warning("image_to_svg failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # AI Logo Generation (DeepSeek)
    # ------------------------------------------------------------------

    @classmethod
    def generate_svg_logo(cls, brand_name: str, description: str = "",
                          industry: str = "", deepseek_api_key: str = "",
                          deepseek_base_url: str = "https://api.deepseek.com") -> dict:
        """Generate an SVG logo via DeepSeek AI.

        Args:
            brand_name: Brand name for the logo
            description: Brand description
            industry: Industry category
            deepseek_api_key: DeepSeek API key

        Returns:
            {"svg": "<svg>...</svg>", "colors": ["#hex",...],
             "typography": {"heading": "Font", "body": "Font"}}
        """
        import requests as http_requests

        prompt = (
            f'你是一个专业的品牌设计师。请为以下品牌设计一个极简现代的 SVG logo：\n'
            f'- 品牌名称：{brand_name}\n'
            f'- 品牌描述：{description or "无"}\n'
            f'- 行业：{industry or "通用"}\n\n'
            f'要求：\n'
            f'1. SVG viewBox="0 0 512 512"，纯矢量，透明背景（严禁添加背景矩形或背景色）\n'
            f'2. 极简现代风格，适合电商品牌\n'
            f'3. 品牌名称文字必须使用 <path> 路径绘制，严禁使用 <text> 标签\n'
            f'4. 2-3个品牌主色调\n'
            f'5. 【重要】图形和文字必须填满viewBox的70%-85%，四周留白控制在10%-15%以内，严禁大面积空白\n\n'
            f'请严格返回以下 JSON 格式（不要markdown代码块）：\n'
            f'{{"svg": "完整的SVG代码", "colors": ["#主色1", "#主色2", "#主色3"], '
            f'"typography": {{"heading": "推荐的标题字体名称", "body": "推荐的正文字体名称"}}}}'
        )

        try:
            resp = http_requests.post(
                f"{deepseek_base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "你是一个专业品牌设计师，只会返回严格的JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.8,
                    "max_tokens": 4096,
                },
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            content_text = body["choices"][0]["message"]["content"]

            # Strip markdown fence
            content_text = re.sub(r'^```(?:json)?\s*', '', content_text.strip())
            content_text = re.sub(r'\s*```$', '', content_text)

            # Try to extract JSON — gracefully handle malformed JSON from AI
            try:
                json_match = re.search(r'\{[\s\S]*"svg"[\s\S]*\}', content_text)
                if json_match:
                    data = json.loads(json_match.group(0))
                else:
                    data = json.loads(content_text)
                svg = data.get("svg", "")
                colors = data.get("colors", [])
                typography = data.get("typography", {})
            except json.JSONDecodeError:
                # AI returned broken JSON (common with embedded SVG quotes)
                # Fallback: extract fields individually with regex
                logger.warning("JSON parse failed, falling back to regex extraction")
                svg = ""
                # Extract SVG: everything between "svg": and the next top-level key
                svg_match = re.search(r'"svg"\s*:\s*"', content_text)
                if svg_match:
                    start = svg_match.end()
                    # Find the closing quote — look for ","colors" or ","typography" or "} after "
                    tail = re.search(r'",\s*"(?:colors|typography)"', content_text[start:])
                    if tail:
                        svg = content_text[start:start + tail.start()]
                    else:
                        # Last field, look for "} at end
                        tail = re.search(r'"\s*\}', content_text[start:])
                        if tail:
                            svg = content_text[start:start + tail.start()]
                # Unescape JSON-escaped SVG content
                svg = svg.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')

                # Extract colors array
                colors = []
                colors_match = re.search(r'"colors"\s*:\s*(\[[^\]]*\])', content_text)
                if colors_match:
                    try:
                        colors = json.loads(colors_match.group(1))
                    except json.JSONDecodeError:
                        pass

                # Extract typography object
                typography = {}
                typo_match = re.search(r'"typography"\s*:\s*(\{[^}]+\})', content_text)
                if typo_match:
                    try:
                        typography = json.loads(typo_match.group(1))
                    except json.JSONDecodeError:
                        pass

            if not svg or "<svg" not in svg:
                raise ValueError("AI 未返回有效的 SVG")

            return {
                "svg": svg,
                "colors": colors,
                "typography": typography,
            }
        except Exception as e:
            logger.error("DeepSeek logo generation failed: %s", e)
            raise LogoLoomError(f"AI Logo 生成失败: {e}")


# Auto-shutdown on process exit
import atexit
atexit.register(LogoLoomService.shutdown)
