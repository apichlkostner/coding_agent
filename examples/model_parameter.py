import requests


def fmt(value: float) -> str:
    return f"{value:.10f}".rstrip("0").rstrip(".")


for model in [
    "https://api.deepinfra.com/models/deepseek-ai/DeepSeek-V4.1-Flash",
    "https://api.deepinfra.com/models/zai-org/GLM-5.3-Flash",
    "https://api.deepinfra.com/models/anthropic/claude-sonnet-5-5",
]:
    print(f"Model = {model}")
    info = requests.get(url=model, timeout=10)
    info.raise_for_status()

    info_json = info.json()
    print(
        info_json.get("pricing"),
        info_json.get("max_tokens"),
        info_json.get("max_output_tokens"),
    )

    discount = info_json["pricing"]["discount"] or 0.0
    input_price = (
        info_json["pricing"]["cents_per_input_token"]
        * 1_000_000
        / 100
        * (1.0 - discount)
    )
    output_price = (
        info_json["pricing"]["cents_per_output_token"]
        * 1_000_000
        / 100
        * (1.0 - discount)
    )

    print(f"Price input = {fmt(input_price)}, output = {fmt(output_price)}")

    cached_rate = info_json["pricing"]["rate_per_input_token_cached"]
    if cached_rate:
        input_cached = input_price * cached_rate
        print(f"Price input cached = {fmt(input_cached)}")

    cache_write = info_json["pricing"]["rate_per_explicit_cache_write_token"]
    if cache_write:
        cache_write_price = input_price * cache_write
        print(f"Price cache write = {fmt(cache_write_price)}")

    max_tokens = info_json["max_tokens"]
    max_output_tokens = info_json["max_output_tokens"]

    print(f"max_tokens = {max_tokens}, max_output_tokens = {max_output_tokens}\n")
