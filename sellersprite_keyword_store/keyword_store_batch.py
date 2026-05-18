"""
卖家精灵关键词词库 → ASIN 拓展
通过关键词词库批量导出关键词数据（搜索量/购买量/PPC竞价等 35 列），
导出 Excel 用于后续 ASIN 拓展分析。

全流程：登录 → 建词库 → 加关键词 → 导出 → 轮询下载 → 删除词库
每批最多 2000 个关键词

用法:
  python keyword_store_batch.py keywords.txt
  python keyword_store_batch.py keywords.txt --market CA --prefix "红光治疗"
  python keyword_store_batch.py keywords.txt --keep-libraries
"""

import argparse
import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

# ---- 常量 ----
USER_DATA = Path.home() / ".sellersprite-keywordstore-profile"
KEYWORD_STORE_URL = "https://www.sellersprite.com/v3/keyword-store"
EXPORT_LOG_URL = "https://www.sellersprite.com/v2/export-log"
BATCH_MAX = 2000

MARKET_MAP = {
    "US": "美国站", "CA": "加拿大", "JP": "日本站", "UK": "英国站",
    "DE": "德国站", "FR": "法国站", "IT": "意大利", "ES": "西班牙", "IN": "印度站",
}

LOGIN_URL_RE = re.compile(r'/(login|passport)')
CREDENTIALS_FILE = Path.home() / ".sellersprite-credentials"


def load_credentials():
    try:
        data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        return data["email"], data["password"]
    except Exception as e:
        print(f"无法读取凭据文件 {CREDENTIALS_FILE}: {e}")
        print('文件格式: {"email": "xxx", "password": "xxx"}')
        sys.exit(1)


async def human_delay(min_s=0.3, max_s=1.2):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def is_on_login_page(page):
    return bool(LOGIN_URL_RE.search(page.url))


# ============================================================
#  登录
# ============================================================

async def auto_login(page):
    """浏览器 UI 登录"""
    email, password = load_credentials()
    print("自动登录中...")
    await human_delay(1.0, 2.0)

    # 等登录表单出现
    try:
        await page.wait_for_selector('input[name="email"], input[type="password"]', timeout=10000)
    except Exception:
        pass
    await human_delay(0.5, 1.0)

    # 填邮箱
    try:
        await page.locator('input[name="email"]:visible').fill(email)
    except Exception:
        try:
            await page.locator('input[placeholder*="手机号"]:visible, input[placeholder*="邮箱"]:visible').fill(email)
        except Exception:
            pass

    # 填密码
    try:
        await page.locator('input[type="password"]:visible').fill(password)
    except Exception:
        pass

    await human_delay(0.5, 1.0)

    # 点登录按钮
    try:
        await page.locator('button:visible').filter(has_text='登录').click(timeout=5000)
    except Exception:
        try:
            await page.locator('button[type="submit"]:visible').click(timeout=5000)
        except Exception:
            pass

    # 检测账密错误
    await asyncio.sleep(2)
    from sellersprite_utils.credential_check import detect_login_error
    err = await detect_login_error(page)
    if err:
        print(f"\n!!! 账密验证失败: {err}")
        print("停止执行，请更新账密后重试")
        sys.exit(1)

    print("等待登录完成", end="", flush=True)
    for _ in range(100):
        await asyncio.sleep(1)
        if not await is_on_login_page(page):
            print(" 成功")
            return True
        print(".", end="", flush=True)
    print(" 超时")
    return False


async def ensure_logged_in(page):
    await page.goto(KEYWORD_STORE_URL, timeout=30000)
    await page.wait_for_load_state("domcontentloaded")
    try:
        await page.wait_for_selector('button:has-text("新建词库")', timeout=10000)
    except Exception:
        pass
    await human_delay(1.0, 1.5)

    if await is_on_login_page(page):
        print("需要登录...")
        await page.goto("https://www.sellersprite.com/w/user/login", timeout=30000)
        await page.wait_for_load_state("domcontentloaded")
        await human_delay(1.0, 1.5)
        if not await auto_login(page):
            print("自动登录失败，退出")
            sys.exit(1)
        await page.goto(KEYWORD_STORE_URL, timeout=30000)
        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_selector('button:has-text("新建词库")', timeout=10000)
        except Exception:
            pass
        await human_delay(1.0, 1.5)
    else:
        is_guest = await page.evaluate(
            """() => {
                const allText = document.body.innerText;
                return allText.includes('未登录') || allText.includes('游客');
            }"""
        )
        if is_guest:
            print("检测到未登录（游客状态），正在登录...")
            await page.goto("https://www.sellersprite.com/w/user/login", timeout=30000)
            await page.wait_for_load_state("domcontentloaded")
            await human_delay(1.0, 1.5)
            if not await auto_login(page):
                print("自动登录失败，退出")
                sys.exit(1)
            await page.goto(KEYWORD_STORE_URL, timeout=30000)
            await page.wait_for_load_state("domcontentloaded")
            try:
                await page.wait_for_selector('button:has-text("新建词库")', timeout=10000)
            except Exception:
                pass
            await human_delay(1.0, 1.5)
        else:
            print("检测到已登录")


# ============================================================
#  词库操作（UI）
# ============================================================

async def create_library(page, name: str, market_cn: str) -> int | None:
    """UI: 新建关键词词库，返回 library ID"""
    print(f"  新建词库: {name}")

    # 等页面稳定 + 按钮可点击
    try:
        await page.wait_for_selector('button:has-text("新建词库")', timeout=15000)
    except Exception:
        pass
    await asyncio.sleep(1.0)

    # Playwright 原生点击（保证可见+可交互）
    try:
        await page.get_by_role("button", name="新建词库").click()
    except Exception:
        try:
            await page.locator('button', has_text="新建词库").first.click()
        except Exception as e:
            print(f"  找不到「新建词库」按钮: {e}")
            return None
    await asyncio.sleep(1.5)

    # 等对话框出现
    for i in range(15):
        visible = await page.evaluate(
            """() => {
                const dialogs = document.querySelectorAll('[role="dialog"]');
                for (const d of dialogs) {
                    if (d.offsetWidth > 0 && d.innerText.includes('新建关键词词库')) return true;
                }
                return false;
            }"""
        )
        if visible:
            break
        await asyncio.sleep(0.5)
    else:
        print("  新建词库对话框未出现")
        return None

    # 选站点：点开下拉
    try:
        await page.locator('[role="dialog"] input[readonly][placeholder="请选择"]').first.click()
    except Exception:
        pass
    await asyncio.sleep(0.8)

    # 选目标站点
    try:
        await page.locator('.el-select-dropdown__item').filter(has_text=market_cn).first.click()
    except Exception:
        pass
    await asyncio.sleep(0.5)

    # 填词库名称
    try:
        name_input = page.locator('[role="dialog"] input[placeholder*="词库名称"]')
        await name_input.fill(name)
    except Exception:
        pass
    await asyncio.sleep(0.3)

    # 记录创建前的最大 ID
    old_max_id = await page.evaluate(
        """() => {
            const links = document.querySelectorAll('a[href*="/keyword-store/"]');
            let maxId = 0;
            for (const link of links) {
                const match = link.href.match(/keyword-store\\/(\\d+)/);
                if (match) { const id = parseInt(match[1]); if (id > maxId) maxId = id; }
            }
            return maxId;
        }"""
    )

    # 监听 API 响应来获取新建词库的 ID
    lib_id_future = asyncio.get_event_loop().create_future()

    async def on_response(response):
        if not lib_id_future.done() and "/api/" in response.url and response.status == 200:
            try:
                body = await response.json()
                if isinstance(body, dict) and body.get("success") and isinstance(body.get("data"), (int, float)):
                    url_path = response.url.split("?")[0]
                    if "store" in url_path or "keyword" in url_path.lower():
                        lib_id_future.set_result(int(body["data"]))
            except Exception:
                pass

    page.on("response", on_response)

    # 点确定（evaluate 方式，绕过 Playwright 定位）
    await page.evaluate(
        """() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {
                if (d.offsetWidth > 0 && d.innerText.includes('新建关键词词库')) {
                    const btns = d.querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.offsetWidth > 0 && btn.innerText.trim() === '确定') {
                            btn.click(); return;
                        }
                    }
                }
            }
        }"""
    )

    # 等 API 返回或超时
    try:
        lib_id = await asyncio.wait_for(lib_id_future, timeout=15)
        print(f"  词库创建成功 (ID={lib_id})")
    except asyncio.TimeoutError:
        lib_id = None
        await asyncio.sleep(2.0)
        lib_id = await page.evaluate(
            """(old) => {
                const links = document.querySelectorAll('a[href*="/keyword-store/"]');
                let maxId = 0;
                for (const link of links) {
                    const match = link.href.match(/keyword-store\\/(\\d+)/);
                    if (match) { const id = parseInt(match[1]); if (id > maxId) maxId = id; }
                }
                return maxId > old ? maxId : null;
            }""",
            old_max_id,
        )
        if lib_id:
            print(f"  词库创建成功 (ID={lib_id}, 页面提取)")
        else:
            print(f"  未检测到新词库 ID（旧最大={old_max_id}）")
    finally:
        page.remove_listener("response", on_response)

    return lib_id


async def add_keywords(page, lib_id: int, keywords: list[str], market: str, cid: int) -> bool:
    """UI 方式添加关键词，靠对话框消失判断成功"""
    print(f"  添加 {len(keywords)} 个关键词...", end="", flush=True)

    detail_url = f"{KEYWORD_STORE_URL}/{lib_id}?market={market}&cid={cid}"
    await page.goto(detail_url, timeout=30000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(5.0)

    # 等"新增关键词"按钮出现，用 JS 直接点（绕过遮挡检测）
    try:
        await page.wait_for_selector('button:visible:has-text("新增关键词")', timeout=15000)
    except Exception:
        print(" 按钮未出现")
        return False
    await asyncio.sleep(0.5)
    try:
        await page.evaluate(
            """() => {
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    if (btn.offsetWidth > 0 && btn.innerText.includes('新增关键词')) {
                        btn.click(); return;
                    }
                }
            }"""
        )
    except Exception as e:
        print(f" 点击失败: {str(e)[:80]}")
        return False
    await asyncio.sleep(1.0)

    # 等对话框出现
    for _ in range(20):
        ready = await page.evaluate(
            """() => {
                const dialogs = document.querySelectorAll('[role="dialog"]');
                for (const d of dialogs) {
                    if (d.offsetWidth > 0 && d.innerText.includes('新增关键词')) return true;
                }
                return false;
            }"""
        )
        if ready:
            break
        await asyncio.sleep(0.5)

    # 填入关键词（用 evaluate 直接设值，绕过 Playwright fill 的字符限制）
    keyword_text = "\n".join(keywords)
    await page.evaluate(
        f"""(kw) => {{
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {{
                if (d.offsetWidth > 0 && d.innerText.includes('新增关键词')) {{
                    const ta = d.querySelector('textarea');
                    if (ta) {{ ta.value = kw; ta.dispatchEvent(new Event('input', {{bubbles: true}})); }}
                }}
            }}
        }}""",
        keyword_text,
    )
    await asyncio.sleep(0.5)

    # 点提交（evaluate 方式）
    await page.evaluate(
        """() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {
                if (d.offsetWidth > 0) {
                    const btns = d.querySelectorAll('button');
                    for (const btn of btns) {
                        if (btn.offsetWidth > 0 && btn.innerText.includes('提交')) {
                            btn.click(); return;
                        }
                    }
                }
            }
        }"""
    )

    # 等对话框消失（最多 90 秒）
    for _ in range(90):
        await asyncio.sleep(1)
        gone = await page.evaluate(
            """() => {
                const dialogs = document.querySelectorAll('[role="dialog"]');
                let visible = 0;
                for (const d of dialogs) {
                    if (d.offsetWidth > 0) visible++;
                }
                return visible === 0;
            }"""
        )
        if gone:
            print(" 完成")
            return True

    print(" 超时")
    return False


async def trigger_export(page) -> bool:
    """UI: 点导出按钮 → 弹出提示对话框 → 点「前往查看」跳转到导出记录页"""
    print("  触发导出...", end="", flush=True)

    # 找到并点击"导出"按钮（不是"导出明细"）
    try:
        await page.locator('button', has_text="导出").first.click()
    except Exception:
        print(" 找不到导出按钮")
        return False
    await asyncio.sleep(2.0)

    # 检查导出对话框
    dialog_text = await page.evaluate(
        """() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {
                if (d.offsetWidth > 0 && d.innerText.includes('数据导出中')) {
                    return d.innerText;
                }
            }
            return null;
        }"""
    )
    if not dialog_text:
        print(" 导出对话框未出现")
        return False

    print(" 已提交")

    # 点"前往查看"
    try:
        await page.locator('button', has_text="前往查看").click()
    except Exception:
        pass
    await asyncio.sleep(2.0)
    return True


async def poll_and_download(page, output_dir: Path, label: str) -> Path | None:
    """在导出记录页轮询，等完成后下载文件"""
    # 导出记录页可能在新标签页中
    export_page = None
    for _ in range(10):
        for p in page.context.pages:
            if "export-log" in p.url:
                export_page = p
                break
        if export_page:
            break
        await asyncio.sleep(1)

    if not export_page:
        print("  未找到导出记录页标签，直接导航")
        export_page = page
        await export_page.goto(EXPORT_LOG_URL, timeout=30000)
        await export_page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2.0)

    # 找最新的导出任务名
    task_name = await export_page.evaluate(
        """() => {
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {
                const text = row.innerText;
                if (text.includes('KeywordList-') && text.includes('关键词词库')) {
                    const m = text.match(/KeywordList-[A-Z]+-\\d+-\\d+/);
                    if (m) return m[0];
                }
            }
            return null;
        }"""
    )
    if not task_name:
        print("  找不到导出任务")
        return None
    print(f"  导出任务: {task_name}")

    # 轮询等完成
    print("  等待导出完成", end="", flush=True)
    completed = False
    for _ in range(60):
        await asyncio.sleep(10)
        await export_page.reload(timeout=30000)
        await export_page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(0.5)

        status = await export_page.evaluate(
            """(name) => {
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {
                    if (row.innerText.includes(name)) {
                        if (row.innerText.includes('已完成')) return 'done';
                        if (row.innerText.includes('导出中')) return 'exporting';
                    }
                }
                return 'missing';
            }""",
            task_name,
        )
        if status == "done":
            completed = True
            print(" 完成")
            break
        if status == "missing":
            print(" 任务丢失")
            return None
        print(".", end="", flush=True)

    if not completed:
        print(" 超时")
        return None

    # 下载
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"KeywordList-{label}.xlsx"

    try:
        download_future = asyncio.get_event_loop().create_future()

        async def on_download(download):
            await download.save_as(str(output_path))
            download_future.set_result(output_path)

        export_page.on("download", on_download)

        await export_page.evaluate(
            """(name) => {
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {
                    if (row.innerText.includes(name)) {
                        const links = row.querySelectorAll('a[href*=".xlsx"]');
                        for (const link of links) {
                            if (link.offsetWidth > 0) { link.click(); return; }
                        }
                    }
                }
            }""",
            task_name,
        )
        path = await asyncio.wait_for(download_future, timeout=60)
        print(f"  文件已保存: {path}")
        return path
    except asyncio.TimeoutError:
        print("  下载超时")
        return None
    except Exception as e:
        print(f"  下载失败: {e}")
        return None
    finally:
        try:
            export_page.remove_listener("download", on_download)
        except Exception:
            pass


async def delete_library(page, lib_id: int, market: str, cid: int) -> bool:
    """UI: 在词库列表页勾选并批量删除"""
    await page.goto(KEYWORD_STORE_URL, timeout=30000)
    await page.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(2.0)

    lib_url = f"/keyword-store/{lib_id}"

    # 确保目标词库可见（切换站点筛选）
    visible = await page.evaluate(
        """(url) => {
            const links = document.querySelectorAll('a[href*="/keyword-store/"]');
            for (const link of links) {
                if (link.href.includes(url)) return true;
            }
            return false;
        }""",
        lib_url,
    )
    if not visible:
        print(f"  词库 ID={lib_id} 不可见，切换站点筛选...")
        market_cn = MARKET_MAP.get(market, "美国站")
        try:
            await page.locator('input[readonly][placeholder="请选择"]').first.click()
        except Exception:
            pass
        await asyncio.sleep(0.8)
        try:
            await page.locator('.el-select-dropdown__item').filter(has_text=market_cn).first.click()
        except Exception:
            pass
        await asyncio.sleep(1.5)

    # 勾选目标词库
    checked = await page.evaluate(
        """(url) => {
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {
                const links = row.querySelectorAll('a[href*="/keyword-store/"]');
                for (const link of links) {
                    if (link.href.includes(url)) {
                        const checkbox = row.querySelector('input[type="checkbox"]');
                        if (checkbox && !checkbox.checked) { checkbox.click(); return true; }
                    }
                }
            }
            return false;
        }""",
        lib_url,
    )
    if not checked:
        print(f"  找不到词库 ID={lib_id} 的复选框")
        return False
    await asyncio.sleep(0.5)

    # 点批量删除
    try:
        await page.locator('button', has_text="批量删除").click()
    except Exception:
        return False
    await asyncio.sleep(1.5)

    # 等词库从列表消失
    for _ in range(20):
        still_there = await page.evaluate(
            """(url) => {
                const links = document.querySelectorAll('a[href*="/keyword-store/"]');
                for (const link of links) {
                    if (link.href.includes(url)) return true;
                }
                return false;
            }""",
            lib_url,
        )
        if not still_there:
            print(f"  词库已删除 (ID={lib_id})")
            return True
        await asyncio.sleep(0.5)

    print(f"  删除确认超时")
    return False


# ============================================================
#  主流程
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="卖家精灵关键词词库批量导出")
    parser.add_argument("keywords_file", help="关键词文件，每行一个")
    parser.add_argument("--market", default="US", choices=list(MARKET_MAP.keys()))
    parser.add_argument("--prefix", default="AUTO", help="词库名前缀")
    parser.add_argument("--output", default=".", help="导出目录")
    parser.add_argument("--keep-libraries", action="store_true", help="保留词库，不删除")
    args = parser.parse_args()

    kw_path = Path(args.keywords_file)
    if not kw_path.exists():
        print(f"文件不存在: {kw_path}")
        sys.exit(1)

    keywords = [l.strip() for l in kw_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    keywords = list(dict.fromkeys(keywords))
    if not keywords:
        print("关键词文件为空")
        sys.exit(1)
    if len(keywords) > BATCH_MAX:
        print(f"警告: {len(keywords)} 个关键词超过上限 {BATCH_MAX}，只处理前 {BATCH_MAX} 个")
        keywords = keywords[:BATCH_MAX]

    print(f"共 {len(keywords)} 个关键词，市场: {args.market}")

    output_dir = Path(args.output).resolve()
    market = args.market
    market_cn = MARKET_MAP[market]
    now_str = time.strftime("%Y%m%d-%H%M%S")
    label = f"{args.prefix}-{market}-{now_str}"

    USER_DATA.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(USER_DATA),
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            print("浏览器 profile 损坏，重建中...")
            import shutil
            shutil.rmtree(str(USER_DATA), ignore_errors=True)
            USER_DATA.mkdir(parents=True, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(USER_DATA),
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )

        page = await context.new_page()
        await ensure_logged_in(page)

        # 提取 CID
        cid = await page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="cid="]');
                for (const link of links) {
                    const match = link.href.match(/cid=(\\d+)/);
                    if (match) return parseInt(match[1]);
                }
                return 1456040;
            }"""
        )
        print(f"CID={cid}")

        # 1) 新建词库
        lib_id = await create_library(page, label, market_cn)
        if not lib_id:
            print("新建词库失败，退出")
            sys.exit(1)

        # 2) 加关键词
        if not await add_keywords(page, lib_id, keywords, market, cid):
            print("添加关键词失败")
            if not args.keep_libraries:
                await delete_library(page, lib_id, market, cid)
            sys.exit(1)

        # 3) 触发导出 → 点前往查看
        if not await trigger_export(page):
            print("触发导出失败")
            if not args.keep_libraries:
                await delete_library(page, lib_id, market, cid)
            sys.exit(1)

        # 4) 轮询 + 下载
        path = await poll_and_download(page, output_dir, label)
        if not path:
            print("导出/下载失败")
            if not args.keep_libraries:
                await delete_library(page, lib_id, market, cid)
            sys.exit(1)

        # 5) 删词库
        if not args.keep_libraries:
            await delete_library(page, lib_id, market, cid)
        else:
            print(f"保留词库 (ID={lib_id})")

        await context.close()

    print(f"\n{'='*50}")
    print(f"完成。导出文件: {path}")


if __name__ == "__main__":
    asyncio.run(main())
