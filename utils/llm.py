from langchain.chat_models.base import init_chat_model
from dotenv import load_dotenv

load_dotenv()


def get_llm(temperature: float = 0.3):
    return init_chat_model(
        model="qwen3-max",
        model_provider="openai",
        temperature=temperature,
    )
