import os
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from react_agent.graph import create_react_agent

@tool
def get_weather(location: str):
    """Get the weather for a location."""
    return f"The weather in {location} is sunny."

# Initialize the language model routing through litellm proxy
model = ChatOpenAI(
    model=os.getenv("LITELLM_MODEL", "google/gemma-4-26b-a4b-it"),
    api_key=os.getenv("LITELLM_PROXY_API_KEY"),
    base_url=os.getenv("LITELLM_PROXY_API_BASE")
)

# Create the agent using the provided React agent framework
graph = create_react_agent(
    model=model,
    tools=[get_weather],
    system_prompt="You are a helpful assistant that can check the weather."
)
