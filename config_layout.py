"""One-time migration of flat AstrBot plugin settings to grouped editor sections.

Group names are only a UI concern. Runtime modules continue to use flat keys.
"""
from __future__ import annotations

from astrbot.api import logger

GROUPS = {
    "prompt_settings": ("v3_extraction_prompt", "v3_profile_prompt"),
    "analysis_settings": (
        "dream_timezone", "dream_hour", "min_messages_to_dream",
        "low_activity_max_wait_days", "deferred_auto_retry",
        "max_messages_per_run", "max_input_tokens_per_run",
        "max_calls_per_group_day", "max_tokens_per_group_day",
        "estimated_output_tokens_per_run",
    ),
    "retrieval_settings": (
        "recall_normal_chars", "recall_history_chars", "recall_deep_chars",
        "recall_max_memories",
    ),
    "media_settings": (
        "media_enabled", "allowed_media_roots", "media_api_url",
        "media_vision_model", "media_transcription_model", "media_daily_calls",
        "media_daily_tokens",
    ),
    "embedding_settings": (
        "embedding_enabled", "embedding_api_url", "embedding_model",
        "embedding_daily_calls", "embedding_daily_tokens",
    ),
    "advanced_settings": (
        "platform_id", "scan_seconds", "max_parallel_groups",
        "max_message_chars_per_run", "max_existing_per_member",
        "retry_delay_seconds", "hook_inbox_db", "enable_private_profile_commands",
        "max_changes_per_run", "analysis_batch_soft_limit", "dreaming_v2_enabled",
    ),
    "pricing_settings": (
        "max_cost_per_group_day_usd", "input_price_usd_per_million",
        "output_price_usd_per_million",
    ),
}


def normalize(config):
    if int(config.get("__ui_schema_version", 0) or 0) < 1:
        # AstrBot leaves historic flat values in the saved config. Copy them
        # exactly once; after that, the grouped fields are authoritative.
        for group, fields in GROUPS.items():
            nested = config.get(group)
            nested = dict(nested) if isinstance(nested, dict) else {}
            for name in fields:
                if name in config:
                    nested[name] = config[name]
            config[group] = nested
        config["__ui_schema_version"] = 1
        save = getattr(config, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                logger.warning("[wechat_memory] grouped config save failed: %s", type(exc).__name__)
    flat = dict(config)
    for group in GROUPS:
        nested = config.get(group)
        if isinstance(nested, dict):
            flat.update(nested)
    return flat
