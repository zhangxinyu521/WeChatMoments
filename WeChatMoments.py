import os
import json
import requests
from common.log import logger
import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from plugins import *
from playwright.async_api import async_playwright
from typing import List, Dict, Any
from io import BytesIO
import asyncio
import os.path

@plugins.register(
    name="WeChatMoments",
    desc="生成朋友圈文案，支持文字版和图片版",
    version="1.0",
    author="Your Name",
    desire_priority=500
)
class WeChatMoments(Plugin):
    # 配置常量
    CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
    TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "moments_template.html")
    API_ENDPOINT = "https://apis.tianapi.com/pyqwenan/index"
    
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        # 确保模板目录存在
        os.makedirs(os.path.dirname(self.TEMPLATE_PATH), exist_ok=True)
        logger.info(f"[{__class__.__name__}] initialized")
        self.browser = None
        self.playwright = None
        # 创建事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def on_handle_context(self, e_context):
        """处理用户输入的上下文"""
        if e_context['context'].type != ContextType.TEXT:
            return
        
        content = e_context["context"].content.strip()
        # 检查是否是文案生成命令
        if content.startswith(("文案", "图片文案")):
            logger.info(f"[{__class__.__name__}] 收到消息: {content}")
            # 在事件循环中运行异步任务
            self.loop.run_until_complete(self._process_request(content, e_context))

    async def _process_request(self, command: str, e_context):
        """异步处理不同类型的文案请求"""
        try:
            # 获取API密钥
            api_key = self._get_api_key()
            if not api_key:
                self._send_error_reply(e_context, f"请先配置{self.CONFIG_PATH}文件中的API密钥")
                return

            # 获取文案内容
            content_data = self._fetch_content(api_key)
            if not content_data:
                self._send_error_reply(e_context, "获取文案失败，请稍后重试")
                return

            # 根据命令类型处理响应
            if command == "文案":
                self._handle_text_content(content_data, e_context)
            elif command == "图片文案":
                await self._handle_image_content(content_data, e_context)
        except Exception as e:
            logger.error(f"处理请求失败: {e}")
            self._send_error_reply(e_context, "处理请求失败，请稍后重试")

    def __del__(self):
        """析构函数"""
        try:
            if self.browser or self.playwright:
                self.loop.run_until_complete(self._cleanup_playwright())
            self.loop.close()
        except Exception as e:
            logger.error(f"清理资源失败: {e}")

    async def _cleanup_playwright(self):
        """异步清理资源"""
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.error(f"Error cleaning up Playwright resources: {e}")
        finally:
            self.browser = None
            self.playwright = None

    async def _init_playwright(self):
        """异步初始化 Playwright"""
        if self.browser is None:
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    args=['--no-sandbox', '--disable-setuid-sandbox']
                )
                logger.info("Playwright initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Playwright: {e}")
                self.playwright = None
                self.browser = None

    def _get_api_key(self) -> str:
        """获取API密钥"""
        try:
            if not os.path.exists(self.CONFIG_PATH):
                logger.error(f"配置文件不存在: {self.CONFIG_PATH}")
                return ""
            
            with open(self.CONFIG_PATH, 'r') as file:
                return json.load(file).get('TIAN_API_KEY', '')
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return ""

    def _fetch_content(self, api_key: str) -> Dict[str, Any]:
        """获取文案内容"""
        try:
            url = f"{self.API_ENDPOINT}?key={api_key}"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == 200 and 'result' in data:
                return data['result']
            logger.error(f"API返回格式不正确: {data}")
            return {}
        except Exception as e:
            logger.error(f"获取文案内容失败: {e}")
            return {}

    def _handle_text_content(self, content_data: Dict[str, Any], e_context):
        """处理文字版文案"""
        content = content_data.get('content', '')
        source = content_data.get('source', '佚名')
        
        reply_text = f"📝 今日文案：\n\n{content}\n\n— {source}"
        e_context["reply"] = Reply(ReplyType.TEXT, reply_text)
        e_context.action = EventAction.BREAK_PASS

    def _send_error_reply(self, e_context, message: str):
        """发送错误消息"""
        e_context["reply"] = Reply(ReplyType.TEXT, message)
        e_context.action = EventAction.BREAK_PASS 

    async def _handle_image_content(self, content_data: Dict[str, Any], e_context):
        """异步处理图片版文案"""
        html_content = self._generate_html(content_data)
        await self._render_and_send_image(html_content, e_context)

    async def _render_and_send_image(self, html_content: str, e_context):
        """异步渲染HTML并发送图片"""
        if not self.browser:
            await self._init_playwright()
            if not self.browser:
                self._send_error_reply(e_context, "浏览器初始化失败，请稍后重试")
                return

        try:
            page = await self.browser.new_page()
            try:
                await page.set_viewport_size({"width": 600, "height": 800})
                await page.set_content(html_content, timeout=60000)
                screenshot_bytes = await page.screenshot(
                    full_page=True,
                    type='png'
                )
                
                if screenshot_bytes:
                    image_io = BytesIO(screenshot_bytes)
                    e_context["reply"] = Reply(ReplyType.IMAGE, image_io)
                    e_context.action = EventAction.BREAK_PASS
                    logger.debug("[WeChatMoments] 图片生成并发送成功")
                else:
                    self._send_error_reply(e_context, "生成图片失败")
            finally:
                await page.close()
        except Exception as e:
            logger.error(f"渲染图片失败: {e}")
            self._send_error_reply(e_context, "生成图片失败，请稍后重试")
            # 如果发生错误，尝试重新初始化浏览器
            await self._cleanup_playwright()
            await self._init_playwright()

    def _generate_html(self, content_data: Dict[str, Any]) -> str:
        """生成HTML内容"""
        try:
            # 读取HTML模板
            with open(self.TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                template = f.read()
            
            content = content_data.get('content', '')
            source = content_data.get('source', '佚名')
            
            # 替换模板中的占位符
            html_content = template.replace('{{content}}', content)
            html_content = html_content.replace('{{source}}', source)
            
            return html_content
            
        except Exception as e:
            logger.error(f"生成HTML内容失败: {e}")
            raise
        
    def get_help_text(self, **kwargs):
        """获取插件帮助信息"""
        help_text = """朋友圈文案生成助手
        指令：
        1. 发送"文案"：获取文字版朋友圈文案
        2. 发送"图片文案"：获取图片版朋友圈文案
        """
        return help_text 