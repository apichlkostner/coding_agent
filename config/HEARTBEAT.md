Check the current weather at Berlin using the web_search tool.
- Read the previous condition with read_memory("last_weather").
- Normalize the current condition to a single lowercase word
  (e.g. "sunny", "cloudy", "rainy", "snowy", "foggy").
- If the normalized condition differs from the previous one, send me
  "Weather changed from <previous> to <current>, current temperature <temperature>°C" via the configured output channel.
- Always store_memory("last_weather", "<normalized condition>") at the end.