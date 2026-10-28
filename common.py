"""兼容垫片：转导出 qqbot_common，新代码直接用 qqbot_common（HANDOVER 第 2 节）。"""
from qqbot_common import *  # noqa: F401,F403
from qqbot_common import (  # noqa: F401
    BASE_DIR, QUEUE_DIR, PENDING_DIR, PROCESSING_DIR, DONE_DIR, FAILED_DIR,
    MEDIA_DIR, API_BASE, TOKEN_URL, get_env, app_id, app_secret, bot_mode,
    config_present, get_proxy, proxy_configured, make_aiohttp_session,
    get_access_token, auth_headers, ensure_queue_dirs, enqueue,
    load_queue_file, archive_queue_file, passive_window_expired,
    target_path, random_msg_seq, post_passive_message, send_text_passive,
    detect_image_format, validate_send_image, fetch_image_bytes, download_media,
)
