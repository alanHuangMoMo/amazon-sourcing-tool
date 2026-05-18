"""
赛道探索器配置管理。
存储位置：sourcing-tool/config/explorer_config.json
未配置时使用默认值，运行时可通过 API 修改并即时生效。
"""
import json
import os as _os
import subprocess
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "explorer_config.json"

DEFAULTS = {
    # DeepSeek
    "deepseek_key": "sk-1fce6b2a9f7844d1938aa3ed512dbcde",
    "deepseek_model": "deepseek-v4-flash",
    "llm_timeout": 30,
    # Sorftime
    "sorftime_path": "C:/Users/alanh/AppData/Roaming/npm/sorftime",
    "sorftime_domain": 1,
    "sorftime_page_size": 200,
    # 管道参数
    "min_keywords": 10,
    "min_jaccard": 0.10,
    "max_depth": 2,
    "min_root_count": 2,
    "max_seeds": 5,
    "max_file_mb": 10,
}


def load() -> dict:
    """加载配置，合并默认值。"""
    if not CONFIG_FILE.exists():
        return dict(DEFAULTS)
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        saved = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return merged


def save(config: dict) -> bool:
    """保存配置（仅保存非默认值以缩减文件）。"""
    defaults = DEFAULTS
    # 只存与默认不同的
    diff = {k: v for k, v in config.items() if k in defaults and v != defaults[k]}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_FILE.write_text(json.dumps(diff, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def apply_to_module(config: dict):
    """将配置注入到 auto_explorer 模块的全局变量。"""
    import app.auto_explorer as mod
    mod.API_KEY = config.get("deepseek_key", DEFAULTS["deepseek_key"])
    mod.MODEL = config.get("deepseek_model", DEFAULTS["deepseek_model"])
    mod.LLM_TIMEOUT = config.get("llm_timeout", DEFAULTS["llm_timeout"])
    mod.SORFTIME_PATH = config.get("sorftime_path", DEFAULTS["sorftime_path"])
    mod.SORFTIME_DEFAULT_DOMAIN = config.get("sorftime_domain", DEFAULTS["sorftime_domain"])
    mod.SORFTIME_PAGE_SIZE = config.get("sorftime_page_size", DEFAULTS["sorftime_page_size"])
    mod.MIN_KEYWORDS_FOR_DECOMPOSE = config.get("min_keywords", DEFAULTS["min_keywords"])
    mod.MIN_JACCARD_SIM = config.get("min_jaccard", DEFAULTS["min_jaccard"])
    mod.MAX_DEPTH = config.get("max_depth", DEFAULTS["max_depth"])
    mod.MIN_ROOT_COUNT = config.get("min_root_count", DEFAULTS["min_root_count"])


def get_sorftime_status() -> dict:
    """获取 Sorftime CLI 状态（账户、剩余请求数）。"""
    path = load().get("sorftime_path", DEFAULTS["sorftime_path"])
    result = {"available": False, "account": "", "requests_left": 0, "error": ""}

    try:
        import subprocess
        r = subprocess.run(
            ["bash", path, "whoami"],
            capture_output=True, text=True, timeout=10
        )
        output = r.stdout + r.stderr
        result["raw"] = output.strip()

        # 尝试提取账户名（输出格式: "当前活跃账户: xxx"）
        for line in output.split("\n"):
            if "活跃" in line or "active" in line.lower() or "account" in line.lower():
                result["account"] = line.strip()
                result["available"] = True
                break

        # 无明确活跃账户标记但命令成功 → 可用
        if r.returncode == 0 and not result["available"]:
            result["available"] = True
            result["account"] = output.strip()[:100]

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result["error"] = f"Sorftime CLI 不可用: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def get_llm_status() -> dict:
    """获取 LLM API 连接状态。"""
    config = load()
    return {
        "model": config.get("deepseek_model", DEFAULTS["deepseek_model"]),
        "key_configured": bool(config.get("deepseek_key")),
        "key_masked": (
            config["deepseek_key"][:8] + "..." + config["deepseek_key"][-4:]
            if config.get("deepseek_key") and len(config.get("deepseek_key", "")) > 12
            else "****"
        ),
    }
