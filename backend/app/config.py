from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    llm_api_key: str = ''
    llm_base_url: str = 'https://api.deepseek.com/v1'
    llm_model: str = 'deepseek-chat'
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1200
    embedding_backend: str = 'bge'
    embedding_model: str = 'BAAI/bge-small-zh-v1.5'
    embedding_device: str = 'cpu'
    similarity_threshold: float = 0.35
    bm25_threshold: float = 1.5
    lexical_coverage_threshold: float = 0.5
    retrieve_top_k: int = 6
    allow_extractive: bool = True
    books_dir: Path = ROOT / 'app/data/books'
    chroma_dir: Path = ROOT / 'app/data/chroma'
    db_path: Path = ROOT / 'app/data/medrag.db'
    model_cache_dir: Path = ROOT / 'app/data/models'


settings = Settings()
