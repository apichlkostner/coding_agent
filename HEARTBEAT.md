Check the weather at Berlin using the prompt_async tool. 
- If it's cloudy AND read_memory("last_weather") != "cloudy", 
  send me "Weather changed to cloudy" via the configured output channel.
- Always store_memory("last_weather", "<current condition>") at the end.