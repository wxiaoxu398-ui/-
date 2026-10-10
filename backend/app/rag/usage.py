"""Observed token counts plus a dated, conservative official-price estimate."""
from urllib.parse import urlparse

PRICE_DATE = '2026-10-11'
PRICE_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
# CNY per million tokens at peak rates. Off-peak billing may be lower.
PEAK_RATES = {'deepseek-flash': (0.04, 2.0, 8.0), 'deepseek-v4-pro': (0.30, 9.0, 27.0)}


def record_usage(usage, model, base_url, metrics):
    values = usage.model_dump() if hasattr(usage, 'model_dump') else vars(usage) if not isinstance(usage, dict) else usage
    observed = {key: values[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens')
                if isinstance(values.get(key), int) and values[key] >= 0}
    metrics.update(model=model, usage=observed)
    if urlparse(base_url).hostname != 'api.deepseek.com' or model not in PEAK_RATES:
        return
    if 'prompt_tokens' not in observed or 'completion_tokens' not in observed:
        return
    # If cache statistics are absent, assume all input tokens miss the cache.
    hit = min(observed.get('prompt_cache_hit_tokens', 0), observed['prompt_tokens'])
    miss = observed['prompt_tokens'] - hit
    rate_hit, rate_miss, rate_output = PEAK_RATES[model]
    metrics.update(estimated_cost_cny_peak=round((hit * rate_hit + miss * rate_miss + observed['completion_tokens'] * rate_output) / 1_000_000, 8),
                   price_snapshot_date=PRICE_DATE, price_source=PRICE_URL,
                   cost_note='按已返回 token 用量与价格快照的高峰单价估算；实际扣费以 DeepSeek 账单为准。')
